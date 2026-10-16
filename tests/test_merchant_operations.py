import base64
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
import test_payments
from web.cashier import allowed_url, extract_cashier_qr

TRANSPORT='alipay.aop.api.DefaultAlipayClient.do_post'


class MerchantOperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): test_payments.PaymentTests.setUpClass.__func__(cls)
    setUp=test_payments.PaymentTests.setUp
    create=test_payments.PaymentTests.create
    notify=test_payments.PaymentTests.notify
    notification=test_payments.PaymentTests.notification

    def admin(self):
        if hasattr(self,'admin_session'): return self.admin_session
        client=self.app.test_client()
        result=client.post('/api/auth/login',json={'email':'admin@example.test','password':'Test-admin-password-123'}).json
        self.admin_session=client,{'X-CSRF-Token':result['csrf_token'],'Origin':'https://pay.example.test'}
        return self.admin_session

    def wire(self,method='refund',**fields):
        data={'code':'10000','out_trade_no':'TEST_ORDER_0001','trade_no':'20261015000000012345','refund_fee':'0.04',**fields}
        body=json.dumps(data,separators=(',',':')).encode()
        sign=base64.b64encode(self.alipay_key.sign(body,padding.PKCS1v15(),hashes.SHA256()))
        return b'{"alipay_trade_'+method.encode()+b'_response":'+body+b',"sign":"'+sign+b'"}'

    def paid(self):
        self.create(amount='0.10',notify_url='https://test.bingyun.vip/notify')
        self.assertEqual(self.notify(self.notification(total_amount='0.10')).data,b'success')

    def refund(self,amount='0.04',refund_no='REFUND_REQUEST_001'):
        client,headers=self.admin()
        return client.post('/api/v1/payments/TEST_ORDER_0001/refund',headers=headers,json={'refund_no':refund_no,'amount':amount,'reason':'测试退款'})

    def test_multiple_configurations_are_encrypted_isolated_and_bound_to_orders(self):
        self.create()
        client,headers=self.admin()
        result=client.post('/api/admin/channels',headers=headers,json={'channel':'alipay','name':'第二个支付宝','fields':{'env':'sandbox','app_id':'2026000000000009','seller_id':'2088000000000000','app_private_key':self.private,'alipay_public_key':self.public}})
        self.assertEqual(result.status_code,201)
        second=result.json['channel']
        new=self.create(out_trade_no='TEST_ORDER_0002',config_id=second['config_id']).json['order']
        self.assertEqual(parse_qs(urlsplit(new['pay_url']).query)['app_id'],['2026000000000009'])
        self.assertNotEqual(self.store.order('TEST_ORDER_0001')['payment_config_id'],new['payment_config_id'])
        self.assertNotIn(self.private,json.dumps(client.get('/api/admin/channels').json))
        self.assertNotIn(self.private.encode(),self.store.path.read_bytes())
        self.assertEqual(client.put('/api/admin/channel-configs/'+str(second['config_id']),headers=headers,json={'fields':{},'version':second['version'],'revision':second['revision'],'enabled':False}).status_code,200)
        self.assertEqual(self.create(out_trade_no='TEST_ORDER_0003',config_id=second['config_id']).status_code,403)
        self.assertEqual(self.notify(self.notification(out_trade_no='TEST_ORDER_0002',app_id='2026000000000009',trade_no='20261015000000067890')).data,b'success')
        self.assertEqual(self.notify(self.notification()).data,b'success')

    def test_all_four_types_allow_multiple_configs_and_regular_users_cannot_edit(self):
        client,headers=self.admin()
        for channel in ('alipay','wechat','paypal','stripe'):
            for index in range(2):
                response=client.post('/api/admin/channels',headers=headers,json={'channel':channel,'name':channel+str(index),'fields':{},'enabled':False})
                self.assertEqual(response.status_code,201)
                self.assertFalse(response.json['channel']['enabled'])
        self.assertEqual(len(client.get('/api/admin/channels').json['channels']),12)
        self.assertEqual(self.client.post('/api/admin/channels',headers=self.headers,json={'channel':'alipay','name':'bad','fields':{}}).status_code,403)
        self.assertEqual(client.post('/api/admin/channels',json={'channel':'alipay','name':'bad','fields':{}}).status_code,403)

    def test_channel_filters_expiry_archive_and_restore_preserve_accounting(self):
        self.paid()
        self.assertEqual(self.client.get('/api/console/orders?channel=alipay').json['total'],1)
        self.assertEqual(self.client.get('/api/console/orders?channel=wechat').json['total'],0)
        self.assertEqual(self.client.delete('/api/v1/payments/TEST_ORDER_0001',headers=self.headers).status_code,200)
        self.assertEqual(self.client.get('/api/console/orders').json['total'],0)
        self.assertEqual(self.client.get('/api/console/orders?deleted=1').json['total'],1)
        self.assertEqual(self.client.get('/api/console/overview').json['revenue_cents'],10)
        self.assertEqual(self.store.order('TEST_ORDER_0001')['status'],'PAID')
        self.assertEqual(self.client.post('/api/v1/payments/TEST_ORDER_0001/restore',headers=self.headers).status_code,200)
        self.create(out_trade_no='TEST_ORDER_EXPIRED')
        with self.store.connect() as db: db.execute("UPDATE orders SET expires_at=1 WHERE out_trade_no='TEST_ORDER_EXPIRED'")
        self.assertEqual(self.client.get('/api/console/orders?status=EXPIRED').json['total'],1)
        self.assertEqual(self.client.get('/api/console/orders?status=CREATED').json['total'],0)

    def test_verified_partial_then_full_refund_is_idempotent_and_not_downgraded(self):
        self.paid()
        with patch(TRANSPORT,return_value=self.wire()) as send:
            first=self.refund()
            repeated=self.refund()
            send.assert_called_once()
            content=json.loads(send.call_args.args[3]['biz_content'])
            self.assertEqual(content['out_request_no'],'REFUND_REQUEST_001')
        self.assertEqual(first.json['order']['status'],'PARTIALLY_REFUNDED')
        self.assertEqual(first.json['order']['refundable_amount'],'0.06')
        self.assertEqual(repeated.json['order']['refunded_amount'],'0.04')
        self.assertEqual(self.refund(amount='0.07',refund_no='REFUND_REQUEST_002').status_code,409)
        with patch(TRANSPORT,return_value=self.wire(refund_fee='0.06')):
            full=self.refund(amount='0.06',refund_no='REFUND_REQUEST_002')
        self.assertEqual(full.json['order']['status'],'REFUNDED')
        self.assertEqual(self.notify(self.notification(total_amount='0.10')).data,b'success')
        self.assertEqual(self.store.order('TEST_ORDER_0001')['status'],'REFUNDED')
        with self.store.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
        self.assertEqual(self.client.get('/api/console/orders?status=REFUNDED').json['total'],1)

    def test_unpaid_non_admin_and_csrf_refunds_are_rejected(self):
        self.create()
        self.assertEqual(self.refund().status_code,409)
        self.notify(self.notification())
        self.assertEqual(self.client.post('/api/v1/payments/TEST_ORDER_0001/refund',headers=self.headers,json={}).status_code,403)
        client,_=self.admin()
        self.assertEqual(client.post('/api/v1/payments/TEST_ORDER_0001/refund',json={}).status_code,403)

    def test_refund_timeout_reserves_balance_and_queries_without_resending_money(self):
        self.paid()
        with patch(TRANSPORT,side_effect=TimeoutError()):
            pending=self.refund()
        self.assertEqual(pending.status_code,202)
        self.assertEqual(pending.json['order']['status'],'REFUNDING')
        self.assertEqual(self.refund(refund_no='DIFFERENT_REFUND_002').status_code,409)
        with patch(TRANSPORT,return_value=self.wire('fastpay_refund_query',out_request_no='REFUND_REQUEST_001',refund_amount='0.04',refund_status='REFUND_SUCCESS')) as send:
            self.app.extensions['refunds'].sync('REFUND_REQUEST_001')
            self.assertEqual(parse_qs(send.call_args.args[1])['method'],['alipay.trade.fastpay.refund.query'])
        self.assertEqual(self.store.order('TEST_ORDER_0001')['refunded_amount'],'0.04')

    def test_refund_signature_and_amount_mismatch_never_confirm_money(self):
        self.paid()
        for value in (self.wire().replace(b'0.04',b'0.05'), self.wire(refund_fee='0.05')):
            with patch(TRANSPORT,return_value=value): result=self.refund()
            self.assertEqual(result.json['refund']['status'],'UNKNOWN')
            self.assertEqual(result.json['order']['refunded_amount'],'0.00')

    def test_refund_definitive_failure_releases_reservation(self):
        self.paid()
        with patch(TRANSPORT,return_value=self.wire(code='40004',sub_code='ACQ.SELLER_BALANCE_NOT_ENOUGH')):
            result=self.refund()
        self.assertEqual(result.json['refund']['status'],'FAILED')
        self.assertEqual(result.json['order']['status'],'PAID')
        self.assertEqual(result.json['order']['refundable_amount'],'0.10')

    def test_cashier_extractor_uses_epay_qrcode_input_and_rejects_untrusted_urls(self):
        self.assertEqual(allowed_url('https://unitradeprod.alipay.com/appAssign.htm'), 'https://unitradeprod.alipay.com/appAssign.htm')
        self.assertEqual(extract_cashier_qr('<input id="J_qrCode" value="https://qr.alipay.com/real_test" type="hidden" name="qrCode">'),'https://qr.alipay.com/real_test')
        for html in ('<script>qrCode="https://qr.alipay.com/test"</script>', '<input name="qrCode" type="hidden" value="javascript:alert(1)">'):
            with self.assertRaises(ValueError): extract_cashier_qr(html)
        for url in ('http://excashier.alipay.com/pay','https://excashier.alipay.com.evil.test/pay','https://127.0.0.1/pay'):
            with self.assertRaises(ValueError): allowed_url(url)

    def test_native_qr_uses_official_embedded_mode_without_fetching_or_reencoding(self):
        self.create()
        with patch('web.cashier.fetch_cashier_qr') as fetch:
            result=self.client.post('/api/v1/payments/TEST_ORDER_0001/prepare-qr',headers=self.headers,json={'native':True})
            fetch.assert_not_called()
        self.assertEqual(result.status_code,200)
        args=parse_qs(urlsplit(result.json['cashier_url']).query)
        biz=json.loads(args['biz_content'][0])
        self.assertEqual(args['method'],['alipay.trade.page.pay'])
        self.assertEqual(biz['qr_pay_mode'],'4')
        self.assertEqual(biz['qrcode_width'],230)
        self.assertEqual(biz['total_amount'],'0.01')
        self.assertEqual(biz['out_trade_no'],'TEST_ORDER_0001')
        self.assertEqual(self.store.order('TEST_ORDER_0001')['status'],'CREATED')


if __name__=='__main__': unittest.main()
