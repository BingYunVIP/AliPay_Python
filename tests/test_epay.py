import base64
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from werkzeug.datastructures import MultiDict

from bypay.epay_protocol import canonical, public_pem, signed, verify
from bypay.alipay_client import AlipayClient
from web.app import create_app
from support import open_gates
from web.webhooks import deliver_one


def secret_pem(key):return key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()


class EpayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.provider_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.merchant_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        dist=self.root/'dist';dist.mkdir();(dist/'index.html').write_text('<html>cashier</html>')
        self.app=create_app({'TESTING':True,'PUBLIC_BASE_URL':'https://pay.example.test','DATABASE':str(self.root/'pay.sqlite3'),'FRONTEND_DIST':str(dist),'ALLOW_REGISTRATION':'true','BYPAY_EMAIL_DOMAINS':'example.test',
            'BYPAY_ADMIN_EMAIL':'admin@example.test','BYPAY_ADMIN_PASSWORD':'test-password-123','ALLOW_REGISTRATION':'true',
            'ALIPAY_ENV':'sandbox','ALIPAY_APP_ID':'2026000000000000','ALIPAY_SELLER_ID':'2088000000000000','ALIPAY_PRIVATE_KEY':secret_pem(self.app_key),'ALIPAY_PUBLIC_KEY':public_pem(self.provider_key),'BYPAY_CALLBACK_HOSTS':'merchant.example'})
        open_gates(self.app)
        self.client=self.app.test_client();self.store=self.app.extensions['store'];self.merchants=self.app.extensions['merchants']
        user=self.store.register('merchant@example.test','test-password-123');self.user=user['id']
        self.profile=self.merchants.update(self.user,{'merchant_public_key':public_pem(self.merchant_key)})
        self.account=self.merchants.account(self.profile['merchant_id'])
        guard=patch('socket.create_connection',side_effect=AssertionError('Unexpected network'));guard.start();self.addCleanup(guard.stop)

    def fields(self,**changes):return {'pid':str(self.profile['merchant_id']),'type':'alipay','out_trade_no':'ORDER_SHARED_01','name':'商户 商品 & +','money':'0.01','notify_url':'https://merchant.example/notify','return_url':'https://merchant.example/return','clientip':'127.0.0.1',**changes}

    def create(self,version='v1',**changes):
        return self.client.post('/mapi.php' if version=='v1' else '/api/pay/create',data=signed(self.fields(**changes),self.profile['md5_key'] if version=='v1' else self.merchant_key,'MD5' if version=='v1' else 'RSA'))

    def paid(self,number):
        data={'app_id':'2026000000000000','seller_id':'2088000000000000','out_trade_no':number,'total_amount':'0.01','trade_no':'20261016000000'+number[-8:],'trade_status':'TRADE_SUCCESS','sign_type':'RSA2'}
        data['sign']=base64.b64encode(self.provider_key.sign(AlipayClient.build_sign_content(data).encode(),padding.PKCS1v15(),hashes.SHA256())).decode()
        self.assertEqual(self.client.post('/payments/notify',data=data).data,b'success')

    def test_registration_gets_unique_merchant_identity_and_rsa_private_is_never_stored(self):
        response=self.client.post('/api/auth/register',json={'email':'new@example.test','password':'Test-password-123'})
        self.assertEqual(response.status_code,200)
        info=self.client.get('/api/merchant/config').json
        self.assertGreaterEqual(info['merchant_id'],1000);self.assertEqual(len(info['md5_key']),32)
        self.assertNotIn('merchant_private_key',info)
        self.assertEqual(self.client.get('/api/merchant/config?merchant_id='+str(self.profile['merchant_id'])).status_code,404)
        self.assertNotEqual(info['merchant_id'],self.profile['merchant_id'])
        with self.store.connect() as db:
            row=db.execute('SELECT md5_key FROM merchant_accounts WHERE user_id=?',(self.user,)).fetchone()
            self.assertNotIn(self.profile['md5_key'].encode(),row['md5_key'])

    def test_v1_create_and_query_use_epay_field_names_and_cashier_url(self):
        response=self.create();self.assertEqual(response.json['code'],1)
        self.assertTrue(response.json['payurl'].startswith('https://pay.example.test/cashier/'))
        info=self.client.post('/api.php',data={'act':'order','pid':self.profile['merchant_id'],'key':self.profile['md5_key'],'out_trade_no':'ORDER_SHARED_01'}).json
        self.assertEqual(info['code'],1);self.assertEqual(info['money'],'0.01');self.assertEqual(info['status'],0);self.assertEqual(info['trade_no'],response.json['trade_no'])
        with self.store.connect() as db:db.execute('UPDATE orders SET expires_at=0')
        expired=self.client.post('/api.php',data={'act':'order','pid':self.profile['merchant_id'],'key':self.profile['md5_key'],'out_trade_no':'ORDER_SHARED_01'}).json
        self.assertEqual(expired['state'],'EXPIRED')

    def test_v2_signs_success_responses_and_checks_timestamp_and_merchant_public_key(self):
        response=self.create('v2');self.assertEqual(response.json['code'],0)
        self.assertTrue(verify(response.json,self.profile['platform_public_key'],'RSA',timestamp=True))
        self.assertEqual(response.json['pay_type'],'jump')
        invalid=self.client.post('/api/pay/create',data=signed(self.fields(),self.merchant_key,'RSA',timestamp=int(time.time())-301))
        self.assertEqual(invalid.json['code'],-3)
        info=self.client.post('/api/merchant/info',data=signed({'pid':self.profile['merchant_id']},self.merchant_key,'RSA')).json
        self.assertEqual(info['pid'],self.profile['merchant_id']);self.assertTrue(verify(info,self.profile['platform_public_key'],'RSA',timestamp=True))

    def test_same_merchant_order_is_idempotent_and_different_merchants_can_reuse_number(self):
        first=self.create().json
        self.assertEqual(self.create().json,first)
        self.assertEqual(self.create(money='0.02').json['error'],'order_conflict')
        other=self.store.register('other@example.test','Test-password-123');profile=self.merchants.describe(other['id'])
        params=self.fields(pid=str(profile['merchant_id']))
        second=self.client.post('/mapi.php',data=signed(params,profile['md5_key'])).json
        self.assertEqual(second['code'],1);self.assertNotEqual(second['trade_no'],first['trade_no'])
        denied=self.client.post('/api.php',data={'act':'order','pid':profile['merchant_id'],'key':profile['md5_key'],'trade_no':first['trade_no']}).json
        self.assertEqual(denied['error'],'order_not_found')

    def test_failed_epay_creation_replay_rechecks_quota(self):
        settings=self.merchants.payment_settings(self.user)
        payload={**settings['settings'],'daily_limit':'0.01','version':settings['version']}
        self.merchants.save_payment_settings(self.user,payload,'3000.00',self.app.extensions['site_settings'].read()['order_timeout_minutes'])
        failed_fields=self.fields(out_trade_no='FAILED_ORDER_001')
        with patch.object(self.store,'create_order',side_effect=RuntimeError('temporary failure')):
            with self.assertRaises(RuntimeError):
                self.client.post('/mapi.php',data=signed(failed_fields,self.profile['md5_key']))
        with self.store.connect() as db:failed=dict(db.execute('SELECT * FROM merchant_orders WHERE user_id=? AND merchant_no=?',(self.user,'FAILED_ORDER_001')).fetchone())
        self.assertEqual(failed['reservation_state'],'failed')
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM merchant_quota_reservations WHERE number=?',(failed['platform_no'],)).fetchone())
        accepted=self.client.post('/mapi.php',data=signed(self.fields(out_trade_no='PAID_ORDER_002'),self.profile['md5_key']))
        self.assertEqual(accepted.json['code'],1)
        with self.store.connect() as db:db.execute('UPDATE orders SET paid_at=? WHERE out_trade_no=?',(int(time.time()),accepted.json['trade_no']))
        retry=self.client.post('/mapi.php',data=signed(failed_fields,self.profile['md5_key']))
        self.assertEqual(retry.json['error'],'merchant_daily_limit_exceeded')
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM merchant_quota_reservations WHERE number=?',(failed['platform_no'],)).fetchone())

    def test_rsa_only_mode_and_duplicate_parameters_do_not_allow_signature_bypass(self):
        params=signed(self.fields(),self.profile['md5_key'])
        params['money']='9.99';self.assertEqual(self.client.post('/mapi.php',data=params).json['code'],-3)
        duplicated=MultiDict(list(signed(self.fields(),self.profile['md5_key']).items())+[('pid','1000')])
        self.assertEqual(self.client.post('/mapi.php',data=duplicated).json['error'],'invalid_parameters')
        self.merchants.update(self.user,{'signature_mode':'rsa'})
        self.assertEqual(self.create().json['error'],'rsa_required')
        self.assertEqual(self.create('v2').json['code'],0)

    def test_cashier_is_order_scoped_embeddable_only_by_merchant_and_admin_stays_denied(self):
        response=self.create();url=response.json['payurl'];path=urlsplit(url).path;token=path.rsplit('/',1)[-1]
        with self.client.get(path) as page:
            self.assertEqual(page.status_code,200);self.assertNotIn('X-Frame-Options',page.headers)
            self.assertIn("frame-ancestors 'self' https://merchant.example",page.headers['Content-Security-Policy'])
        with self.client.get('/login') as page:self.assertEqual(page.headers['X-Frame-Options'],'DENY')
        state=self.client.get('/api/cashier/'+token).json
        self.assertEqual(state['out_trade_no'],'ORDER_SHARED_01');self.assertNotIn('md5_key',state)
        self.assertEqual(state['layout'], {'cashier_ratio':'9:16','cashier_width':392})
        self.assertEqual(self.client.get('/api/cashier/bad-token').status_code,404)
        with patch('web.cashier.fetch_cashier_qr',return_value='https://qr.alipay.com/fixture'):
            self.assertEqual(self.client.post('/api/cashier/'+token+'/prepare',json={},headers={'Origin':'https://pay.example.test'}).status_code,200)
        self.assertIn(b'<svg',self.client.get('/api/cashier/'+token+'/qr.svg').data)
        self.paid(response.json['trade_no'])
        self.assertTrue(self.client.get('/api/cashier/'+token).json['paid'])
        self.assertEqual(self.client.get('/api/cashier/'+token+'/qr.svg').status_code,409)

    def test_v1_callback_keeps_original_key_after_rotation_and_only_paid_orders_notify(self):
        number=self.create().json['trade_no'];self.paid(number)
        self.merchants.update(self.user,{'rotate_md5':True})
        captured=[]
        def receive(url,body,headers,allowed):captured.append(dict((k,v[0]) for k,v in parse_qs(body.decode()).items()));return True,200
        deliver_one(self.store,self.app.extensions['webhook_signer'],self.app.extensions['trace'],{'merchant.example'},receive)
        self.assertEqual(captured[0]['out_trade_no'],'ORDER_SHARED_01');self.assertEqual(captured[0]['trade_no'],number)
        self.assertTrue(verify(captured[0],self.profile['md5_key']))
        self.assertFalse(deliver_one(self.store,self.app.extensions['webhook_signer'],self.app.extensions['trace'],{'merchant.example'},receive))

    def test_v2_callback_is_rsa_signed_with_platform_key(self):
        number=self.create('v2').json['trade_no'];self.paid(number)
        notification=self.merchants.notification(number)
        self.assertEqual(notification['sign_type'],'RSA');self.assertTrue(verify(notification,self.profile['platform_public_key'],'RSA',timestamp=True))
        self.assertEqual(notification['money'],'0.01')

    def test_localhost_and_loopback_use_the_actual_validated_local_interface(self):
        local=create_app({**self.app.config,'PUBLIC_BASE_URL':'http://localhost:888'})
        client=local.test_client()
        params=self.fields(notify_url='http://127.0.0.1:8100/notify',return_url='http://127.0.0.1:8100/return')
        response=client.post('/mapi.php',data=signed(params,self.profile['md5_key']),base_url='http://127.0.0.1:888')
        self.assertTrue(response.json['payurl'].startswith('http://127.0.0.1:888/cashier/'))
        token=response.json['payurl'].rsplit('/',1)[-1]
        with client.get('/cashier/'+token,base_url='http://127.0.0.1:888') as page:
            self.assertIn('http://localhost:8100',page.headers['Content-Security-Policy'])
            self.assertIn('http://127.0.0.1:8100',page.headers['Content-Security-Policy'])
        with patch('web.cashier.fetch_cashier_qr',return_value='https://qr.alipay.com/local-fixture'):
            self.assertEqual(client.post('/api/cashier/'+token+'/prepare',json={},base_url='http://127.0.0.1:888',headers={'Origin':'http://127.0.0.1:888'}).status_code,200)
        other=client.post('/mapi.php',data=signed(self.fields(out_trade_no='OTHER_ALIAS_ORDER'),self.profile['md5_key']),base_url='http://evil.example:888')
        self.assertTrue(other.json['payurl'].startswith('http://localhost:888/cashier/'))

    def test_merchant_key_file_loss_is_not_silently_replaced(self):
        from web.merchants import Merchants
        (self.root/'merchant-master.key').unlink()
        with self.assertRaisesRegex(ValueError,'restore_backup'):Merchants(self.store,self.app.extensions['site_settings'])


