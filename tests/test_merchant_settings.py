import json
import unittest

import test_epay


class MerchantSettingsTests(unittest.TestCase):
    setUpClass = classmethod(test_epay.EpayTests.setUpClass.__func__)
    setUp = test_epay.EpayTests.setUp
    fields = test_epay.EpayTests.fields
    create = test_epay.EpayTests.create

    def save_platform(self, **changes):
        site = self.app.extensions['site_settings']
        current = site.read()
        return site.save({**changes, 'version': current['version']})

    def save_platform_fee(self, fee_rate):
        rules = self.app.extensions['operations'].rules
        current = rules.read()
        return rules.save({**current, 'fee_rate': fee_rate, 'version': current['version']})

    def save_merchant(self, **changes):
        current = self.merchants.payment_settings(self.user)
        return self.merchants.save_payment_settings(
            self.user, {**current['settings'], **changes, 'version': current['version']},
            '3000.00', self.app.extensions['site_settings'].read()['order_timeout_minutes'])

    def financial_policy(self, number):
        with self.store.connect() as db:
            row = db.execute('SELECT fee,policy FROM financial_orders WHERE number=?', (number,)).fetchone()
        return row['fee'], json.loads(row['policy'])

    def test_unsaved_merchant_fee_inherits_platform_fee_for_epay_orders(self):
        self.save_platform_fee('5.00')
        self.assertEqual(self.merchants.payment_settings(self.user)['version'], 0)
        result = self.create(money='100.00')
        self.assertEqual(result.json['code'], 1, result.json)
        fee, policy = self.financial_policy(result.json['trade_no'])
        self.assertEqual(policy['fee_rate'], '5.00')
        self.assertEqual(fee, 500)

    def test_saved_merchant_fee_is_snapshotted_and_existing_order_is_unchanged(self):
        self.save_platform_fee('5.00')
        self.save_merchant(fee_rate='2.50')
        created = self.create(money='100.00')
        number = created.json['trade_no']
        self.assertEqual(created.json['code'], 1, created.json)
        fee, policy = self.financial_policy(number)
        self.assertEqual((fee, policy['fee_rate']), (250, '2.50'))
        self.save_merchant(fee_rate='8.75')
        self.assertEqual(self.financial_policy(number), (250, policy))

    def test_payment_settings_get_inherits_current_platform_timeout(self):
        self.save_platform(order_timeout_minutes=27)
        login = self.client.post('/api/auth/login', json={'email': 'merchant@example.test', 'password': 'test-password-123'})
        self.assertEqual(login.status_code, 200, login.json)
        response = self.client.get('/api/merchant/payment-settings')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['platform_order_timeout_minutes'], 27)
        self.assertEqual(response.json['settings']['timeout_minutes'], 27)
        self.assertEqual(response.json['version'], 0)

    def test_unsaved_timeout_used_by_epay_and_tracks_platform_default(self):
        self.save_platform(order_timeout_minutes=23)
        response = self.create(out_trade_no='PLATFORM_TIMEOUT_INHERIT')
        self.assertEqual(response.json['code'], 1, response.json)
        order = self.store.order(response.json['trade_no'])
        self.assertEqual(order['expires_at'] - order['created_at'], 23 * 60)

    def test_legacy_quota_schema_migrates_and_backfills_expiry(self):
        with self.store.connect() as db:
            db.execute('DROP TABLE merchant_quota_reservations')
            db.execute('CREATE TABLE merchant_quota_reservations (number TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount TEXT NOT NULL, created_at INTEGER NOT NULL)')
            db.execute('INSERT INTO merchant_quota_reservations VALUES (?,?,?,?)', ('LEGACY_RESERVATION', self.user, '1.00', 1000))
        from web.merchants import Merchants
        Merchants(self.store, self.app.extensions['site_settings'])
        with self.store.connect() as db:
            row = db.execute('SELECT expires_at FROM merchant_quota_reservations WHERE number=?', ('LEGACY_RESERVATION',)).fetchone()
        self.assertEqual(row['expires_at'], 1120)

    def test_expired_quota_reservation_is_rechecked_and_refreshed(self):
        settings = self.merchants.payment_settings(self.user)['settings']
        now = 2_000_000_000
        with self.store.connect() as db:
            db.execute('INSERT INTO merchant_quota_reservations(number,user_id,amount,created_at,expires_at) VALUES (?,?,?,?,?)',
                       ('EXPIRED_RESERVATION', self.user, '0.01', now - 180, now - 60))
        with self.store.connect() as db:
            self.merchants.check_quota(db, self.user, '0.01', settings, now)
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM merchant_quota_reservations WHERE number=?', ('EXPIRED_RESERVATION',)).fetchone())
        self.merchants.reserve_quota(self.user, 'EXPIRED_RESERVATION', '0.01', settings)
        with self.store.connect() as db:
            row = db.execute('SELECT created_at,expires_at FROM merchant_quota_reservations WHERE number=?', ('EXPIRED_RESERVATION',)).fetchone()
        self.assertGreater(row['expires_at'], row['created_at'])
        self.assertEqual(row['expires_at'] - row['created_at'], 120)
