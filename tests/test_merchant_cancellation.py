import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import test_epay
from bypay.epay_protocol import signed, verify


class MerchantCancellationTests(unittest.TestCase):
    setUpClass = classmethod(test_epay.EpayTests.setUpClass.__func__)
    setUp = test_epay.EpayTests.setUp
    fields = test_epay.EpayTests.fields
    create = test_epay.EpayTests.create
    paid = test_epay.EpayTests.paid

    def cancel(self, number, version='v1', **extra):
        params = {'pid': self.profile['merchant_id'], 'trade_no': number, **extra}
        params = signed(params, self.profile['md5_key'] if version == 'v1' else self.merchant_key,
                        'MD5' if version == 'v1' else 'RSA')
        return self.client.post('/api/pay/cancel-v1' if version == 'v1' else '/api/pay/cancel', data=params).json

    def settings(self, **changes):
        current = self.merchants.payment_settings(self.user)
        return self.merchants.save_payment_settings(self.user, {**current['settings'], **changes, 'version': current['version']}, '3000.00', 1440)['settings']

    def test_v1_v2_close_idempotency_and_response_signature(self):
        for version in ('v1', 'v2'):
            with self.subTest(version=version):
                merchant_no = 'CANCEL_' + version
                number = self.create(version, out_trade_no=merchant_no).json['trade_no']
                with patch.dict(self.app.extensions, merchant_close_provider=lambda order: {'is_success': True, 'provider_state': 'TRADE_CLOSED'}):
                    result = self.cancel(number, version)
                self.assertEqual(result['code'], 1 if version == 'v1' else 0, result)
                self.assertEqual(result['out_trade_no'], merchant_no)
                self.assertEqual(self.store.order(number)['status'], 'CLOSED')
                with patch.dict(self.app.extensions, merchant_close_provider=lambda order: self.fail('Repeated provider close')):
                    again = self.cancel(number, version)
                self.assertTrue(again['idempotent'], again)
                if version == 'v2':
                    self.assertTrue(verify(result, self.profile['platform_public_key'], 'RSA', timestamp=True))

    def test_invalid_signature_and_cross_merchant_are_rejected(self):
        number = self.create().json['trade_no']
        params = signed({'pid': self.profile['merchant_id'], 'trade_no': number}, 'wrong-key')
        self.assertEqual(self.client.post('/api/pay/cancel-v1', data=params).json['error'], 'invalid_signature')
        other = self.store.register('other@example.test', 'test-password-123')
        profile = self.merchants.describe(other['id'])
        params = signed({'pid': profile['merchant_id'], 'trade_no': number}, profile['md5_key'])
        self.assertEqual(self.client.post('/api/pay/cancel-v1', data=params).json['error'], 'order_not_found')
        self.assertEqual(self.store.order(number)['status'], 'CREATED')

    def test_provider_failure_and_exception_do_not_consume_cancel_limit(self):
        self.settings(cancel_limit=1)
        number = self.create().json['trade_no']
        for fail in (lambda order: {'is_success': False}, lambda order: (_ for _ in ()).throw(RuntimeError('network'))):
            with patch.dict(self.app.extensions, merchant_close_provider=fail):
                self.assertEqual(self.cancel(number)['error'], 'merchant_cancel_failed')
            self.assertEqual(self.store.order(number)['status'], 'CREATED')
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT state FROM merchant_cancel_requests WHERE platform_no=?', (number,)).fetchone()[0], 'failed')
        with patch.dict(self.app.extensions, merchant_close_provider=lambda order: {'is_success': True}):
            self.assertEqual(self.cancel(number)['code'], 1)
            another = self.create(out_trade_no='SECOND_CANCEL').json['trade_no']
            self.assertEqual(self.cancel(another)['error'], 'merchant_cancel_limit_exceeded')

    def test_payment_during_provider_close_cannot_be_downgraded(self):
        number = self.create().json['trade_no']
        def provider(order):
            self.paid(number)
            return {'is_success': True, 'provider_state': 'TRADE_CLOSED'}
        with patch.dict(self.app.extensions, merchant_close_provider=provider):
            self.assertEqual(self.cancel(number)['error'], 'merchant_order_not_cancellable')
        self.assertEqual(self.store.order(number)['status'], 'PAID')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM merchant_cancel_requests WHERE platform_no=?', (number,)).fetchone()[0], 'superseded')

    def test_late_paid_notification_after_close_remains_authoritative(self):
        number = self.create().json['trade_no']
        with patch.dict(self.app.extensions, merchant_close_provider=lambda order: {'is_success': True}):
            self.assertEqual(self.cancel(number)['code'], 1)
        self.paid(number)
        self.assertEqual(self.store.order(number)['status'], 'PAID')
        self.assertEqual(self.cancel(number)['error'], 'merchant_order_not_cancellable')

    def test_natural_expiry_and_provider_close_do_not_count(self):
        self.settings(cancel_limit=1)
        expired = self.create(out_trade_no='EXPIRED_CANCEL').json['trade_no']
        closed = self.create(out_trade_no='CLOSED_CANCEL').json['trade_no']
        with self.store.connect() as db:
            db.execute('UPDATE orders SET expires_at=? WHERE out_trade_no=?', (int(time.time()) - 1, expired))
        self.store.close_unpaid(closed, 'TRADE_CLOSED')
        for number in (expired, closed):
            self.assertEqual(self.cancel(number)['error'], 'merchant_order_not_cancellable')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM merchant_cancel_requests').fetchone()[0], 0)

    def test_pending_requests_reserve_frequency_slot_concurrently(self):
        settings = self.settings(cancel_limit=1)
        numbers = [self.create(out_trade_no='CONCURRENT_' + str(i)).json['trade_no'] for i in range(2)]
        barrier = Barrier(2)
        def begin(number):
            barrier.wait()
            try:
                self.merchants.begin_cancel(self.user, number, settings)
                return 'accepted'
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(begin, numbers))
        self.assertCountEqual(results, ['accepted', 'merchant_cancel_limit_exceeded'])

    def test_legacy_cancel_entry(self):
        number = self.create().json['trade_no']
        with patch.dict(self.app.extensions, merchant_close_provider=lambda order: {'is_success': True}):
            result = self.client.post('/api.php', data={'act': 'cancel', 'pid': self.profile['merchant_id'], 'key': self.profile['md5_key'], 'trade_no': number}).json
        self.assertEqual(result['code'], 1, result)
        self.assertEqual(result['out_trade_no'], 'ORDER_SHARED_01')
