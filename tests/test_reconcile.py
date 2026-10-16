import base64
import json
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

import test_payments
from bypay.alipay_client import AlipayClient
from web.app import create_app, payment_config
from web.store import Store

TRANSPORT = 'alipay.aop.api.DefaultAlipayClient.do_post'


class ReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_payments.PaymentTests.setUpClass.__func__(cls)

    setUp = test_payments.PaymentTests.setUp
    create = test_payments.PaymentTests.create
    notification = test_payments.PaymentTests.notification
    notify = test_payments.PaymentTests.notify

    def wire_response(self, **fields):
        data = {'code': '10000', 'msg': 'Success', 'out_trade_no': 'TEST_ORDER_0001',
                'total_amount': '0.01', 'trade_status': 'TRADE_SUCCESS', 'trade_no': '20261015000000012345', **fields}
        body = json.dumps(data, separators=(',', ':')).encode()
        signature = base64.b64encode(self.alipay_key.sign(body, padding.PKCS1v15(), hashes.SHA256()))
        return b'{"alipay_trade_query_response":' + body + b',"sign":"' + signature + b'"}'

    def token(self, response):
        checkout = parse_qs(urlsplit(response.json['order']['pay_url']).query)
        return parse_qs(urlsplit(checkout['return_url'][0]).query)['state'][0]

    def due(self):
        return self.store.payment_check('TEST_ORDER_0001')['next_check'] + 1

    def test_worker_uses_actual_sdk_signature_verification_and_saves_once(self):
        self.create(notify_url='https://test.bingyun.vip/notify')
        with patch('web.store.time.time', return_value=self.due()), patch(TRANSPORT, return_value=self.wire_response()) as transport:
            service = self.app.extensions['reconciler']
            self.assertTrue(service.tick())
            self.assertFalse(service.tick())
            self.assertEqual(service.sync('TEST_ORDER_0001')['order']['status'], 'PAID')
            transport.assert_called_once()
        paid_at = self.store.order('TEST_ORDER_0001')['paid_at']
        self.assertEqual(self.notify(self.notification()).data, b'success')
        self.assertEqual(self.store.order('TEST_ORDER_0001')['paid_at'], paid_at)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)
        event = next(e for e in self.store.events() if e['stage'] == 'query.accepted')
        self.assertEqual(event['details']['source'], 'automatic')

    def test_sdk_tampered_signed_response_cannot_mark_paid(self):
        self.create()
        wire = self.wire_response(trade_status='WAIT_BUYER_PAY').replace(b'WAIT_BUYER_PAY', b'TRADE_SUCCESS')
        with patch('web.store.time.time', return_value=self.due()), patch(TRANSPORT, return_value=wire):
            self.app.extensions['reconciler'].tick()
        result = self.app.extensions['reconciler'].snapshot('TEST_ORDER_0001')
        self.assertEqual(result['order']['status'], 'CREATED')
        self.assertEqual(result['verification']['error'], 'payment_response_signature_invalid')

    def test_unsigned_sdk_success_is_rejected(self):
        self.create()
        wire = b'{"alipay_trade_query_response":{"code":"10000","trade_status":"TRADE_SUCCESS"}}'
        with patch(TRANSPORT, return_value=wire):
            result = self.app.extensions['reconciler'].sync('TEST_ORDER_0001')
        self.assertEqual(result['order']['status'], 'CREATED')
        self.assertEqual(result['verification']['state'], 'error')

    def test_signed_response_must_match_exact_order_amount_and_merchant(self):
        self.create()
        self.create(out_trade_no='TEST_ORDER_0002')
        for fields in [{'out_trade_no': 'TEST_ORDER_0002'}, {'total_amount': '0.02'}, {'app_id': 'wrong'}, {'seller_id': 'wrong'}, {'trade_status': 'UNKNOWN'}]:
            with self.subTest(fields=fields):
                now = self.store.payment_check('TEST_ORDER_0001')['locked_until'] + 1
                with patch('web.store.time.time', return_value=max(now, self.due())), patch(TRANSPORT, return_value=self.wire_response(**fields)):
                    result = self.app.extensions['reconciler'].sync('TEST_ORDER_0001')
                self.assertEqual(result['verification']['error'], 'payment_mismatch')
                self.assertEqual(self.store.order('TEST_ORDER_0001')['status'], 'CREATED')
                self.assertEqual(self.store.order('TEST_ORDER_0002')['status'], 'CREATED')

    def test_missing_trade_is_waiting_and_configuration_error_is_not_unpaid(self):
        self.create()
        for response, state, error in [
            ({'code': '40004', 'sub_code': 'ACQ.TRADE_NOT_EXIST'}, 'awaiting_checkout', ''),
            ({'code': '40002', 'sub_code': 'isv.invalid-signature'}, 'error', 'payment_request_signature_invalid'),
        ]:
            now = self.due()
            with patch('web.store.time.time', return_value=now), patch(TRANSPORT, return_value=self.wire_response(**response)):
                result = self.app.extensions['reconciler'].sync('TEST_ORDER_0001')
            self.assertEqual(result['order']['status'], 'CREATED')
            self.assertEqual(result['verification']['state'], state)
            self.assertEqual(result['verification']['error'], error)
            response_event = next(e for e in self.store.events() if e['stage'] == 'query.response')
            self.assertEqual(bool(response_event['accepted']), state == 'awaiting_checkout')
            self.assertGreater(self.store.payment_check('TEST_ORDER_0001')['next_check'], now)

    def test_waiting_trade_can_later_be_paid_and_closed_trade_stops_polling(self):
        self.create()
        service = self.app.extensions['reconciler']
        with patch(TRANSPORT, return_value=self.wire_response(trade_status='WAIT_BUYER_PAY')):
            self.assertEqual(service.sync('TEST_ORDER_0001')['verification']['state'], 'waiting')
        with patch('web.store.time.time', return_value=self.due()), patch(TRANSPORT, return_value=self.wire_response(trade_status='TRADE_CLOSED')):
            self.assertTrue(service.tick())
        self.assertEqual(self.store.order('TEST_ORDER_0001')['status'], 'CLOSED')
        self.assertFalse(service.tick())

    def test_query_lease_cooldown_and_expiration_survive_another_worker(self):
        self.create()
        first = self.store.claim_payment_check('TEST_ORDER_0001')
        other = Store(self.store.path)
        self.assertIsNone(other.claim_payment_check('TEST_ORDER_0001'))
        locked_until = self.store.payment_check('TEST_ORDER_0001')['locked_until']
        with patch('web.store.time.time', return_value=locked_until + 1):
            replacement = other.claim_payment_check('TEST_ORDER_0001')
            self.store.finish_payment_check(first, 'waiting')
            self.assertEqual(other.payment_check('TEST_ORDER_0001')['lease'], replacement['lease'])
            self.assertIsNone(self.store.claim_payment_check('TEST_ORDER_0001'))
            other.finish_payment_check(replacement, 'waiting')
            self.assertIsNone(self.store.claim_payment_check('TEST_ORDER_0001'))

    def test_background_query_is_bounded_but_manual_recovery_remains_available(self):
        self.create()
        future = self.store.order('TEST_ORDER_0001')['created_at'] + 86401
        with patch('web.store.time.time', return_value=future), patch(TRANSPORT, return_value=self.wire_response()) as transport:
            self.assertFalse(self.app.extensions['reconciler'].tick())
            transport.assert_not_called()
            self.assertEqual(self.app.extensions['reconciler'].sync('TEST_ORDER_0001')['order']['status'], 'PAID')

    def test_return_token_only_locates_order_and_never_confirms_payment(self):
        token = self.token(self.create())
        visitor = self.app.test_client()
        response = visitor.get('/alipay/return?' + urlencode({'state': token, 'trade_status': 'TRADE_SUCCESS', 'total_amount': '100', 'out_trade_no': 'WRONG_ORDER'}))
        self.assertEqual(response.location, '/payment-return#state=' + token)
        self.assertEqual(self.store.order('TEST_ORDER_0001')['status'], 'CREATED')
        with patch(TRANSPORT, return_value=self.wire_response()):
            receipt = visitor.post('/api/payment-result', json={'state': token}, headers={'Origin': 'https://pay.example.test'})
        self.assertEqual(receipt.status_code, 200)
        self.assertEqual(receipt.json['order']['status'], 'PAID')
        self.assertEqual(set(receipt.json['order']), {'out_trade_no', 'subject', 'amount', 'status', 'paid_at'})
        self.assertEqual(visitor.get('/api/v1/payments/TEST_ORDER_0001').status_code, 401)
        self.assertNotIn(token, (self.root / 'logs/payment.jsonl').read_text())

    def test_return_capability_cannot_be_guessed_expired_or_used_cross_origin(self):
        token = self.token(self.create())
        for candidate in ['', 'TEST_ORDER_0001', 'a' * 43, {'state': token}]:
            self.assertEqual(self.client.post('/api/payment-result', json={'state': candidate}).status_code, 404)
        self.assertEqual(self.client.post('/api/payment-result', json={'state': token}, headers={'Origin': 'https://evil.test'}).status_code, 403)
        self.assertEqual(self.client.get('/alipay/return?state=' + token + '&state=' + token).location, '/payment-return')
        with self.store.connect() as db:
            self.assertNotEqual(db.execute('SELECT hash FROM payment_returns').fetchone()[0], token)
            db.execute('UPDATE payment_returns SET expires=0')
        self.assertEqual(self.client.post('/api/payment-result', json={'state': token}).status_code, 404)

    def test_old_checkout_return_needs_real_signature_before_issuing_receipt(self):
        self.create()
        visitor = self.app.test_client()
        params = self.notification()
        unsigned = {**params, 'sign': 'invalid'}
        self.assertEqual(visitor.get('/alipay/return?' + urlencode(unsigned)).location, '/payment-return')
        response = visitor.get('/alipay/return?' + urlencode(params))
        token = parse_qs(urlsplit(response.location).fragment)['state'][0]
        self.assertEqual(self.store.return_order(token)['out_trade_no'], 'TEST_ORDER_0001')
        self.assertEqual(self.store.order('TEST_ORDER_0001')['status'], 'CREATED')

    def test_local_browser_alias_preserves_return_host_and_uses_same_verified_query(self):
        local = create_app({**self.app.config, 'PUBLIC_BASE_URL': 'http://localhost:8000', 'BYPAY_RUN_MODE': 'local'})
        self.client = local.test_client()
        login = self.client.post('/api/auth/login', json={'email': 'user@example.test', 'password': 'Test-user-password-123'}, headers={'Origin': 'http://127.0.0.1:8000'})
        self.headers = {'Origin': 'http://127.0.0.1:8000', 'X-CSRF-Token': login.json['csrf_token']}
        response = self.create()
        query = parse_qs(urlsplit(response.json['order']['pay_url']).query)
        self.assertNotIn('notify_url', query)
        self.assertTrue(query['return_url'][0].startswith('http://127.0.0.1:8000/alipay/return?state='))
        with patch(TRANSPORT, return_value=self.wire_response()):
            self.assertEqual(local.extensions['reconciler'].sync('TEST_ORDER_0001')['order']['status'], 'PAID')

    def test_public_notification_and_browser_origins_are_independent(self):
        cfg = payment_config({'PUBLIC_BASE_URL': 'http://localhost:8000', 'ALIPAY_NOTIFY_URL': 'https://tunnel.example.test/alipay/notify'})
        self.assertEqual(cfg['return_url'], 'http://localhost:8000/alipay/return')
        self.assertEqual(cfg['notify_url'], 'https://tunnel.example.test/alipay/notify')
        production = payment_config({'PUBLIC_BASE_URL': 'https://pay.bingyun.vip'})
        self.assertEqual(production['notify_url'], 'https://pay.bingyun.vip/alipay/notify')
        for url in ['http://localhost:8000/alipay/notify', 'https://test.bingyun.vip/notify', 'https://user@pay.bingyun.vip/alipay/notify']:
            with self.assertRaises(ValueError):
                payment_config({'PUBLIC_BASE_URL': 'http://localhost:8000', 'ALIPAY_NOTIFY_URL': url})


if __name__ == '__main__':
    unittest.main()
