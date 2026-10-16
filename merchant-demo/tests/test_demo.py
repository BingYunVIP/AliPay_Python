import base64
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import create_app


class MerchantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        public_file = root / "public.pem"
        public_file.write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        self.db = root / "merchant.sqlite3"
        self.app = create_app({"TESTING": True, "PAY_ORIGIN":"https://pay.bingyun.vip", "DEMO_ORIGIN":"https://test.bingyun.vip", "DATABASE": str(self.db), "PAY_PUBLIC_KEY_FILE": str(public_file), "PAY_API_KEY": "byp_test_placeholder"})
        self.client = self.app.test_client()
        self.headers = {"Origin": "https://test.bingyun.vip", "X-Demo-Request": "1"}
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("INSERT INTO orders(number,amount,status,created_at) VALUES ('DEMO_ORDER_01','0.01','WAITING',?)", (int(time.time()),))
        self.guard = patch("socket.create_connection", side_effect=AssertionError("Unexpected network request"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def event(self, **changes):
        return {"id": "event_001", "event": "payment.paid", "out_trade_no": "DEMO_ORDER_01", "trade_no": "202610150000123456", "amount": "0.01", "currency": "CNY", "channel": "alipay", "status": "PAID", "paid_at": int(time.time()), **changes}

    def notify(self, event, stamp=None, corrupt=False):
        body = json.dumps(event, separators=(",", ":")).encode()
        stamp = str(stamp or int(time.time()))
        signature = self.key.sign(stamp.encode() + b"." + body)
        headers = {"X-ByPay-Timestamp": stamp, "X-ByPay-Event-Id": event["id"], "X-ByPay-Signature": "invalid" if corrupt else base64.b64encode(signature).decode()}
        return self.client.post("/notify", data=body, content_type="application/json", headers=headers)

    def status(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            return connection.execute("SELECT status FROM orders WHERE number='DEMO_ORDER_01'").fetchone()[0]

    def test_open_console_and_same_origin_validation(self):
        with self.client.get("/") as response:
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("WWW-Authenticate", response.headers)
        with self.client.get("/", headers=self.headers) as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/state", headers=self.headers).json["callback_url"], "https://test.bingyun.vip/notify")
        self.assertEqual(self.client.post("/api/orders", headers={**self.headers, "Origin": "https://evil.test"}, json={"amount": "0.01"}).status_code, 403)

    def test_verified_callback_and_duplicate_are_idempotent(self):
        event = self.event()
        self.assertEqual(self.notify(event).data, b"success")
        self.assertEqual(self.status(), "PAID")
        self.assertEqual(self.notify(event).data, b"success")
        with closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM receipts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM logs WHERE stage='notify.verified'").fetchone()[0], 1)
        records = [json.loads(line) for line in (self.db.parent / "logs/demo.jsonl").read_text().splitlines()]
        self.assertEqual(len([row for row in records if row['stage'] == 'notify.verified']), 1)

    def test_direct_ip_origin_can_submit_but_other_addresses_cannot(self):
        with patch('app.local_hosts', return_value={'127.0.0.1', '192.168.1.20'}):
            app = create_app(dict(self.app.config))
        client = app.test_client()
        # Invalid amounts are rejected after origin validation, without contacting Pay
        for origin in ('http://192.168.1.20:8100', 'http://127.0.0.1:8100'):
            response = client.post('/api/orders', json={'amount':'0'}, headers={**self.headers,'Origin':origin})
            self.assertEqual(response.status_code, 400)
        for origin in ('http://192.168.1.21:8100', 'http://192.168.1.20:9999'):
            response = client.post('/api/orders', json={'amount':'0'}, headers={**self.headers,'Origin':origin})
            self.assertEqual(response.status_code, 403)

    def test_bad_signature_stale_timestamp_and_bad_amount_rejected(self):
        self.assertEqual(self.notify(self.event(), corrupt=True).status_code, 400)
        self.assertEqual(self.notify(self.event(), stamp=int(time.time()) - 601).status_code, 400)
        for fields in [{"amount": "0.02"}, {"amount": "NaN"}, {"currency": "USD"}, {"channel": "wechat"}, {"trade_no": None}, {"paid_at": []}, {"out_trade_no": "OTHER_ORDER"}]:
            with self.subTest(fields=fields):
                self.assertEqual(self.notify(self.event(**fields)).status_code, 400)
        self.assertEqual(self.status(), "WAITING")

    def test_event_id_cannot_be_reused_with_different_content(self):
        self.notify(self.event())
        self.assertEqual(self.notify(self.event(trade_no="202610150000654321")).status_code, 400)

    def test_order_creation_sends_merchant_notify_to_pay(self):
        received = []
        real_client = httpx.Client
        def upstream(request):
            received.append(request)
            return httpx.Response(201, json={"order": {"pay_url": "https://openapi.alipay.com/gateway.do?sign=example", "status": "CREATED", "amount": "0.01"}})
        transport = httpx.MockTransport(upstream)
        with patch("pay_http.httpx.Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
            response = self.client.post("/api/orders", headers=self.headers, json={"amount": "0.01", "product": "page"})
            self.assertEqual(response.status_code, 201)
            request = received[0]
            self.assertEqual(str(request.url), "https://pay.bingyun.vip/api/v1/payments")
            self.assertEqual(json.loads(request.content)["notify_url"], "https://test.bingyun.vip/notify")
            self.assertTrue(request.headers["User-Agent"].startswith("python-httpx/"))
        logs = self.client.get("/api/state", headers=self.headers).json["logs"]
        self.assertNotIn("byp_test_placeholder", json.dumps(logs))


if __name__ == "__main__":
    unittest.main()
