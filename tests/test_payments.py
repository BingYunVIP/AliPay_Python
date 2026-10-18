import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from werkzeug.datastructures import MultiDict

from bypay import logger
from bypay.alipay_client import AlipayClient
from bypay.key_utils import normalize_public_key
from bypay.notify_server import NotifyServer
from bypay.order_store import OrderStore
from web.app import create_app
from web.webhooks import deliver_one, send_to_merchant, validate_callback
from support import open_gates


class PaymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alipay_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.app_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public = cls.alipay_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        cls.private = cls.app_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        logger.configure(self.root / "legacy-logs", console=False)
        self.app = create_app({"TESTING": True, "PUBLIC_BASE_URL": "https://pay.example.test", "TEST_MAX_AMOUNT": "10.00",
                               "DATABASE": str(self.root / "pay.sqlite3"), "ALIPAY_APP_ID": "2026000000000000",
                               "ALIPAY_SELLER_ID": "2088000000000000", "ALIPAY_PRIVATE_KEY": self.private,
                               "ALIPAY_PUBLIC_KEY": self.public, "BYPAY_ADMIN_EMAIL": "admin@example.test",
                               "BYPAY_ADMIN_PASSWORD": "Test-admin-password-123", "BYPAY_CALLBACK_HOSTS": "test.bingyun.vip",
                               "ALLOW_REGISTRATION": "true", "BYPAY_EMAIL_DOMAINS": "example.test"})
        open_gates(self.app)
        self.client = self.app.test_client()
        self.store = self.app.extensions["store"]
        response = self.client.post("/api/auth/register", json={"email": "user@example.test", "password": "Test-user-password-123", "role": "admin"})
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json["csrf_token"]
        self.user_id = response.json["user"]["id"]
        self.assertEqual(response.json["user"]["role"], "user")
        self.headers = {"X-CSRF-Token": self.csrf, "Origin": "https://pay.example.test"}
        # Tests must not send network traffic, including SDK queries or merchant callbacks.
        self.network_guard = patch("socket.create_connection", side_effect=AssertionError("Unexpected network request"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def create(self, **fields):
        payload = {"out_trade_no": "TEST_ORDER_0001", "amount": "0.01", "subject": "测试商品", "channel": "alipay", "product": "page", **fields}
        return self.client.post("/api/v1/payments", json=payload, headers=self.headers)

    def notification(self, **fields):
        params = {"app_id": "2026000000000000", "seller_id": "2088000000000000", "out_trade_no": "TEST_ORDER_0001",
                  "total_amount": "0.01", "trade_no": "20261015000000012345", "trade_status": "TRADE_SUCCESS", "sign_type": "RSA2", **fields}
        content = AlipayClient.build_sign_content(params).encode()
        params["sign"] = base64.b64encode(self.alipay_key.sign(content, padding.PKCS1v15(), hashes.SHA256())).decode()
        return params

    def notify(self, params):
        return self.client.post("/payments/notify", data=params)

    def test_order_records_the_real_client_ip_behind_a_local_proxy(self):
        order = self.create().json["order"]
        self.assertEqual(order["client_ip"], "127.0.0.1")
        payload = {"out_trade_no": "TEST_ORDER_0002", "amount": "0.01", "subject": "测试商品", "channel": "alipay", "product": "page"}
        forwarded = self.client.post("/api/v1/payments", json=payload, headers={**self.headers, "X-Forwarded-For": "8.8.8.8, 10.0.0.2"})
        self.assertEqual(forwarded.json["order"]["client_ip"], "8.8.8.8")

    def test_checkout_is_a_real_sdk_signed_url_without_network(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        order = response.json["order"]
        self.assertEqual(order["status"], "CREATED")
        query = parse_qs(urlsplit(order["pay_url"]).query)
        self.assertEqual(query["notify_url"], ["https://pay.example.test/payments/notify"])
        self.assertEqual(query["method"], ["alipay.trade.page.pay"])
        self.assertEqual(json.loads(query["biz_content"][0])["total_amount"], "0.01")
        self.assertIn("sign", query)
        self.assertNotIn("app_id", order)
        sign = base64.b64decode(query.pop("sign")[0])
        content = "&".join(f"{key}={values[0]}" for key, values in sorted(query.items()) if values[0])
        self.app_key.public_key().verify(sign, content.encode(), padding.PKCS1v15(), hashes.SHA256())

    def test_wap_checkout_uses_official_method(self):
        response = self.create(product="wap")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(parse_qs(urlsplit(response.json["order"]["pay_url"]).query)["method"], ["alipay.trade.wap.pay"])

    def test_idempotent_orders_and_conflicts(self):
        first = self.create()
        self.assertEqual(self.create(amount="0.010").status_code, 400)
        second = self.create()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json, second.json)
        self.assertEqual(self.create(amount="0.02").status_code, 409)
        self.assertEqual(len(self.store.orders(self.user_id)), 1)

    def test_money_validation_and_unsupported_products(self):
        for amount in [0.01, "0", "-1", "NaN", "Infinity", "1e1", "0.001", "11.00", "100000000"]:
            with self.subTest(amount=amount):
                self.assertEqual(self.create(amount=amount).status_code, 400)
        self.assertEqual(self.create(channel="paypal").status_code, 422)
        self.assertEqual(self.create(channel="wechat").status_code, 503)  # integrated, but nothing configured yet
        self.assertEqual(self.create(product="unknown").status_code, 422)
        self.assertEqual(self.create(return_url="https://evil.example").status_code, 400)

    def test_verified_callback_and_duplicates_are_idempotent(self):
        self.create()
        first = self.notify(self.notification())
        self.assertEqual(first.data, b"success")
        self.assertEqual(first.content_type, "text/plain; charset=utf-8")
        paid_at = self.store.order("TEST_ORDER_0001")["paid_at"]
        self.assertEqual(self.notify(self.notification()).data, b"success")
        self.assertEqual(self.store.order("TEST_ORDER_0001")["paid_at"], paid_at)
        self.assertEqual(self.store.order("TEST_ORDER_0001")["status"], "PAID")

    def test_wrong_amount_merchant_or_order_never_marks_paid(self):
        self.create()
        for fields in [{"total_amount": "0.02"}, {"total_amount": "NaN"}, {"app_id": "wrong"}, {"seller_id": "wrong"},
                       {"out_trade_no": "UNKNOWN_ORDER"}, {"trade_no": ""}, {"trade_status": "UNKNOWN"}]:
            with self.subTest(fields=fields):
                self.assertEqual(self.notify(self.notification(**fields)).data, b"failure")
                self.assertEqual(self.store.order("TEST_ORDER_0001")["status"], "CREATED")

    def test_invalid_signature_and_sha1_disguised_as_rsa2_rejected(self):
        self.create()
        params = self.notification()
        params["sign"] = "invalid-signature"
        self.assertEqual(self.notify(params).data, b"failure")
        params = self.notification()
        params["sign"] = base64.b64encode(self.alipay_key.sign(AlipayClient.build_sign_content(params).encode(), padding.PKCS1v15(), hashes.SHA1())).decode()
        self.assertEqual(self.notify(params).data, b"failure")
        self.assertEqual(self.store.order("TEST_ORDER_0001")["status"], "CREATED")

    def test_duplicate_form_fields_and_oversized_body_rejected(self):
        self.create()
        params = MultiDict(self.notification())
        params.add("total_amount", "0.02")
        self.assertEqual(self.client.post("/payments/notify", data=params).status_code, 400)
        self.assertEqual(self.client.post("/payments/notify?app_id=other", data=self.notification()).status_code, 400)
        self.assertEqual(self.client.post("/payments/notify", data=b"x" * 40000).status_code, 413)
        self.assertEqual(self.client.post("/prefix/payments/notify", data=self.notification()).status_code, 405)

    def test_paid_order_cannot_be_downgraded_or_rebound(self):
        self.create()
        self.notify(self.notification())
        self.assertEqual(self.notify(self.notification(trade_status="TRADE_CLOSED")).data, b"success")
        self.assertEqual(self.store.order("TEST_ORDER_0001")["status"], "PAID")
        self.assertEqual(self.notify(self.notification(trade_no="20261015000000067890")).data, b"failure")
        self.create(out_trade_no="TEST_ORDER_0002")
        self.assertEqual(self.notify(self.notification(out_trade_no="TEST_ORDER_0002")).data, b"failure")
        self.assertEqual(self.store.order("TEST_ORDER_0002")["status"], "CREATED")

    def test_legacy_alipay_notify_and_return_routes_remain_compatible(self):
        self.create()
        notice = self.notification()
        self.assertEqual(self.client.post('/alipay/notify', data=notice).data, b'success')
        self.assertEqual(self.store.order('TEST_ORDER_0001')['status'], 'PAID')

        self.create(out_trade_no='TEST_ORDER_0002')
        params = self.notification(out_trade_no='TEST_ORDER_0002', trade_no='20261015000000012346')
        returned = self.client.get('/alipay/return?' + urlencode(params))
        self.assertEqual(returned.status_code, 303)
        self.assertTrue(returned.location.startswith('/payment-return#state='))
        self.assertEqual(self.store.order('TEST_ORDER_0002')['status'], 'CREATED')

    def test_browser_return_is_untrusted_and_never_marks_paid(self):
        self.create()
        response = self.client.get("/payment/return?" + urlencode({"out_trade_no": "<script>alert(1)</script>", "trade_status": "TRADE_SUCCESS"}))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "/payment-return")
        self.assertNotIn(b"<script>", response.data)
        self.assertEqual(self.store.order("TEST_ORDER_0001")["status"], "CREATED")

    def test_user_order_log_and_admin_isolation(self):
        self.create()
        self.assertEqual(self.client.get("/api/admin/events").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/diagnostics").status_code, 403)
        other = self.app.test_client()
        other.post("/api/auth/register", json={"email": "other@example.test", "password": "Test-other-password-123"})
        self.assertEqual(other.get("/api/v1/payments/TEST_ORDER_0001").status_code, 404)
        self.assertEqual(other.get("/api/v1/payments").json["orders"], [])
        self.assertEqual(other.get("/api/events").json["events"], [])

    def test_auth_csrf_origin_logout_and_key_rotation(self):
        self.assertEqual(self.client.post("/api/keys").status_code, 403)
        self.assertEqual(self.client.post("/api/keys", headers={**self.headers, "Origin": "https://evil.test"}).status_code, 403)
        first = self.client.post("/api/keys", headers=self.headers).json["api_key"]
        bearer = self.app.test_client()
        self.assertEqual(bearer.get("/api/v1/payments", headers={"Authorization": "Bearer " + first}).status_code, 200)
        self.client.post("/api/keys", headers=self.headers)
        self.assertEqual(bearer.get("/api/v1/payments", headers={"Authorization": "Bearer " + first}).status_code, 401)
        self.client.post("/api/auth/logout", headers=self.headers)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_cookies_security_and_single_admin(self):
        response = self.client.post("/api/auth/login", json={"email": "admin@example.test", "password": "Test-admin-password-123"})
        cookie = response.headers["Set-Cookie"]
        for expected in ["__Host-bypay_session=", "HttpOnly", "Secure", "SameSite=Lax", "Path=/"]:
            self.assertIn(expected, cookie)
        self.assertEqual(self.client.get("/api/admin/diagnostics").status_code, 200)
        self.store.bootstrap("second@example.test", "Other-test-password-123")
        self.assertIsNone(self.store.user_by_email("second@example.test"))
        self.assertEqual(self.client.get("/api/auth/me").headers["Cache-Control"], "no-store")

    def test_local_logs_explain_steps_and_redact_payer_signature(self):
        self.create()
        params = self.notification(buyer_id="sensitive-payer-12345", buyer_logon_id="sensitive@example.test")
        self.notify(params)
        logs = (self.root / "logs/payment.jsonl").read_text()
        self.assertNotIn(params["sign"], logs)
        self.assertNotIn(params["buyer_id"], logs)
        self.assertNotIn(params["buyer_logon_id"], logs)
        for stage in ["create.received", "sdk.link", "create.saved", "notify.received", "notify.signature", "notify.accepted"]:
            self.assertIn(stage, logs)

    def test_manual_query_validates_returned_order_and_amount(self):
        self.create()
        good = {"is_success": True, **self.notification()}
        with patch.object(AlipayClient, "query", return_value={**good, "total_amount": "0.02"}):
            response = self.client.post("/api/v1/payments/TEST_ORDER_0001/sync", headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.store.order("TEST_ORDER_0001")["status"], "CREATED")
        # A query retry observes the same cooldown as the background worker.
        retry_at = self.store.payment_check("TEST_ORDER_0001")["locked_until"] + 1
        with patch("web.store.time.time", return_value=retry_at), patch.object(AlipayClient, "query", return_value=good):
            response = self.client.post("/api/v1/payments/TEST_ORDER_0001/sync", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["order"]["status"], "PAID")

    def test_callback_allowlist_and_fixed_alipay_notify_url(self):
        for url in ["http://test.bingyun.vip/notify", "https://localhost/notify", "https://test.bingyun.vip@evil.test/notify", "https://test.bingyun.vip:8443/notify", "https://test.bingyun.vip/notify?x=1"]:
            self.assertEqual(self.create(notify_url=url).status_code, 400)
        response = self.create(notify_url="https://test.bingyun.vip/notify")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(parse_qs(urlsplit(response.json["order"]["pay_url"]).query)["notify_url"], ["https://pay.example.test/payments/notify"])

    def test_callback_outbox_deduplicates_and_signs_merchant_event(self):
        self.create(notify_url="https://test.bingyun.vip/notify")
        self.notify(self.notification())
        self.notify(self.notification())
        received = []
        signer = self.app.extensions["webhook_signer"]
        def receiver(url, body, headers, hosts):
            self.assertEqual(url, "https://test.bingyun.vip/notify")
            signer.key.public_key().verify(base64.b64decode(headers["X-ByPay-Signature"]), headers["X-ByPay-Timestamp"].encode() + b"." + body)
            received.append(json.loads(body))
            return True, 200
        self.assertTrue(deliver_one(self.store, signer, self.app.extensions["trace"], {"test.bingyun.vip"}, receiver))
        self.assertFalse(deliver_one(self.store, signer, self.app.extensions["trace"], {"test.bingyun.vip"}, receiver))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["event"], "payment.paid")

    def test_callback_retry_is_durable_and_private_addresses_blocked(self):
        self.create(notify_url="https://test.bingyun.vip/notify")
        self.notify(self.notification())
        deliver_one(self.store, self.app.extensions["webhook_signer"], self.app.extensions["trace"], {"test.bingyun.vip"}, lambda *_: (False, 503))
        with self.store.connect() as db:
            job = db.execute("SELECT * FROM outbox").fetchone()
            self.assertEqual(job["status"], "pending")
            self.assertEqual(job["attempts"], 1)
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                send_to_merchant("https://test.bingyun.vip/notify", b"{}", {}, {"test.bingyun.vip"})

    def test_legacy_cannot_skip_signatures_or_lose_order_ledger(self):
        with self.assertRaises(ValueError):
            NotifyServer("", None, 0, debug_skip_verify=True)
        orders = OrderStore(self.root / "legacy-orders.json")
        orders.add("ORDER_LEGACY_01", "test", "0.01")
        with self.assertRaises(ValueError):
            orders.mark_paid("ORDER_LEGACY_01", "TRADE_1234567", {"out_trade_no": "ORDER_LEGACY_01", "total_amount": "0.02"})
        orders.path.write_text("broken ledger")
        with self.assertRaises(RuntimeError):
            orders.add("ORDER_LEGACY_02", "test", "0.01")
        self.assertEqual(orders.path.read_text(), "broken ledger")

    def test_pkcs1_public_key_is_normalized_correctly(self):
        raw = self.alipay_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.PKCS1).decode()
        self.assertEqual(normalize_public_key(raw), normalize_public_key(self.public))


if __name__ == "__main__":
    unittest.main()
