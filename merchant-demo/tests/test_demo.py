import base64
import time
import json
import sqlite3
import unittest
from contextlib import closing
from fixtures import MerchantFixture


class MerchantTests(MerchantFixture,unittest.TestCase):
    def setUp(self):self.setup_merchant()

    def test_anonymous_page_and_same_origin_protection(self):
        with self.client.get('/') as response:self.assertEqual(response.status_code,200);self.assertNotIn('WWW-Authenticate',response.headers)
        self.assertEqual(self.client.post('/api/orders',json={'amount':'0.01'},headers={**self.headers,'Origin':'https://evil.test'}).status_code,403)

    def test_v1_and_v2_create_embeddable_platform_cashier(self):
        for version in ('v1','v2'):
            with self.subTest(version=version):
                order=self.create(version)
                self.assertTrue(order['pay_url'].startswith('https://pay.bingyun.vip/cashier/'))
                self.assertEqual(order['merchant_id'],'1000');self.assertEqual(order['api_version'],version)
                with self.client.get('/') as page:self.assertIn('frame-src https://pay.bingyun.vip',page.headers['Content-Security-Policy'])

    def test_signed_callbacks_are_idempotent_for_both_versions(self):
        for version in ('v1','v2'):
            with self.subTest(version=version):
                # Use an isolated fixture per protocol to avoid the fixed mock platform order ID colliding
                if version=='v2':
                    with closing(sqlite3.connect(self.settings['DATABASE'])) as db,db:db.execute('DELETE FROM receipts')
                order=self.create(version);params=self.callback(order)
                self.assertEqual(self.client.get('/notify',query_string=params).data,b'success')
                self.assertEqual(self.client.get('/notify',query_string=params).data,b'success')
                state=self.client.get('/api/state').json
                self.assertEqual(next(o for o in state['orders'] if o['number']==order['number'])['status'],'PAID')

    def test_forged_wrong_merchant_and_wrong_amount_never_mark_paid(self):
        order=self.create('v2')
        for changes in ({'money':'0.02'},{'pid':'1001'},{'type':'wxpay'},{'trade_no':'OTHER_ORDER_1234'},{'trade_status':'WAIT_BUYER_PAY'}):
            self.assertEqual(self.client.get('/notify',query_string=self.callback(order,**changes)).status_code,400)
        bad=self.callback(order);bad['sign']='bad'
        self.assertEqual(self.client.get('/notify',query_string=bad).status_code,400)
        self.assertEqual(self.client.get('/api/state').json['orders'][0]['status'],'WAITING')

    def test_query_confirms_payment_only_when_signature_identity_and_amount_match(self):
        order=self.create('v2');self.paid=True;self.wrong_amount=True
        self.assertEqual(self.post('/api/orders/'+order['number']+'/sync',{}).status_code,502)
        self.assertEqual(self.client.get('/api/state').json['orders'][0]['status'],'WAITING')
        with closing(sqlite3.connect(self.settings['DATABASE'])) as db,db:db.execute('UPDATE orders SET last_query_at=0')
        self.wrong_amount=False
        result=self.post('/api/orders/'+order['number']+'/sync',{})
        self.assertEqual(result.json['order']['status'],'PAID');self.assertEqual(result.json['order']['confirmed_by'],'query')

    def test_key_change_does_not_break_callback_for_old_order_and_snapshots_are_encrypted(self):
        order=self.create();self.accepted_md5='b'*32
        self.assertEqual(self.post('/api/config',{'md5_key':self.accepted_md5}).status_code,200)
        self.assertEqual(self.client.get('/notify',query_string=self.callback(order)).data,b'success')
        with closing(sqlite3.connect(self.settings['DATABASE'])) as db:raw=db.execute('SELECT connection_enc FROM orders').fetchone()[0]
        self.assertNotIn(self.md5,raw);self.assertNotIn(self.private,raw)
        body=self.client.get('/api/state').get_data(as_text=True)
        self.assertNotIn(self.md5,body);self.assertNotIn(self.private,body)
        self.assertNotIn(self.md5,(self.root/'logs/demo.jsonl').read_text())

    def test_browser_return_does_not_confirm_payment(self):
        self.create()
        with self.client.get('/return?trade_status=TRADE_SUCCESS') as response:self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/state').json['orders'][0]['status'],'WAITING')


    def test_existing_ed25519_orders_can_still_receive_old_callbacks(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key=Ed25519PrivateKey.generate();path=self.root/'legacy-public.pem'
        path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
        self.app.config['PAY_PUBLIC_KEY_FILE']=str(path)
        with closing(sqlite3.connect(self.settings['DATABASE'])) as db,db:
            db.execute("INSERT INTO orders(number,amount,status,created_at) VALUES ('LEGACY_ORDER_01','0.01','WAITING',?)",(int(time.time()),))
        data={'id':'legacy_event_01','event':'payment.paid','out_trade_no':'LEGACY_ORDER_01','trade_no':'202610160000999999','amount':'0.01','currency':'CNY','channel':'alipay','status':'PAID','paid_at':int(time.time())}
        raw=json.dumps(data).encode();stamp=str(int(time.time()))
        headers={'X-ByPay-Timestamp':stamp,'X-ByPay-Event-Id':data['id'],'X-ByPay-Signature':base64.b64encode(key.sign(stamp.encode()+b'.'+raw)).decode()}
        self.assertEqual(self.client.post('/notify',data=raw,content_type='application/json',headers=headers).data,b'success')
        self.assertEqual(self.client.post('/notify',json=[],headers=headers).status_code,400)

    def test_lost_snapshot_key_is_not_silently_regenerated(self):
        self.create()
        key=self.root/'connection-master.key';key.unlink()
        with self.assertRaisesRegex(ValueError,'restore_backup'):self.config.seal(self.config.read())
        self.assertFalse(key.exists())


if __name__=='__main__':unittest.main()
