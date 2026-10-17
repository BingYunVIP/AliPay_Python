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

    def sent_to_pay(self,path):
        """The form fields of the latest request the demo made to one Pay endpoint."""
        from urllib.parse import parse_qs
        request=[r for r in self.requests if r.url.path==path][-1]
        return {k:v[0] for k,v in parse_qs(request.content.decode(),keep_blank_values=True).items()}

    def test_the_chosen_payment_type_goes_to_pay_and_stays_with_the_order(self):
        for version in ('v1','v2'):
            for pay_type in ('alipay','wxpay'):
                with self.subTest(version=version,pay_type=pay_type):
                    order=self.create(version,pay_type)
                    self.assertEqual(order['pay_type'],pay_type)
                    self.assertEqual(self.sent_to_pay('/mapi.php' if version=='v1' else '/api/pay/create')['type'],pay_type)
                    listed=next(o for o in self.client.get('/api/state').json['orders'] if o['number']==order['number'])
                    self.assertEqual(listed['pay_type'],pay_type)
                    log=[l for l in self.client.get('/api/state').json['logs'] if l['stage']=='create.request' and l['number']==order['number']][0]
                    self.assertEqual(log['detail']['type'],pay_type)

    def test_without_a_type_the_order_is_an_alipay_order_and_an_unknown_type_is_refused(self):
        self.assertEqual(self.create()['pay_type'],'alipay')  # an older page that sends no type
        before=len(self.requests)
        for value in ('bank','WXPAY','',None,1,['wxpay'],{'a':1}):
            with self.subTest(value=value):
                response=self.post('/api/orders',{'amount':'0.01','type':value})
                self.assertEqual((response.status_code,response.json['error']),(400,'invalid_pay_type'))
        self.assertEqual(len(self.requests),before)  # nothing reached Pay

    def test_a_notice_must_carry_the_type_its_order_was_created_with(self):
        for version in ('v1','v2'):
            for created,forged in (('wxpay','alipay'),('alipay','wxpay')):
                with self.subTest(version=version,created=created):
                    order=self.create(version,created)
                    self.assertEqual(self.client.get('/notify',query_string=self.callback(order,type=forged)).status_code,400)
                    self.assertEqual(next(o for o in self.client.get('/api/state').json['orders'] if o['number']==order['number'])['status'],'WAITING')
                    self.assertEqual(self.client.get('/notify',query_string=self.callback(order)).data,b'success')
                    paid=next(o for o in self.client.get('/api/state').json['orders'] if o['number']==order['number'])
                    self.assertEqual((paid['status'],paid['pay_type'],paid['confirmed_by']),('PAID',created,'notify'))
                    self.assertEqual(self.client.get('/notify',query_string=self.callback(order)).data,b'success')  # a repeat is acknowledged, not applied twice

    def test_wechat_orders_are_confirmed_by_the_server_side_query_with_the_same_checks(self):
        for version in ('v1','v2'):
            with self.subTest(version=version):
                order=self.create(version,'wxpay');self.paid=True;self.query_type='alipay'
                with closing(sqlite3.connect(self.settings['DATABASE'])) as db,db:db.execute('UPDATE orders SET last_query_at=0')
                self.assertEqual(self.post('/api/orders/'+order['number']+'/sync',{}).status_code,502)  # Pay answered with another type
                self.assertEqual(next(o for o in self.client.get('/api/state').json['orders'] if o['number']==order['number'])['status'],'WAITING')
                self.query_type=None
                with closing(sqlite3.connect(self.settings['DATABASE'])) as db,db:db.execute('UPDATE orders SET last_query_at=0')
                result=self.post('/api/orders/'+order['number']+'/sync',{}).json['order']
                self.assertEqual((result['status'],result['pay_type'],result['confirmed_by']),('PAID','wxpay','query'))
                self.paid=False

    def test_orders_saved_before_payment_types_existed_are_alipay_orders(self):
        database=self.root/'earlier.sqlite3'
        with closing(sqlite3.connect(database)) as db,db:
            db.execute('CREATE TABLE orders(number TEXT PRIMARY KEY,amount TEXT NOT NULL,status TEXT NOT NULL,pay_url TEXT NOT NULL DEFAULT "",created_at INTEGER NOT NULL,trade_no TEXT,paid_at INTEGER)')
            db.execute("INSERT INTO orders(number,amount,status,created_at) VALUES ('EARLIER_ORDER_1','0.01','WAITING',?)",(int(time.time()),))
        from app import create_app
        earlier=create_app({**self.settings,'DATABASE':str(database),'CONFIG_FILE':str(self.env)})
        row=earlier.test_client().get('/api/state').json['orders'][0]
        self.assertEqual((row['number'],row['pay_type']),('EARLIER_ORDER_1','alipay'))

    def test_wechat_problems_reported_by_pay_reach_the_page_as_codes_and_unknown_ones_do_not(self):
        for code in ('wechat_https_required','wechat_app_id_mismatch','wechat_no_auth','payment_not_configured','payment_provider_error'):
            with self.subTest(code=code):
                self.create_error=code
                response=self.post('/api/orders',{'amount':'0.01','type':'wxpay'})
                self.assertEqual((response.status_code,response.json['error']),(502,code))
        for odd in ('internal_stack_trace_detail','wechat_','WECHAT_X','wechat_'+'a'*80,'',12,False,['x']):
            with self.subTest(odd=odd):
                self.create_error=odd
                self.assertEqual(self.post('/api/orders',{'amount':'0.01','type':'wxpay'}).json['error'],'epay_request_failed')
        with closing(sqlite3.connect(self.settings['DATABASE'])) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM orders WHERE status!='CREATE_FAILED'").fetchone()[0],0)  # a refused order is never left waiting

    def test_the_page_offers_alipay_and_wechat_pay_without_inline_styles(self):
        def text(path):
            with self.client.get(path) as response:return response.get_data(as_text=True)
        html=text('/')
        self.assertIn('name="pay-type" value="alipay" checked',html)
        self.assertIn('name="pay-type" value="wxpay"',html)
        self.assertNotIn(' style=',html)  # the page's Content-Security-Policy would refuse inline styles
        script=text('/static/app.js')
        self.assertIn("type:document.querySelector('input[name=pay-type]:checked').value",script)

    def test_lost_snapshot_key_is_not_silently_regenerated(self):
        self.create()
        key=self.root/'connection-master.key';key.unlink()
        with self.assertRaisesRegex(ValueError,'restore_backup'):self.config.seal(self.config.read())
        self.assertFalse(key.exists())


if __name__=='__main__':unittest.main()
