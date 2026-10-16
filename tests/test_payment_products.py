import base64
import json
import unittest
from urllib.parse import parse_qs
from unittest.mock import patch
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

import test_payments
from web.qrcode import payment_qr_svg

TRANSPORT = 'alipay.aop.api.DefaultAlipayClient.do_post'


class ProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): test_payments.PaymentTests.setUpClass.__func__(cls)
    setUp = test_payments.PaymentTests.setUp
    create = test_payments.PaymentTests.create
    notification = test_payments.PaymentTests.notification
    notify = test_payments.PaymentTests.notify

    def wire(self, method='create', **values):
        body = json.dumps({'code': '10000', 'out_trade_no': 'TEST_ORDER_0001', 'trade_no': '20261015000000012345', **values}, separators=(',', ':')).encode()
        signature = base64.b64encode(self.alipay_key.sign(body, padding.PKCS1v15(), hashes.SHA256()))
        return b'{"alipay_trade_' + method.encode() + b'_response":' + body + b',"sign":"' + signature + b'"}'

    def test_app_order_string_uses_official_sdk_and_can_be_verified_offline(self):
        response = self.create(product='app')
        self.assertEqual(response.status_code, 201)
        order = response.json['order']
        self.assertEqual(order['pay_url'], '')
        query = parse_qs(order['payment_payload']['order_string'])
        self.assertEqual(query['method'], ['alipay.trade.app.pay'])
        content = json.loads(query['biz_content'][0])
        self.assertEqual(content['product_code'], 'QUICK_MSECURITY_PAY')
        self.assertEqual(content['total_amount'], '0.01')
        sign = base64.b64decode(query.pop('sign')[0])
        unsigned = '&'.join(f'{k}={v[0]}' for k, v in sorted(query.items()) if v[0])
        self.app_key.public_key().verify(sign, unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
        self.assertEqual(order['status'], 'CREATED')
        self.assertEqual(self.create(product='app').json, response.json)

    def test_mini_creation_is_persisted_before_io_and_idempotent(self):
        buyer = 'test_OpenID_fixture_123456'
        def transport(*args):
            self.assertEqual(self.store.order('TEST_ORDER_0001')['status'], 'CREATED')
            biz = json.loads(args[3]['biz_content'])
            self.assertEqual(biz['buyer_open_id'], buyer)
            self.assertEqual(biz['total_amount'], '0.01')
            self.assertIn('time_expire', biz)
            return self.wire()
        with patch(TRANSPORT, side_effect=transport) as sender:
            first = self.create(product='mini', buyer_open_id=buyer)
            second = self.create(product='mini', buyer_open_id=buyer)
            sender.assert_called_once()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json, second.json)
        self.assertEqual(first.json['order']['payment_payload'], {'kind': 'mini', 'trade_no': '20261015000000012345'})
        self.assertEqual(first.json['order']['status'], 'CREATED')
        self.assertEqual(self.create(product='mini', buyer_open_id='other_OpenID_123456').status_code, 409)
        self.assertNotIn(buyer, json.dumps(self.store.order('TEST_ORDER_0001')))
        self.assertNotIn(buyer, (self.root / 'logs/payment.jsonl').read_text())
        self.assertEqual(self.notify(self.notification()).data, b'success')

    def test_mini_requires_exactly_one_valid_buyer_identifier(self):
        for values in ({}, {'buyer_open_id': ''}, {'buyer_id': 'invalid'}, {'buyer_id': '2088000000000001', 'buyer_open_id': 'buyer_open_fixture'}):
            self.assertEqual(self.create(product='mini', **values).status_code, 400)
        self.assertEqual(self.create(buyer_id='2088000000000001').status_code, 400)

    def test_network_failure_retry_keeps_order_and_valid_signature_is_required(self):
        with patch(TRANSPORT, side_effect=TimeoutError()):
            self.assertEqual(self.create(product='mini', buyer_id='2088000000000001').status_code, 502)
        self.assertEqual(len(self.store.orders(self.user_id)), 1)
        invalid = self.wire().replace(b'20261015000000012345', b'20261015000000054321')
        with patch(TRANSPORT, return_value=invalid):
            self.assertEqual(self.create(product='mini', buyer_id='2088000000000001').status_code, 502)
        self.assertEqual(json.loads(self.store.order('TEST_ORDER_0001')['payment_payload']), {})
        with patch(TRANSPORT, return_value=self.wire()):
            result = self.create(product='mini', buyer_id='2088000000000001')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(self.store.orders(self.user_id)), 1)

    def test_creation_response_must_match_order_and_merchant(self):
        for values in ({'out_trade_no': 'OTHER_ORDER'}, {'seller_id': 'wrong'}, {'total_amount': '0.02'}):
            with patch(TRANSPORT, return_value=self.wire(**values)):
                self.assertEqual(self.create(product='mini', buyer_id='2088000000000001').status_code, 502)
            self.assertEqual(json.loads(self.store.order('TEST_ORDER_0001')['payment_payload']), {})

    def test_network_retry_keeps_original_callback_after_settings_change(self):
        with patch(TRANSPORT, side_effect=TimeoutError()):
            self.create(product='mini', buyer_id='2088000000000001')
        self.app.extensions['site_settings'].save({'notify_url': 'https://next.example/alipay/notify'})
        with patch(TRANSPORT, return_value=self.wire()) as sender:
            result = self.create(product='mini', buyer_id='2088000000000001')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(parse_qs(sender.call_args.args[1])['notify_url'], ['https://pay.example.test/alipay/notify'])
        self.assertNotIn('provider_notify_url', result.json['order'])

    def test_removed_qr_product_is_rejected(self):
        self.assertEqual(self.create(product='scan').status_code, 422)
        self.assertNotIn('scan', self.client.get('/api/capabilities').json['products'])

    def test_website_cashier_qr_is_fetched_once_and_keeps_the_web_product(self):
        self.create()
        url = 'https://qr.alipay.com/website_fixture'
        with patch('web.cashier.fetch_cashier_qr', return_value=url) as fetch:
            for _ in range(2):
                response = self.client.post('/api/v1/payments/TEST_ORDER_0001/prepare-qr', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['order']['product'], 'page')
                self.assertEqual(response.json['order']['cashier_qr'], url)
            fetch.assert_called_once()
        response = self.client.get('/api/v1/payments/TEST_ORDER_0001/qr.svg')
        self.assertIn(b'viewBox="0 0 24 24"', response.data)
        self.assertEqual(self.app.test_client().get('/api/v1/payments/TEST_ORDER_0001/qr.svg').status_code, 401)
        self.notify(self.notification())
        self.assertEqual(self.client.get('/api/v1/payments/TEST_ORDER_0001/qr.svg').status_code, 409)

    def test_receipt_encoder_does_not_embed_untrusted_text_or_remote_resources(self):
        svg = payment_qr_svg('https://qr.alipay.com/test?text=<script>alert(1)</script>')
        self.assertNotIn('<script', svg)
        self.assertNotIn('href=', svg)


if __name__ == '__main__': unittest.main()
