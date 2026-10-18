import json
import time
import unittest
from unittest.mock import patch

import test_epay
from web.merchants import Merchants, QUOTA_RESERVATION_TTL


class MerchantQuotaTests(unittest.TestCase):
    setUpClass = classmethod(test_epay.EpayTests.setUpClass.__func__)
    setUp = test_epay.EpayTests.setUp
    fields = test_epay.EpayTests.fields
    create = test_epay.EpayTests.create
    paid = test_epay.EpayTests.paid

    def settings(self, **changes):
        current = self.merchants.payment_settings(self.user)
        payload = {**current['settings'], **changes, 'version': current['version']}
        return self.merchants.save_payment_settings(
            self.user, payload, '3000.00', self.app.extensions['site_settings'].read()['order_timeout_minutes'])

    def reservation(self, number):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM merchant_quota_reservations WHERE number=?', (number,)).fetchone()
        return dict(row) if row else None

    def reservation_for_merchant_order(self, merchant_no):
        with self.store.connect() as db:
            row = db.execute('''SELECT r.* FROM merchant_quota_reservations r
                JOIN merchant_orders m ON m.platform_no=r.number WHERE m.user_id=? AND m.merchant_no=?''',
                (self.user, merchant_no)).fetchone()
        return dict(row) if row else None

    def test_old_reservation_schema_is_migrated_and_expiry_is_backfilled(self):
        created = int(time.time()) - 45
        with self.store.connect() as db:
            db.execute('DROP TABLE merchant_quota_reservations')
            db.execute('''CREATE TABLE merchant_quota_reservations (
                number TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount TEXT NOT NULL, created_at INTEGER NOT NULL)''')
            db.execute('INSERT INTO merchant_quota_reservations VALUES (?,?,?,?)',
                       ('LEGACY_RESERVATION_1', self.user, '0.25', created))

        Merchants(self.store, self.app.extensions['site_settings'])

        with self.store.connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(merchant_quota_reservations)')}
            row = db.execute('SELECT * FROM merchant_quota_reservations WHERE number=?',
                             ('LEGACY_RESERVATION_1',)).fetchone()
        self.assertIn('expires_at', columns)
        self.assertEqual(row['expires_at'], created + QUOTA_RESERVATION_TTL)

    def test_expired_reservations_are_removed_and_do_not_count_against_daily_or_pending_limits(self):
        settings = {**self.merchants.payment_settings(self.user)['settings'],
                    'daily_limit': '1.00', 'max_pending_orders': 1}
        now = int(time.time())
        with patch('web.merchants.time.time', return_value=now):
            self.merchants.reserve_quota(self.user, 'TTL_RESERVATION_1', '0.60', settings)
        reservation = self.reservation('TTL_RESERVATION_1')
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation['expires_at'], now + QUOTA_RESERVATION_TTL)

        with patch('web.merchants.time.time', return_value=now + QUOTA_RESERVATION_TTL):
            self.merchants.reserve_quota(self.user, 'TTL_RESERVATION_1', '0.60', settings)
        retried = self.reservation('TTL_RESERVATION_1')
        self.assertEqual(retried['expires_at'], now + 2 * QUOTA_RESERVATION_TTL)

        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.merchants.check_quota(db, self.user, '0.60', settings, now + 2 * QUOTA_RESERVATION_TTL)
            left = db.execute('SELECT 1 FROM merchant_quota_reservations WHERE number=?',
                              ('TTL_RESERVATION_1',)).fetchone()
        self.assertIsNone(left)
        with patch('web.merchants.time.time', return_value=now + 2 * QUOTA_RESERVATION_TTL):
            self.merchants.reserve_quota(self.user, 'TTL_RESERVATION_2', '0.60', settings)
        self.assertIsNotNone(self.reservation('TTL_RESERVATION_2'))

    def test_release_quota_requires_matching_user_and_amount(self):
        settings = {**self.merchants.payment_settings(self.user)['settings'], 'daily_limit': '1.00'}
        self.merchants.reserve_quota(self.user, 'RELEASE_RESERVATION_1', '0.40', settings)
        release = self.merchants.release_quota

        release('RELEASE_RESERVATION_1', self.user + 1, '0.40')
        self.assertIsNotNone(self.reservation('RELEASE_RESERVATION_1'))
        release('RELEASE_RESERVATION_1', self.user, '0.41')
        self.assertIsNotNone(self.reservation('RELEASE_RESERVATION_1'))

        release('RELEASE_RESERVATION_1', self.user, '0.40')
        self.assertIsNone(self.reservation('RELEASE_RESERVATION_1'))

    def test_epay_provider_exception_releases_reservation_and_same_merchant_order_can_retry(self):
        self.settings(daily_limit='0.01')
        fields = self.fields(out_trade_no='EPAY_PROVIDER_RETRY_1')

        class FailingClient:
            def __init__(self, config):
                raise RuntimeError('provider setup failure')

        with patch.dict(self.app.config, ALIPAY_CLIENT_FACTORY=FailingClient):
            with self.assertRaisesRegex(RuntimeError, 'provider setup failure'):
                self.client.post('/mapi.php', data=test_epay.signed(fields, self.profile['md5_key']))

        failed = self.merchants.order(self.reservation_for_merchant_order('EPAY_PROVIDER_RETRY_1')['number']) if self.reservation_for_merchant_order('EPAY_PROVIDER_RETRY_1') else None
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM merchant_orders WHERE user_id=? AND merchant_no=?',
                             (self.user, 'EPAY_PROVIDER_RETRY_1')).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['reservation_state'], 'failed')
        self.assertIsNone(self.reservation(row['platform_no']))
        self.assertIsNone(failed)

        retry = self.client.post('/mapi.php', data=test_epay.signed(fields, self.profile['md5_key']))
        self.assertEqual(retry.json['code'], 1, retry.json)
        blocked = self.client.post('/mapi.php', data=test_epay.signed(
            self.fields(out_trade_no='EPAY_PROVIDER_RETRY_2'), self.profile['md5_key']))
        self.assertEqual(blocked.json['error'], 'merchant_daily_limit_exceeded')

    def test_regular_api_provider_exception_releases_reservation_and_replay_rechecks_limits(self):
        self.settings(daily_limit='0.01')
        session = self.client.post('/api/auth/login', json={
            'email': 'merchant@example.test', 'password': 'test-password-123'}).json
        headers = {'Origin': 'https://pay.example.test', 'X-CSRF-Token': session['csrf_token']}
        payload = {'out_trade_no': 'API_PROVIDER_RETRY_1', 'amount': '0.01', 'subject': 'API provider retry',
                   'channel': 'alipay', 'product': 'page'}

        class FailingClient:
            def __init__(self, config):
                raise RuntimeError('provider setup failure')

        with patch.dict(self.app.config, ALIPAY_CLIENT_FACTORY=FailingClient):
            with self.assertRaisesRegex(RuntimeError, 'provider setup failure'):
                self.client.post('/api/v1/payments', json=payload, headers=headers)

        self.assertIsNone(self.reservation('API_PROVIDER_RETRY_1'))
        self.assertIsNone(self.store.order('API_PROVIDER_RETRY_1'))
        retry = self.client.post('/api/v1/payments', json=payload, headers=headers)
        self.assertEqual(retry.status_code, 201, retry.json)
        blocked = self.client.post('/api/v1/payments', json={**payload, 'out_trade_no': 'API_PROVIDER_RETRY_2'}, headers=headers)
        self.assertEqual((blocked.status_code, blocked.json['error']), (403, 'merchant_daily_limit_exceeded'))

    def test_existing_epay_order_idempotency_does_not_reserve_quota_twice(self):
        self.settings(daily_limit='0.01')
        fields = self.fields(out_trade_no='EPAY_IDEMPOTENT_1')
        first = self.client.post('/mapi.php', data=test_epay.signed(fields, self.profile['md5_key']))
        second = self.client.post('/mapi.php', data=test_epay.signed(fields, self.profile['md5_key']))
        self.assertEqual(first.json['trade_no'], second.json['trade_no'])
        self.assertEqual(second.json, first.json)
        self.assertEqual(self.client.post('/mapi.php', data=test_epay.signed(
            self.fields(out_trade_no='EPAY_IDEMPOTENT_2'), self.profile['md5_key'])).json['error'],
            'merchant_daily_limit_exceeded')

    def test_pending_order_limit_and_fee_rate_are_snapshotted_per_order(self):
        self.settings(max_pending_orders=1, fee_rate='10.00')
        first_fields = self.fields(out_trade_no='PENDING_LIMIT_1', money='1.00')
        first = self.client.post('/mapi.php', data=test_epay.signed(first_fields, self.profile['md5_key']))
        self.assertEqual(first.json['code'], 1, first.json)
        blocked = self.client.post('/mapi.php', data=test_epay.signed(
            self.fields(out_trade_no='PENDING_LIMIT_2', money='1.00'), self.profile['md5_key']))
        self.assertEqual(blocked.json['error'], 'merchant_pending_limit_exceeded')
        self.settings(max_pending_orders=0, fee_rate='25.00')
        with self.store.connect() as db:
            snapshot = db.execute('SELECT gross,fee,policy FROM financial_orders WHERE number=?',
                                  (first.json['trade_no'],)).fetchone()
        policy = json.loads(snapshot['policy'])
        self.assertEqual(policy['fee_rate'], '10.00')
        self.assertEqual((snapshot['gross'], snapshot['fee']), (100, 10))

    def test_payment_configuration_selection_obeys_first_round_robin_and_minimum_volume(self):
        second_key = test_epay.rsa.generate_private_key(public_exponent=65537, key_size=2048)
        second = self.app.extensions['channels'].create('alipay', 'Quota secondary', {
            'env': 'sandbox', 'app_id': '2026000000000009', 'seller_id': '2088000000000001',
            'app_private_key': test_epay.secret_pem(second_key),
            'alipay_public_key': test_epay.public_pem(self.provider_key)}, self.user)
        channels = self.app.extensions['channels']
        default_id = next(item['config_id'] for item in channels.all()
                          if item['id'] == 'alipay' and item['is_default'])
        config_ids = sorted([default_id, second['config_id']])

        self.settings(load_balance='first')
        self.assertEqual(self.merchants.select_config(self.user, 'alipay', channels), config_ids[0])
        self.settings(load_balance='round_robin')
        self.assertEqual([self.merchants.select_config(self.user, 'alipay', channels) for _ in range(4)],
                         [config_ids[0], config_ids[1], config_ids[0], config_ids[1]])

        self.settings(load_balance='minimum_amount')
        first_fields = self.fields(out_trade_no='ROUTE_VOLUME_1', money='0.01')
        second_fields = self.fields(out_trade_no='ROUTE_VOLUME_2', money='0.02')
        order_numbers=[]
        for fields in (first_fields, second_fields):
            response = self.client.post('/mapi.php', data=test_epay.signed(fields, self.profile['md5_key']))
            self.assertEqual(response.json['code'], 1, response.json)
            order_numbers.append(response.json['trade_no'])
        with self.store.connect() as db:
            db.execute('UPDATE orders SET payment_config_id=? WHERE out_trade_no=?', (config_ids[0], order_numbers[0]))
            db.execute('UPDATE orders SET payment_config_id=? WHERE out_trade_no=?', (config_ids[1], order_numbers[1]))
        self.assertEqual(self.merchants.select_config(self.user, 'alipay', channels), config_ids[0])


if __name__ == '__main__':
    unittest.main()
