import csv
import io
import json
import sqlite3
import time
import unittest

import test_payments
from web.store import Store


class ConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_payments.PaymentTests.setUpClass.__func__(cls)

    setUp = test_payments.PaymentTests.setUp
    create = test_payments.PaymentTests.create
    notification = test_payments.PaymentTests.notification
    notify = test_payments.PaymentTests.notify

    def admin(self):
        client = self.app.test_client()
        response = client.post('/api/auth/login', json={'email': 'admin@example.test', 'password': 'Test-admin-password-123'})
        return client, {'X-CSRF-Token': response.json['csrf_token'], 'Origin': 'https://pay.example.test'}

    def test_console_data_and_all_statistics_respect_user_scope(self):
        self.create()
        self.notify(self.notification())
        data = self.client.get('/api/console/overview?period=7d').json
        self.assertEqual(data['revenue_cents'], 1)
        self.assertEqual(data['paid_count'], 1)
        self.assertEqual(sum(row['amount_cents'] for row in data['trend']), 1)
        other = self.app.test_client()
        other.post('/api/auth/register', json={'email': 'other@example.test', 'password': 'Test-other-password-123'})
        self.assertEqual(other.get('/api/console/overview').json['revenue_cents'], 0)
        for endpoint, field in [('orders', 'orders'), ('events', 'events'), ('callbacks', 'callbacks')]:
            response = other.get('/api/console/' + endpoint)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json[field], [])

    def test_overview_and_pagination_are_not_limited_to_latest_100(self):
        cfg = self.app.extensions['alipay_config']
        for i in range(107):
            self.store.create_order(self.user_id, {'out_trade_no': f'BULK_TEST_{i:04}', 'subject': 'Pagination test', 'amount': '0.01', 'product': 'page'}, 'https://example.test', cfg)
        self.assertEqual(self.client.get('/api/console/overview').json['order_count'], 107)
        response = self.client.get('/api/console/orders?page=6&page_size=20').json
        self.assertEqual(response['total'], 107)
        self.assertEqual(len(response['orders']), 7)
        self.assertNotIn('seller_id', response['orders'][0])

    def test_filters_and_export_with_formula_injection_protection(self):
        self.create(subject='=HYPERLINK("https://example.test")')
        response = self.client.get('/api/console/orders?search=TEST_ORDER&status=CREATED&period=today')
        self.assertEqual(response.json['total'], 1)
        self.assertEqual(self.client.get('/api/console/orders?status=PAID').json['total'], 0)
        export = self.client.get('/api/console/orders/export?search=TEST_ORDER')
        self.assertEqual(export.status_code, 200)
        rows = list(csv.reader(io.StringIO(export.data.decode('utf-8-sig'))))
        self.assertTrue(rows[1][1].startswith("'="))
        self.assertNotIn('pay_url', rows[0])
        self.assertEqual(self.client.get('/api/console/orders?status=bogus').status_code, 400)

    def test_only_admin_can_manage_accounts_or_settings(self):
        for path in ['/api/admin/users', '/api/admin/settings']:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.put('/api/admin/settings', headers=self.headers, json={'registration': False}).status_code, 403)
        admin, headers = self.admin()
        self.assertEqual(admin.put('/api/admin/settings', json={'registration': False}).status_code, 403)
        response = admin.get('/api/admin/settings')
        self.assertNotIn(self.private, json.dumps(response.json))
        self.assertNotIn('password', json.dumps(admin.get('/api/admin/users').json))

    def test_channel_pause_blocks_new_orders_but_not_payment_notifications(self):
        self.create()
        admin, headers = self.admin()
        response = admin.put('/api/admin/settings', headers=headers, json={'alipay_enabled': False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.create(out_trade_no='NEW_ORDER_0002').json['error'], 'channel_disabled')
        self.assertEqual(self.notify(self.notification()).data, b'success')
        self.assertEqual(self.store.order('TEST_ORDER_0001')['status'], 'PAID')
        self.assertFalse(self.client.get('/api/capabilities').json['channel_enabled'])

    def test_registration_and_amount_settings_are_persisted_and_enforced(self):
        admin, headers = self.admin()
        self.assertEqual(admin.put('/api/admin/settings', headers=headers, json={'registration': False, 'max_amount': '0.50'}).status_code, 200)
        self.assertEqual(self.app.test_client().post('/api/auth/register', json={'email': 'new@example.test', 'password': 'New-test-password-123'}).status_code, 403)
        self.assertEqual(self.create(amount='0.51').json['error'], 'test_amount_exceeded')
        self.assertEqual(Store(self.store.path).setting('max_amount', None), '0.50')
        self.assertEqual(admin.put('/api/admin/settings', headers=headers, json={'max_amount': '1000.00'}).status_code, 400)
        self.assertEqual(admin.put('/api/admin/settings', headers=headers, json={'app_private_key': 'changed'}).status_code, 400)

    def test_disable_revokes_sessions_and_keys_and_protects_admin(self):
        token = self.client.post('/api/keys', headers=self.headers).json['api_key']
        admin, headers = self.admin()
        self.assertEqual(admin.post(f'/api/admin/users/{self.user_id}/access', headers=headers, json={'disabled': True}).status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)
        self.assertEqual(self.app.test_client().get('/api/v1/payments', headers={'Authorization': 'Bearer ' + token}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={'email': 'user@example.test', 'password': 'Test-user-password-123'}).json['error'], 'user_disabled')
        self.assertEqual(admin.post('/api/admin/users/1/access', headers=headers, json={'disabled': True}).status_code, 403)
        self.assertEqual(admin.post(f'/api/admin/users/{self.user_id}/access', headers=headers, json={'disabled': False}).status_code, 200)
        self.assertIsNone(self.store.key_info(self.user_id))

    def test_admin_creates_only_regular_users(self):
        admin, headers = self.admin()
        response = admin.post('/api/admin/users', headers=headers, json={'email': 'created@example.test', 'password': 'New-user-password-123', 'role': 'admin'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['user']['role'], 'user')

    def test_callback_retry_is_scoped_and_preserves_payment(self):
        self.create(notify_url='https://test.bingyun.vip/notify')
        self.notify(self.notification())
        job = self.store.claim_delivery()
        self.store.finish_delivery(job, False, 'unavailable')
        before = self.store.order('TEST_ORDER_0001')
        response = self.client.get('/api/console/callbacks')
        self.assertEqual(response.json['total'], 1)
        other = self.app.test_client()
        session = other.post('/api/auth/register', json={'email': 'other@example.test', 'password': 'Test-other-password-123'}).json
        self.assertEqual(other.post(f'/api/console/callbacks/{job["id"]}/retry', headers={'X-CSRF-Token': session['csrf_token']}).status_code, 404)
        self.assertEqual(self.client.post(f'/api/console/callbacks/{job["id"]}/retry', headers=self.headers).status_code, 200)
        claimed = self.store.claim_delivery()
        self.assertEqual(claimed['id'], job['id'])
        self.assertEqual(claimed['payload'], job['payload'])
        self.store.finish_delivery(claimed, True)
        self.assertEqual(self.client.post(f'/api/console/callbacks/{job["id"]}/retry', headers=self.headers).status_code, 409)
        self.assertEqual(self.store.order('TEST_ORDER_0001'), before)

    def test_legacy_database_migration_preserves_existing_users(self):
        path = self.root / 'old.sqlite3'
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',created_at INTEGER NOT NULL)")
        db.execute("INSERT INTO users VALUES (1,'preserved@example.test','hash','admin',1)")
        db.commit(); db.close()
        migrated = Store(path)
        user = migrated.user_by_email('preserved@example.test')
        self.assertEqual(user['id'], 1)
        self.assertEqual(user['disabled'], 0)


if __name__ == '__main__':
    unittest.main()
