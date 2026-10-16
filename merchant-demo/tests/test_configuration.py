import base64
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import dotenv_values, set_key
from app import create_app


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / '.env'
        self.source.touch(mode=0o600)
        self.old_key = 'byp_' + 'a' * 43
        self.new_key = 'byp_' + 'b' * 43
        self.signer = Ed25519PrivateKey.generate()
        self.pem = self.signer.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        (self.root/'public.pem').write_text(self.pem)
        for k,v in {'PAY_ORIGIN':'http://127.0.0.1:888','DEMO_ORIGIN':'http://127.0.0.1:8100','PAY_API_KEY':self.old_key,'UNRELATED':'preserve-me'}.items():
            set_key(self.source,k,v)
        self.settings = {'TESTING':True, 'CONFIG_FILE':str(self.source), 'PAY_ORIGIN':'http://127.0.0.1:888','DEMO_ORIGIN':'http://127.0.0.1:8100', 'DEMO_PORT':'8100',
                         'DATABASE':str(self.root/'demo.sqlite3'),'PAY_PUBLIC_KEY_FILE':str(self.root/'public.pem')}
        self.app = create_app(self.settings)
        self.client = self.app.test_client()
        self.headers = {'Origin':'http://127.0.0.1:8100','X-Demo-Request':'1'}
        self.calls = []
        self.accepted = {self.old_key,self.new_key}
        self.public_error = False
        real_client=httpx.Client
        def upstream(request):
            self.calls.append(request)
            if request.url.path == '/api/webhooks/public-key':
                return httpx.Response(503,json={'error':'temporarily_unavailable'}) if self.public_error else httpx.Response(200,json={'algorithm':'Ed25519','public_key':self.pem})
            if request.headers.get('Authorization','').removeprefix('Bearer ') not in self.accepted:
                return httpx.Response(401,json={'error':'unauthorized'})
            if request.method=='GET' and request.url.path.startswith('/api/v1/payments/DEMO_AUTH_'):
                return httpx.Response(404,json={'error':'order_not_found'})
            if request.method=='POST' and request.url.path=='/api/v1/payments':
                return httpx.Response(201,json={'order':{'pay_url':'https://openapi.alipay.com/gateway.do?sign=test','status':'CREATED','amount':'0.01'}})
            raise AssertionError('Unexpected upstream request: '+str(request.url))
        transport=httpx.MockTransport(upstream)
        mocked=patch('pay_http.httpx.Client',side_effect=lambda **kw: real_client(transport=transport,**kw));mocked.start();self.addCleanup(mocked.stop)

    def post(self,path,payload,**kwargs):
        return self.client.post(path,json=payload,headers=self.headers,base_url='http://127.0.0.1:8100',**kwargs)

    def test_local_browser_can_configure_without_login_but_no_secret_is_returned(self):
        response=self.client.get('/api/config',base_url='http://127.0.0.1:8100')
        self.assertTrue(response.json['can_manage'])
        self.assertEqual(response.json['key_status'],'unverified')
        self.assertNotIn(self.old_key,response.get_data(as_text=True))
        self.assertNotIn('WWW-Authenticate',response.headers)

    def test_validation_is_read_only_against_pay_and_does_not_create_orders(self):
        response=self.post('/api/config/check',{})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json['verified']);self.assertFalse(response.json['saved'])
        self.assertEqual([r.method for r in self.calls],['GET','GET'])
        with closing(sqlite3.connect(self.settings['DATABASE'])) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM orders').fetchone()[0],0)
        self.assertEqual(self.client.get('/api/state').json['key_status'],'valid')

    def test_invalid_replacement_never_overwrites_existing_config(self):
        before=self.source.read_bytes()
        response=self.post('/api/config',{'api_key':'byp_'+'x'*43})
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json['error'],'unauthorized')
        self.assertEqual(self.source.read_bytes(),before)
        self.assertFalse(response.json['saved'])

    def test_save_is_live_for_other_app_instances_and_persists_on_restart(self):
        second=create_app(self.settings).test_client()
        response=self.post('/api/config',{'api_key':self.new_key})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.source.stat().st_mode & 0o777,0o600)
        self.assertEqual(dotenv_values(self.source)['UNRELATED'],'preserve-me')
        self.assertEqual(dotenv_values(self.source)['PAY_API_KEY'],self.new_key)
        for client in (self.client,second,create_app(self.settings).test_client()):
            response=client.post('/api/orders',json={'amount':'0.01'},headers=self.headers)
            self.assertEqual(response.status_code,201)
            self.assertEqual(self.calls[-1].headers['Authorization'],'Bearer '+self.new_key)
            self.assertEqual(json.loads(self.calls[-1].content)['notify_url'],'http://127.0.0.1:8100/notify')

    def test_blank_key_preserves_the_current_key(self):
        self.assertEqual(self.post('/api/config',{'api_key':''}).status_code,200)
        self.assertEqual(dotenv_values(self.source)['PAY_API_KEY'],self.old_key)

    def test_wrong_service_address_and_missing_public_key_do_not_destroy_config(self):
        before=self.source.read_bytes()
        self.assertEqual(self.post('/api/config',{'pay_origin':'http://127.0.0.1:888/dashboard'}).status_code,400)
        self.public_error=True
        response=self.post('/api/config',{'api_key':self.new_key})
        self.assertEqual(response.json['error'],'public_key_unavailable')
        self.assertEqual(self.source.read_bytes(),before)

    def test_key_revoked_after_validation_is_reported_and_logged_without_secret(self):
        self.post('/api/config/check',{})
        self.accepted.clear()
        response=self.post('/api/orders',{'amount':'0.01'})
        self.assertEqual(response.status_code,502)
        self.assertEqual(response.json['error'],'unauthorized')
        state=self.client.get('/api/state')
        self.assertEqual(state.json['key_status'],'invalid')
        for value in (state.get_data(as_text=True),(self.root/'logs/demo.jsonl').read_text()):
            self.assertNotIn(self.old_key,value)
            self.assertNotIn(self.new_key,value)

    def test_wsl_private_peer_is_supported_and_external_or_forwarded_edit_is_rejected(self):
        self.assertEqual(self.post('/api/config/check',{},environ_overrides={'REMOTE_ADDR':'172.20.10.1'}).status_code,200)
        self.assertEqual(self.post('/api/config',{},environ_overrides={'REMOTE_ADDR':'8.8.8.8'}).status_code,403)
        self.assertEqual(self.client.post('/api/config',json={},headers={**self.headers,'X-Forwarded-For':'8.8.8.8'}).status_code,403)
        self.assertEqual(self.client.post('/api/config',json={},headers={**self.headers,'Origin':'http://evil.example'}).status_code,403)

    def test_previous_orders_keep_their_verification_key_after_changing_connection(self):
        self.post('/api/orders',{'amount':'0.01'})
        number=self.client.get('/api/state').json['orders'][0]['number']
        old_signer=self.signer
        self.signer=Ed25519PrivateKey.generate()
        self.pem=self.signer.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.assertEqual(self.post('/api/config',{'api_key':self.new_key}).status_code,200)
        event={'id':'test_previous_connection','event':'payment.paid','out_trade_no':number,'trade_no':'LOCAL_TRADE_123456','amount':'0.01','currency':'CNY','channel':'alipay','status':'PAID','paid_at':int(time.time())}
        raw=json.dumps(event).encode();stamp=str(int(time.time()))
        headers={'X-ByPay-Timestamp':stamp,'X-ByPay-Event-Id':event['id'],'X-ByPay-Signature':base64.b64encode(old_signer.sign(stamp.encode()+b'.'+raw)).decode()}
        self.assertEqual(self.client.post('/notify',data=raw,content_type='application/json',headers=headers).data,b'success')

    def test_browser_save_failure_keeps_previous_values(self):
        before=self.source.read_bytes()
        with patch('configuration.os.replace',side_effect=OSError('disk full')):
            response=self.post('/api/config',{'api_key':self.new_key})
        self.assertEqual(response.status_code,500)
        self.assertEqual(response.json['error'],'config_write_failed')
        self.assertEqual(self.source.read_bytes(),before)


if __name__=='__main__':unittest.main()