class PhpOracleTests(unittest.TestCase):
    def test_php_epay_md5_and_rsa_interoperate(self):
        source=Path('/root/ByPay/Epay/includes/lib/Payment.php')
        if not shutil.which('php') or not source.exists():self.skipTest('Local Epay PHP reference unavailable')
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        fields={'pid':'1000','name':'商品 & + 中文','money':'0.01','zero':'0','space':' ','sign_type':'RSA','timestamp':str(int(time.time()))}
        payload={'file':str(source),'fields':fields,'python':signed(fields,key,'RSA'),'private':secret_pem(key),'public':public_pem(key)}
        php='''$v=json_decode(stream_get_contents(STDIN),true);function isEmpty($v){return $v===null||trim($v)==='';}function base64ToPem($v,$label){if(strpos($v,'-----BEGIN')===0)return $v;return "-----BEGIN $label-----\\n".chunk_split($v,64,"\\n")."-----END $label-----\\n";}require $v['file'];$conf=['private_key'=>$v['private']];$rsa=\\lib\\Payment::makeSign($v['fields'],'');$ok=\\lib\\Payment::verifySign($v['python'],'',$v['public']);$v['fields']['sign_type']='MD5';echo json_encode(['rsa'=>$rsa,'ok'=>$ok,'md5'=>\\lib\\Payment::makeSign($v['fields'],'testmd5secret0123456789')]);'''
        result=subprocess.run(['php','-r',php],input=json.dumps(payload),capture_output=True,text=True,check=True)
        oracle=json.loads(result.stdout)
        self.assertTrue(oracle['ok'])
        self.assertTrue(verify({**fields,'sign':oracle['rsa']},public_pem(key),'RSA',timestamp=True))
        self.assertEqual(oracle['md5'],signed({**fields,'sign_type':'MD5'},'testmd5secret0123456789')['sign'])


if __name__=='__main__':unittest.main()
