"""The independent merchant demo against a real Pay app, with no network.

The demo's own calls to Pay go straight into Pay's WSGI app, WeChat Pay is the in-process gateway of tests/wechat_fake.py,
and Pay's merchant callback is handed to the demo's /notify. Both payment types and both signature versions are covered.
"""
import base64
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from dotenv import set_key

from bypay.alipay_client import AlipayClient
from bypay.epay_protocol import public_pem
from bypay.wechat_client import WechatClient
from web.app import ROOT, create_app
from support import open_gates
from web.webhooks import deliver_one, validate_callback
from wechat_fake import FakeWechat

sys.path.insert(0, str(ROOT / "merchant-demo"))
try:
    import connection as demo_connection
    from app import create_app as create_demo
finally:
    sys.path.remove(str(ROOT / "merchant-demo"))

PAY = "https://pay.example.test"
DEMO = "https://demo.example.test"


def pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


class MerchantDemoAgainstPayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alipay_app = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.alipay_platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        quiet = patch("sys.stdout", new_callable=io.StringIO)  # the demo prints every event it logs
        quiet.start()
        self.addCleanup(quiet.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wechat = FakeWechat()
        dist = self.root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>pay</html>")
        self.pay = create_app({"TESTING": True, "PUBLIC_BASE_URL": PAY, "DATABASE": str(self.root / "pay.sqlite3"), "FRONTEND_DIST": str(dist),
                               "BYPAY_ADMIN_EMAIL": "admin@example.test", "BYPAY_ADMIN_PASSWORD": "Admin-password-123", "ALLOW_REGISTRATION": "true",
                               "BYPAY_CALLBACK_HOSTS": "demo.example.test", "ALIPAY_ENV": "sandbox", "ALIPAY_APP_ID": "2026000000000000", "ALIPAY_SELLER_ID": "2088000000000000",
                               "ALIPAY_PRIVATE_KEY": pem(self.alipay_app), "ALIPAY_PUBLIC_KEY": public_pem(self.alipay_platform),
                               "WECHAT_CLIENT_FACTORY": lambda cfg: WechatClient(cfg, transport=self.wechat.transport)})
        open_gates(self.pay)
        self.store, self.merchants = self.pay.extensions["store"], self.pay.extensions["merchants"]
        self.pay_client = self.pay.test_client()
        # the administrator enters the WeChat Pay parameters in the console
        admin = self.pay.test_client()
        login = admin.post("/api/auth/login", json={"email": "admin@example.test", "password": "Admin-password-123"}).json
        channel = next(c for c in self.pay.extensions["channels"].all() if c["id"] == "wechat")
        saved = admin.put(f"/api/admin/channel-configs/{channel['config_id']}", json={"fields": self.wechat.config(), "version": channel["version"], "revision": channel["revision"]},
                          headers={"Origin": PAY, "X-CSRF-Token": login["csrf_token"]})
        self.assertEqual(saved.status_code, 200, saved.json)
        # the shop is a merchant of Pay, with an MD5 key (V1) and a registered RSA public key (V2)
        user = self.store.register("shop@example.test", "Shop-password-123")["id"]
        self.profile = self.merchants.update(user, {"merchant_public_key": public_pem(self.merchant)})
        # the demo, configured the way its page would configure it
        env = self.root / "demo.env"
        env.touch(mode=0o600)
        values = {"PAY_ORIGIN": PAY, "DEMO_ORIGIN": DEMO, "PAY_MERCHANT_ID": str(self.profile["merchant_id"]), "PAY_API_VERSION": "v1", "PAY_MD5_KEY": self.profile["md5_key"],
                  "PAY_MERCHANT_PRIVATE_KEY": pem(self.merchant), "PAY_PLATFORM_PUBLIC_KEY": self.profile["platform_public_key"]}
        for key, value in values.items():
            set_key(env, key, value)
        self.demo = create_demo({**values, "TESTING": True, "CONFIG_FILE": str(env), "DATABASE": str(self.root / "demo.sqlite3"), "DEMO_PORT": "8100", "DEMO_SETUP_TOKEN": "setup-fixture-token"})
        self.demo_client = self.demo.test_client()
        self.demo_headers = {"Origin": DEMO, "X-Demo-Request": "1", "X-Demo-Setup": "setup-fixture-token"}

        def into_pay(method, url, *, payload=None, api_key=None, form=False):
            parsed = urlsplit(url)
            self.assertEqual(parsed.scheme + "://" + parsed.netloc, PAY)
            response = self.pay_client.open(parsed.path, method=method, data=payload)
            return response.get_json(), response.status_code

        for target in (patch.object(demo_connection, "request_json", into_pay), patch("socket.create_connection", side_effect=AssertionError("Unexpected network"))):
            target.start()
            self.addCleanup(target.stop)

    # ------------------------------------------------------------------ helpers
    def demo_post(self, path, payload):
        return self.demo_client.post(path, json=payload, headers=self.demo_headers, base_url="http://127.0.0.1:8100")

    def state(self, number):
        return next(o for o in self.demo_client.get("/api/state").json["orders"] if o["number"] == number)

    def allow_another_query(self):
        with closing(sqlite3.connect(self.demo.config["DATABASE"])) as db, db:
            db.execute("UPDATE orders SET last_query_at=0")  # the demo rate-limits queries per order

    def order(self, version, pay_type):
        if version == "v2":
            self.assertEqual(self.demo_post("/api/config", {"api_version": "v2"}).status_code, 200)
        response = self.demo_post("/api/orders", {"amount": "0.01", "type": pay_type})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["order"]

    def customer_pays(self, order):
        """What the payer's app does at the payment channel; Pay learns of it from the channel's own signed message."""
        number = order["platform_no"]
        if order["pay_type"] == "wxpay":
            self.wechat.pay(number)
            headers, body = self.wechat.notification(number)
            self.assertEqual(self.pay_client.post(f"/payments/notify/{number}", data=body, headers=headers).status_code, 204)
            return
        data = {"app_id": "2026000000000000", "seller_id": "2088000000000000", "out_trade_no": number, "total_amount": "0.01", "trade_no": "20261017000000" + number[-8:],
                "trade_status": "TRADE_SUCCESS", "sign_type": "RSA2"}
        data["sign"] = base64.b64encode(self.alipay_platform.sign(AlipayClient.build_sign_content(data).encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
        self.assertEqual(self.pay_client.post("/payments/notify", data=data).data, b"success")

    def deliver_callback(self):
        """Pay's worker hands the signed merchant notification to the demo's /notify."""
        answers = []

        def to_demo(url, body, headers, allowed_hosts):
            parsed = validate_callback(url, allowed_hosts)  # Pay's own rule: https and a host on the allow-list
            reply = self.demo_client.get(parsed.path + "?" + body.decode("ascii"), headers={"Accept": "text/plain"})
            answers.append(reply.get_data())
            return reply.status_code == 200 and reply.get_data().strip() == b"success", reply.status_code

        delivered = deliver_one(self.store, self.pay.extensions["webhook_signer"], self.pay.extensions["trace"], self.pay.extensions["callback_hosts"], sender=to_demo)
        self.assertTrue(delivered)
        self.assertEqual(answers, [b"success"])

    # ------------------------------------------------------------------ the flows
    def test_every_payment_type_and_signature_version_is_settled_by_the_signed_callback(self):
        for version in ("v1", "v2"):
            for pay_type, label in (("alipay", "支付宝"), ("wxpay", "微信支付")):
                with self.subTest(version=version, pay_type=pay_type):
                    order = self.order(version, pay_type)
                    self.assertTrue(order["pay_url"].startswith(PAY + "/cashier/"))
                    self.assertEqual((order["status"], order["pay_type"], order["api_version"]), ("WAITING", pay_type, version))
                    with self.store.connect() as db:
                        channel = db.execute("SELECT channel FROM orders WHERE out_trade_no=?", (order["platform_no"],)).fetchone()["channel"]
                    self.assertEqual(channel, {"alipay": "alipay", "wxpay": "wechat"}[pay_type])  # Epay's wxpay is Pay's wechat channel
                    self.customer_pays(order)
                    self.assertEqual(self.merchants.notification(order["platform_no"])["type"], pay_type)  # what Pay tells the merchant
                    self.deliver_callback()
                    paid = self.state(order["number"])
                    self.assertEqual((paid["status"], paid["pay_type"], paid["confirmed_by"]), ("PAID", pay_type, "notify"), label)

    def test_every_payment_type_and_signature_version_is_settled_by_the_server_side_query(self):
        for version in ("v1", "v2"):
            for pay_type in ("alipay", "wxpay"):
                with self.subTest(version=version, pay_type=pay_type):
                    order = self.order(version, pay_type)
                    self.assertEqual(self.demo_post("/api/orders/" + order["number"] + "/sync", {}).json["order"]["status"], "WAITING")  # not paid yet
                    self.customer_pays(order)
                    self.allow_another_query()
                    result = self.demo_post("/api/orders/" + order["number"] + "/sync", {}).json["order"]
                    self.assertEqual((result["status"], result["pay_type"], result["confirmed_by"]), ("PAID", pay_type, "query"))

    def test_a_wechat_order_is_refused_with_a_clear_code_until_pay_has_a_public_https_address(self):
        self.assertEqual(self.order("v1", "wxpay")["status"], "WAITING")  # an https site address, as on the VPS
        with self.store.connect() as db:  # an administrator who still has the plain-http address of a local install
            db.execute("INSERT INTO console_settings VALUES ('origin', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('"http://localhost:888"',))
        refused = self.demo_post("/api/orders", {"amount": "0.01", "type": "wxpay"})
        self.assertEqual((refused.status_code, refused.json["error"]), (502, "wechat_https_required"))
        self.assertEqual(self.demo_client.get("/api/state").json["orders"][0]["status"], "CREATE_FAILED")  # nothing is left waiting for a payment that cannot happen
        self.assertEqual(self.demo_post("/api/orders", {"amount": "0.01", "type": "bogus"}).json["error"], "invalid_pay_type")


if __name__ == "__main__":
    unittest.main()
