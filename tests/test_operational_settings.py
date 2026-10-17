import json
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import test_payments
from web.app import create_app
from web.webhooks import deliver_one


class OperationalSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): test_payments.PaymentTests.setUpClass.__func__(cls)
    setUp = test_payments.PaymentTests.setUp
    create = test_payments.PaymentTests.create
    notification = test_payments.PaymentTests.notification
    notify = test_payments.PaymentTests.notify

    def admin(self):
        if hasattr(self, '_admin'): return self._admin
        client = self.app.test_client()
        result = client.post('/api/auth/login', json={'email': 'admin@example.test', 'password': 'Test-admin-password-123'}).json
        self._admin = client, {'X-CSRF-Token': result['csrf_token'], 'Origin': 'https://pay.example.test'}
        return self._admin

    def save(self, **values):
        admin, headers = self.admin()
        return admin.put('/api/admin/settings', json=values, headers=headers)

    def test_default_ceiling_is_3000_and_new_orders_enforce_it(self):
        cfg = dict(self.app.config)
        cfg.pop('TEST_MAX_AMOUNT')
        self.app = create_app(cfg)
        self.assertEqual(self.app.test_client().get('/api/capabilities').json['max_amount'], '3000.00')
        self.client, self.headers = self.admin()
        self.assertEqual(self.create(amount='3000.00').status_code, 201)
        self.assertEqual(self.create(out_trade_no='LARGE_ORDER_0002', amount='3000.01').status_code, 400)

    def test_edit_routing_is_persistent_used_by_new_orders_and_keeps_old_links(self):
        old = self.create().json['order']
        saved = self.save(origin='https://pay.new.example', notify_url='https://notify.example/alipay/notify',
                          return_url='https://pay.new.example/alipay/return', callback_hosts=['merchant.example'])
        self.assertEqual(saved.status_code, 200)
        changed = self.create(out_trade_no='TEST_ORDER_0002', notify_url='https://merchant.example/paid')
        self.assertEqual(changed.status_code, 201)
        query = parse_qs(urlsplit(changed.json['order']['pay_url']).query)
        self.assertEqual(query['notify_url'], ['https://notify.example/alipay/notify'])
        self.assertTrue(query['return_url'][0].startswith('https://pay.new.example/alipay/return?state='))
        self.assertEqual(self.store.order(old['out_trade_no'])['pay_url'], old['pay_url'])
        self.assertEqual(self.notify(self.notification()).data, b'success')
        restarted = create_app(dict(self.app.config))
        self.assertEqual(restarted.extensions['site_settings'].read()['origin'], 'https://pay.new.example')
        self.assertEqual(set(restarted.extensions['callback_hosts']), {'merchant.example'})
        self.assertEqual(self.create(out_trade_no='TEST_ORDER_0003', notify_url='https://test.bingyun.vip/notify').status_code, 400)

    def test_saved_public_domain_does_not_break_local_login_or_csrf(self):
        self.app = create_app({**self.app.config, 'PUBLIC_BASE_URL': 'http://localhost:8000', 'BYPAY_RUN_MODE': 'local'})
        admin = self.app.test_client()
        session = admin.post('/api/auth/login', json={'email': 'admin@example.test', 'password': 'Test-admin-password-123'}, headers={'Origin': 'http://127.0.0.1:8000'}).json
        response = admin.put('/api/admin/settings', json={'origin': 'https://pay.bingyun.vip'}, headers={'Origin': 'http://127.0.0.1:8000', 'X-CSRF-Token': session['csrf_token']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(admin.put('/api/admin/settings', json={'poll_interval_seconds': 20}, headers={'Origin': 'http://localhost:8000', 'X-CSRF-Token': session['csrf_token']}).status_code, 200)
        self.assertEqual(admin.put('/api/admin/settings', json={'poll_interval_seconds': 20}).status_code, 403)
        self.assertEqual(admin.put('/api/admin/settings', json={'poll_interval_seconds': 20}, headers={'Origin': 'https://evil.example', 'X-CSRF-Token': session['csrf_token']}).status_code, 403)

    def test_invalid_settings_are_atomic_and_field_errors_identify_the_problem(self):
        invalid = [({'origin': 'https://user:pass@evil.example'}, 'origin'),
                   ({'origin': 'https://[bad'}, 'origin'),
                   ({'return_url': 'https://[bad/alipay/return'}, 'return_url'),
                   ({'origin': 'http://localhost:8000'}, 'origin'),
                   ({'notify_url': 'https://test.bingyun.vip/notify'}, 'notify_url'),
                   ({'notify_url': 'http://localhost:8000/alipay/notify'}, 'notify_url'),
                   ({'return_url': 'https://pay.example/alipay/return?state=bad'}, 'return_url'),
                   ({'callback_hosts': ['127.0.0.1', '*.example']}, 'callback_hosts'),
                   ({'order_timeout_minutes': 0}, 'order_timeout_minutes'),
                   ({'poll_interval_seconds': True}, 'poll_interval_seconds'),
                   ({'query_window_minutes': 1}, 'query_window_minutes'),
                   ({'callback_timeout_seconds': 100}, 'callback_timeout_seconds'),
                   ({'callback_first_delay_seconds': -1}, 'callback_first_delay_seconds'),
                   ({'callback_retry_interval_seconds': 2}, 'callback_retry_interval_seconds'),
                   ({'callback_retry_interval_seconds': '30'}, 'callback_retry_interval_seconds'),
                   ({'email_domains': ['*.']}, 'email_domains')]
        for payload, field in invalid:
            with self.subTest(payload=payload):
                response = self.save(**payload, registration=False)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json['field'], field)
                self.assertTrue(self.app.extensions['site_settings'].read()['registration'])

    def test_registration_only_accepts_the_configured_email_domains(self):
        from web.settings import EMAIL_DOMAINS_DEFAULT
        self.assertEqual(EMAIL_DOMAINS_DEFAULT, ['qq.com', 'gmail.com', '*.edu.cn', '163.com'])
        self.assertEqual(self.app.extensions['site_settings'].read()['email_domains'], ['example.test'])
        self.assertEqual(self.save(registration=True).status_code, 200)
        client = self.app.test_client()
        self.assertEqual(client.post('/api/auth/register', json={'email': 'user@outlook.com', 'password': 'Test-password-123'}).json['error'], 'email_domain_not_allowed')
        self.assertEqual(client.post('/api/auth/register', json={'email': 'second@example.test', 'password': 'Test-password-123'}).status_code, 200)
        self.assertEqual(self.save(email_domains=['*.example.test']).status_code, 200)
        self.assertEqual(client.post('/api/auth/register', json={'email': 'user@shop.example.test', 'password': 'Test-password-123'}).status_code, 200)
        self.assertEqual(self.save(email_domains=['example.org']).status_code, 200)
        self.assertEqual(client.post('/api/auth/register', json={'email': 'user@example.test', 'password': 'Test-password-123'}).json['error'], 'email_domain_not_allowed')

    def test_version_conflict_and_admin_permission(self):
        self.assertEqual(self.client.put('/api/admin/settings', json={'order_timeout_minutes': 30}, headers=self.headers).status_code, 403)
        self.assertEqual(self.save(version=0, order_timeout_minutes=30).status_code, 200)
        self.assertEqual(self.save(version=0, order_timeout_minutes=40).status_code, 409)
        self.assertEqual(self.store.policy()['order_timeout_minutes'], 30)

    def test_order_deadline_is_in_signed_sdk_parameters_and_remains_pinned(self):
        self.save(order_timeout_minutes=25, poll_interval_seconds=23)
        response = self.create()
        order = response.json['order']
        content = json.loads(parse_qs(urlsplit(order['pay_url']).query)['biz_content'][0])
        self.assertEqual(content['timeout_express'], '25m')
        self.assertIn('time_expire', content)
        self.assertEqual(order['expires_at'] - order['created_at'], 1500)
        self.save(order_timeout_minutes=10)
        self.assertEqual(self.store.order(order['out_trade_no'])['expires_at'], order['expires_at'])
        job = self.store.claim_payment_check(order['out_trade_no'])
        with patch('web.store.time.time', return_value=order['created_at'] + 1):
            self.store.finish_payment_check(job, 'waiting')
        check = self.store.payment_check(order['out_trade_no'])
        self.assertEqual(check['next_check'], order['created_at'] + 24)
        self.assertEqual(check['locked_until'], order['created_at'] + 24)

    def test_callback_delay_timeout_retries_are_executed(self):
        self.save(callback_first_delay_seconds=9, callback_timeout_seconds=17, callback_retry_interval_seconds=11)
        self.create(notify_url='https://test.bingyun.vip/notify')
        now = int(time.time())
        with patch('web.store.time.time', return_value=now):
            self.notify(self.notification())
            self.assertIsNone(self.store.claim_delivery())
        with patch('web.store.time.time', return_value=now + 10), patch('web.webhooks.send_to_merchant', return_value=(False, 503)) as sender:
            self.assertTrue(deliver_one(self.store, self.app.extensions['webhook_signer'], self.app.extensions['trace'], {'test.bingyun.vip'}))
            self.assertEqual(sender.call_args.kwargs['timeout'], 17)
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM outbox').fetchone()
            self.assertEqual(row['next_attempt'], now + 21)
            self.assertEqual(row['attempts'], 1)
        # 固定间隔重试，最多 8 次：第 9 次失败后不再排队
        with self.store.connect() as db: db.execute('UPDATE outbox SET attempts=8')
        with patch('web.store.time.time', return_value=now + 22):
            job = self.store.claim_delivery()
            self.assertEqual(self.store.finish_delivery(job, False), 'failed')
            self.assertEqual(self.store.policy()['callback_retry_interval_seconds'], 11)

    def test_verification_window_limits_automatic_queries_but_allows_manual_recovery(self):
        self.save(query_window_minutes=15)
        order = self.create().json['order']
        with patch('web.store.time.time', return_value=order['created_at'] + 901):
            self.assertIsNone(self.store.claim_payment_check())
            self.assertIsNotNone(self.store.claim_payment_check(order['out_trade_no']))


if __name__ == '__main__': unittest.main()
