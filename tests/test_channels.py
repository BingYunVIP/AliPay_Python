import base64
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import test_payments
from bypay.alipay_client import AlipayClient
from bypay.key_utils import normalize_private_key, normalize_public_key
from web.app import create_app
from web.channels import ChannelError, ChannelStore


class ChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_payments.PaymentTests.setUpClass.__func__(cls)
        cls.new_app_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.new_alipay_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.new_private = cls.new_app_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        cls.new_public = cls.new_alipay_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    setUp = test_payments.PaymentTests.setUp
    create = test_payments.PaymentTests.create
    notification = test_payments.PaymentTests.notification
    notify = test_payments.PaymentTests.notify

    def admin(self):
        client = self.app.test_client()
        result = client.post('/api/auth/login', json={'email': 'admin@example.test', 'password': 'Test-admin-password-123'}).json
        return client, {'X-CSRF-Token': result['csrf_token'], 'Origin': 'https://pay.example.test'}

    def save(self, admin, headers, name, fields):
        desc = next(c for c in admin.get('/api/admin/channels').json['channels'] if c['id'] == name)
        return admin.put('/api/admin/channels/' + name, headers=headers, json={'version': desc['version'], 'fields': fields})

    def test_configuration_endpoints_require_admin_session_and_csrf(self):
        self.assertEqual(self.client.get('/api/admin/channels').status_code, 403)
        self.assertEqual(self.client.put('/api/admin/channels/alipay', headers=self.headers, json={'version': 0, 'fields': {}}).status_code, 403)
        admin, _ = self.admin()
        self.assertEqual(admin.put('/api/admin/channels/alipay', json={'version': 0, 'fields': {}}).status_code, 403)

    def test_save_is_encrypted_redacted_and_visible_after_restart(self):
        admin, headers = self.admin()
        response = self.save(admin, headers, 'alipay', {'app_id': '2026000000000001'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['channel']['fields']['app_id'], '2026000000000001')
        self.assertTrue(response.json['channel']['secrets']['app_private_key'])
        self.assertNotIn(self.private, json.dumps(response.json))
        database = self.store.path.read_bytes()
        self.assertNotIn(self.private.encode(), database)
        self.assertNotIn(self.private.splitlines()[1].encode(), database)
        self.assertEqual((self.root / 'channel-master.key').stat().st_mode & 0o777, 0o600)
        reopened = ChannelStore(self.store, self.app.extensions['alipay_config'])
        self.assertEqual(reopened.payment_config()['app_id'], '2026000000000001')
        self.assertEqual(reopened.payment_config()['app_private_key'], normalize_private_key(self.private))
        other_app = create_app(dict(self.app.config))
        self.assertEqual(other_app.extensions['channels'].payment_config()['app_id'], '2026000000000001')

    def test_blank_secrets_keep_existing_keys(self):
        admin, headers = self.admin()
        response = self.save(admin, headers, 'alipay', {'app_id': '2026000000000002', 'app_private_key': '', 'alipay_public_key': ''})
        self.assertEqual(response.status_code, 200)
        cfg = self.app.extensions['channels'].payment_config()
        self.assertEqual(cfg['app_private_key'], normalize_private_key(self.private))
        self.assertEqual(cfg['alipay_public_key'], normalize_public_key(self.public))

    def test_invalid_credentials_and_stale_updates_are_rejected(self):
        admin, headers = self.admin()
        for fields in [{'env': 'invalid'}, {'app_id': 'wrong'}, {'seller_id': '123'}, {'app_private_key': 'invalid'}, {'gateway': 'https://evil.test'}]:
            with self.subTest(fields=list(fields)):
                self.assertEqual(self.save(admin, headers, 'alipay', fields).status_code, 400)
        self.assertEqual(self.save(admin, headers, 'alipay', {'app_id': '2026000000000001'}).status_code, 200)
        self.assertEqual(admin.put('/api/admin/channels/alipay', headers=headers, json={'version': 0, 'fields': {'app_id': '2026000000000002'}}).status_code, 409)

    def test_app_public_key_cannot_be_used_as_alipay_public_key(self):
        admin, headers = self.admin()
        own = self.app_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        response = self.save(admin, headers, 'alipay', {'alipay_public_key': own})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['error'], 'alipay_public_key_is_app_key')

    def test_rotated_credentials_apply_to_new_orders_and_old_callbacks_keep_working(self):
        old_order = self.create().json['order']
        admin, headers = self.admin()
        self.assertEqual(self.save(admin, headers, 'alipay', {'app_id': '2026000000000009', 'app_private_key': self.new_private, 'alipay_public_key': self.new_public}).status_code, 200)
        new_order = self.create(out_trade_no='TEST_ORDER_0002').json['order']
        self.assertEqual(parse_qs(urlsplit(new_order['pay_url']).query)['app_id'], ['2026000000000009'])
        self.assertEqual(self.notify(self.notification()).data, b'success')
        params = self.notification(out_trade_no='TEST_ORDER_0002', app_id='2026000000000009', trade_no='20261015000000045678')
        self.assertEqual(self.notify(params).data, b'failure')
        params['sign'] = base64.b64encode(self.new_alipay_key.sign(AlipayClient.build_sign_content(params).encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
        self.assertEqual(self.notify(params).data, b'success')
        self.assertNotIn('channel_version', old_order)

    def test_query_uses_order_configuration_after_channel_changes(self):
        self.create()
        admin, headers = self.admin()
        self.save(admin, headers, 'alipay', {'app_id': '2026000000000009'})
        seen = []
        class QueryClient:
            def __init__(self, cfg): seen.append(cfg)
            def query(self, **_): return {'is_success': False, 'sub_code': 'ACQ.TRADE_NOT_EXIST'}
        self.app.config['ALIPAY_CLIENT_FACTORY'] = QueryClient
        result = self.client.post('/api/v1/payments/TEST_ORDER_0001/sync', headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(seen[0]['app_id'], '2026000000000000')

    def test_other_channel_configuration_saves_without_pretending_adapter_exists(self):
        admin, headers = self.admin()
        values = {
            'wechat': {'app_id': 'wx0123456789abcdef', 'mch_id': '1234567890', 'api_v3_key': 'a' * 32},
            'paypal': {'env': 'sandbox', 'client_id': 'paypal_test_client_id', 'client_secret': 'paypal_test_secret'},
            'stripe': {'publishable_key': 'pk_test_' + 'a' * 24, 'secret_key': 'sk_test_' + 'b' * 24, 'webhook_secret': 'whsec_' + 'c' * 24},
        }
        for name, fields in values.items():
            with self.subTest(channel=name):
                result = self.save(admin, headers, name, fields)
                self.assertEqual(result.status_code, 200)
                self.assertFalse(result.json['channel']['integrated'])
        self.assertEqual(self.create(channel='wechat').status_code, 422)
        logs = json.dumps(admin.get('/api/admin/events').json)
        self.assertNotIn('paypal_test_secret', logs)
        self.assertNotIn('b' * 24, logs)

    def test_lost_master_key_fails_closed_without_replacing_it(self):
        admin, headers = self.admin()
        self.save(admin, headers, 'paypal', {'client_id': 'paypal_test_client_id', 'client_secret': 'paypal_test_secret'})
        key = self.root / 'channel-master.key'
        key.unlink()
        self.assertEqual(admin.get('/api/admin/channels').status_code, 503)
        with self.assertRaises(ChannelError):
            self.app.extensions['channels'].save('paypal', {'client_id': 'another_test_id'}, 0, 1)
        self.assertFalse(key.exists())


if __name__ == '__main__':
    unittest.main()
