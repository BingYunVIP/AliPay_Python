import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bypay import logger
from bypay.epay_protocol import public_pem, signed
from bypay.wechat_client import WechatClient
from web.app import create_app
from web.webhooks import deliver_one
from wechat_fake import FakeWechat, pem_private

OPENID = "oFixtureOpenId0123456789ab"


class WechatCase(unittest.TestCase):
    """The whole WeChat Pay path against an in-process gateway: creation, notices, queries, refunds and merchants."""

    def setUp(self):
        self.fake = FakeWechat()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        logger.configure(self.root / "logs", console=False)  # a test run leaves nothing in data/logs
        dist = self.root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>pay</html>")
        self.app = create_app({"TESTING": True, "PUBLIC_BASE_URL": "https://pay.example.test", "DATABASE": str(self.root / "pay.sqlite3"), "FRONTEND_DIST": str(dist),
                               "BYPAY_ADMIN_EMAIL": "admin@example.test", "BYPAY_ADMIN_PASSWORD": "Admin-password-123", "ALLOW_REGISTRATION": "true",
                               "BYPAY_CALLBACK_HOSTS": "merchant.example", "WECHAT_CLIENT_FACTORY": lambda cfg: WechatClient(cfg, transport=self.fake.transport)})
        self.store = self.app.extensions["store"]
        self.channels = self.app.extensions["channels"]
        self.admin = self.app.test_client()
        login = self.admin.post("/api/auth/login", json={"email": "admin@example.test", "password": "Admin-password-123"}).json
        self.admin_headers = {"Origin": "https://pay.example.test", "X-CSRF-Token": login["csrf_token"]}
        self.client = self.app.test_client()
        registered = self.client.post("/api/auth/register", json={"email": "user@example.test", "password": "User-password-123"}).json
        self.headers = {"Origin": "https://pay.example.test", "X-CSRF-Token": registered["csrf_token"]}
        self.user = registered["user"]["id"]
        self.config_id = next(c["config_id"] for c in self.channels.all() if c["id"] == "wechat")
        self.save_config()
        guard = patch("socket.create_connection", side_effect=AssertionError("Unexpected network"))
        guard.start()
        self.addCleanup(guard.stop)
        self.number = 0

    def save_config(self, **overrides):
        describe = next(c for c in self.channels.all() if c["config_id"] == self.config_id)
        return self.admin.put(f"/api/admin/channel-configs/{self.config_id}", json={"fields": {**self.fake.config(), **overrides}, "version": describe["version"], "revision": describe["revision"]}, headers=self.admin_headers)

    def create(self, client=None, headers=None, number=None, **fields):
        self.number += 1
        body = {"out_trade_no": number or f"WX_ORDER_{self.number:04d}", "amount": "0.01", "subject": "微信测试商品", "channel": "wechat", "product": "page", **fields}
        return (client or self.client).post("/api/v1/payments", json=body, headers=headers or self.headers)

    def order(self, number):
        return self.store.order(number)

    def notice(self, number, **kw):
        headers, body = self.fake.notification(number, **kw)
        return self.client.post(f"/wechat/notify/{number}", data=body, headers=headers)


class WechatCreationTests(WechatCase):
    def test_native_order_is_created_once_and_replayed(self):
        response = self.create(notify_url="https://merchant.example/notify")
        self.assertEqual(response.status_code, 201, response.json)
        order = response.json["order"]
        self.assertEqual((order["channel"], order["payment_payload"]["kind"], order["pay_url"]), ("wechat", "wechat_native", ""))
        self.assertRegex(order["cashier_qr"], r"^weixin://wxpay/bizpayurl\?pr=")
        self.assertEqual(order["payment_payload"]["code_url"], order["cashier_qr"])
        sent = json.loads(self.fake.calls[0][2])
        self.assertEqual((sent["out_trade_no"], sent["amount"]["total"], sent["notify_url"]), (order["out_trade_no"], 1, f"https://pay.example.test/wechat/notify/{order['out_trade_no']}"))
        self.assertRegex(sent["time_expire"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+08:00$")
        stored = self.store.order(order["out_trade_no"])
        self.assertEqual((stored["app_id"], stored["seller_id"], stored["channel"]), (self.fake.app_id, self.fake.mch_id, "wechat"))
        again = self.create(number=order["out_trade_no"], notify_url="https://merchant.example/notify")
        self.assertEqual((again.status_code, again.json["order"]["cashier_qr"]), (200, order["cashier_qr"]))
        self.assertEqual(len(self.fake.calls), 1)  # replayed from our database, WeChat is not called twice
        different = self.create(number=order["out_trade_no"], amount="0.02", notify_url="https://merchant.example/notify")
        self.assertEqual(different.status_code, 409)

    def test_qr_endpoint_renders_the_wechat_code_in_wechat_colours_with_the_logo(self):
        number = self.create().json["order"]["out_trade_no"]
        svg = self.client.get(f"/api/v1/payments/{number}/qr.svg")
        self.assertEqual(svg.status_code, 200)
        self.assertIn("#087c49", svg.text)
        self.assertIn("currentColor", svg.text)
        self.assertLess(int(svg.text.split('viewBox="0 0 ')[1].split(" ")[0]), 70)
        self.fake.pay(number)
        self.assertEqual(self.notice(number).status_code, 204)
        self.assertEqual(self.client.get(f"/api/v1/payments/{number}/qr.svg").status_code, 409)

    def test_wechat_limits_and_requirements_are_reported_clearly(self):
        self.assertEqual(self.create(number="W" * 33).json["error"], "wechat_order_number_too_long")
        self.assertEqual(self.create(number="W" * 32).status_code, 201)
        self.assertEqual(self.create(product="mini").json["error"], "buyer_identity_required")
        self.assertEqual(self.create(product="mini", buyer_id="2088123456789012").json["error"], "invalid_buyer_identity")
        self.assertEqual(self.create(config_id=1).status_code, 400)  # config 1 is the Alipay configuration
        self.save_config(app_id="wx9999999999999999")  # an AppID the merchant number is not bound to
        failed = self.create()
        self.assertEqual((failed.status_code, failed.json["error"]), (502, "wechat_app_id_mismatch"))

    def test_plain_http_site_address_cannot_create_wechat_orders(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO console_settings VALUES ('origin', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps("http://localhost:888"),))
        response = self.create()
        self.assertEqual((response.status_code, response.json["error"]), (409, "wechat_https_required"))
        self.assertEqual(self.fake.calls, [])

    def test_provider_failure_keeps_the_order_for_a_retry_and_exposes_only_a_code(self):
        self.fake.fail_next = (500, "SYSTEM_ERROR")
        failed = self.create(number="WX_RETRY_0001")
        self.assertEqual((failed.status_code, failed.json["error"]), (502, "wechat_system_error"))
        retry = self.create(number="WX_RETRY_0001")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json["order"]["payment_payload"]["kind"], "wechat_native")
        self.assertEqual(len(self.fake.orders), 1)

    def test_app_and_miniprogram_products_return_signed_invocation_parameters(self):
        app = self.create(product="app", amount="9.90").json["order"]
        self.assertEqual(app["payment_payload"]["kind"], "wechat_app")
        params = app["payment_payload"]["params"]
        self.assertEqual((params["partnerid"], params["package"], params["appid"]), (self.fake.mch_id, "Sign=WXPay", self.fake.app_id))
        self.assertEqual(json.loads(self.fake.calls[-1][2])["amount"]["total"], 990)
        mini = self.create(product="mini", buyer_open_id=OPENID).json["order"]
        self.assertEqual(mini["payment_payload"]["kind"], "wechat_jsapi")
        self.assertEqual((mini["payment_payload"]["params"]["signType"], mini["payment_payload"]["params"]["package"][:10]), ("RSA", "prepay_id="))
        self.assertEqual(self.fake.orders[mini["out_trade_no"]]["openid"], OPENID)
        self.assertEqual(mini["pay_url"], "")

    def test_a_wechat_order_never_shares_its_number_with_an_alipay_request(self):
        number = self.create().json["order"]["out_trade_no"]
        self.assertEqual(self.client.post("/api/v1/payments", json={"out_trade_no": number, "amount": "0.01", "subject": "微信测试商品", "channel": "alipay", "product": "page"}, headers=self.headers).status_code, 409)


class WechatH5Tests(WechatCase):
    def test_h5_link_redirects_with_a_return_address_and_is_renewed_when_stale(self):
        order = self.create(product="wap").json["order"]
        number = order["out_trade_no"]
        self.assertRegex(order["pay_url"], r"^https://pay\.example\.test/m/[A-Za-z0-9_-]{12}$")
        sent = json.loads(self.fake.calls[0][2])
        self.assertEqual(sent["scene_info"]["h5_info"]["type"], "Wap")
        self.assertTrue(sent["scene_info"]["payer_client_ip"])
        phone = self.app.test_client().get(urlsplit(order["pay_url"]).path)
        self.assertEqual(phone.status_code, 302)
        target = urlsplit(phone.headers["Location"])
        self.assertEqual((target.hostname, target.path), ("wx.tenpay.com", "/cgi-bin/mmpayweb-bin/checkmweb"))
        redirect = parse_qs(target.query)["redirect_url"][0]
        self.assertRegex(redirect, r"^https://pay\.example\.test/wechat/return\?state=[A-Za-z0-9_-]{43}$")
        self.assertEqual(len(self.fake.calls), 1)  # a fresh link is used as it is
        # the link only lives five minutes: a stale one is renewed with the order's original parameters
        stored = self.order(number)
        payload = json.loads(stored["payment_payload"])
        payload["h5_at"] -= 600
        with self.store.connect() as db: db.execute("UPDATE orders SET payment_payload=? WHERE out_trade_no=?", (json.dumps(payload), number))
        renewed = self.app.test_client().get(urlsplit(order["pay_url"]).path)
        self.assertEqual(renewed.status_code, 302)
        self.assertEqual(len(self.fake.calls), 2)
        self.assertEqual(json.loads(self.fake.calls[1][2])["scene_info"], sent["scene_info"])
        self.assertNotEqual(renewed.headers["Location"].split("&redirect_url")[0], phone.headers["Location"].split("&redirect_url")[0])
        # the return page works from WeChat's redirect
        back = self.app.test_client().get(urlsplit(redirect).path + "?" + urlsplit(redirect).query)
        self.assertEqual((back.status_code, back.headers["Location"]), (303, "/payment-return#state=" + parse_qs(urlsplit(redirect).query)["state"][0]))

    def test_h5_link_that_cannot_be_renewed_says_so(self):
        order = self.create(product="wap").json["order"]
        number = order["out_trade_no"]
        payload = json.loads(self.order(number)["payment_payload"])
        payload["h5_at"] -= 600
        with self.store.connect() as db: db.execute("UPDATE orders SET payment_payload=? WHERE out_trade_no=?", (json.dumps(payload), number))
        self.fake.fail_next = (403, "OUT_TRADE_NO_USED")
        gone = self.app.test_client().get(urlsplit(order["pay_url"]).path)
        self.assertEqual(gone.status_code, 410)
        self.assertNotIn("Location", gone.headers)
        with self.store.connect() as db: db.execute("UPDATE orders SET expires_at=1 WHERE out_trade_no=?", (number,))
        self.assertEqual(self.app.test_client().get(urlsplit(order["pay_url"]).path).status_code, 410)

    def test_the_return_page_does_not_treat_a_wechat_order_like_an_alipay_one(self):
        order = self.create(product="wap").json["order"]
        number = order["out_trade_no"]
        legacy = self.app.test_client().get("/wechat/return", query_string={"out_trade_no": number, "app_id": self.order(number)["app_id"]})
        self.assertEqual((legacy.status_code, legacy.headers["Location"]), (303, "/payment-return"))  # no receipt, no error, nothing to learn

    def test_a_link_that_keeps_failing_to_renew_does_not_hammer_wechat_pay(self):
        order = self.create(product="wap").json["order"]
        number = order["out_trade_no"]
        payload = json.loads(self.order(number)["payment_payload"])
        payload["h5_at"] -= 600
        with self.store.connect() as db: db.execute("UPDATE orders SET payment_payload=? WHERE out_trade_no=?", (json.dumps(payload), number))
        calls = len(self.fake.calls)
        self.fake.fail_next = (500, "SYSTEM_ERROR")
        visits = [self.app.test_client().get(urlsplit(order["pay_url"]).path).status_code for _ in range(5)]
        self.assertEqual(visits, [410] * 5)
        self.assertEqual(len(self.fake.calls), calls + 1)  # one attempt, then a pause
        payload = json.loads(self.order(number)["payment_payload"])
        payload["h5_failed_at"] -= 60
        with self.store.connect() as db: db.execute("UPDATE orders SET payment_payload=? WHERE out_trade_no=?", (json.dumps(payload), number))
        self.assertEqual(self.app.test_client().get(urlsplit(order["pay_url"]).path).status_code, 302)  # after the pause it renews again
        self.assertEqual(len(self.fake.calls), calls + 2)

    def test_wechat_qr_for_a_mobile_order_encodes_the_short_link(self):
        order = self.create(product="wap").json["order"]
        with patch("web.qrcode.payment_qr_svg", return_value="<svg/>") as encode:
            self.assertEqual(self.client.get(f"/api/v1/payments/{order['out_trade_no']}/qr.svg").status_code, 200)
        self.assertEqual(encode.call_args.args, (order["pay_url"], "wechat"))


class WechatNoticeTests(WechatCase):
    def setUp(self):
        super().setUp()
        self.number_paid = self.create(notify_url="https://merchant.example/notify").json["order"]["out_trade_no"]

    def test_valid_notice_marks_the_order_paid_once_and_queues_one_merchant_callback(self):
        self.assertEqual(self.order(self.number_paid)["status"], "CREATED")
        transaction = self.fake.pay(self.number_paid)
        first = self.notice(self.number_paid)
        self.assertEqual((first.status_code, first.data), (204, b""))
        order = self.order(self.number_paid)
        self.assertEqual((order["status"], order["trade_no"], order["provider_status"]), ("PAID", transaction, "SUCCESS"))
        self.assertEqual(self.notice(self.number_paid).status_code, 204)  # WeChat may send it again
        with self.store.connect() as db:
            events = db.execute("SELECT payload FROM outbox WHERE out_trade_no=?", (self.number_paid,)).fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual((json.loads(events[0]["payload"])["channel"], json.loads(events[0]["payload"])["trade_no"]), ("wechat", transaction))

    def test_notices_that_fail_any_check_change_nothing(self):
        self.fake.pay(self.number_paid)
        headers, body = self.fake.notification(self.number_paid)
        cases = {
            "forged body": (headers, body.replace(b"TRANSACTION.SUCCESS", b"TRANSACTION.SUCCESs")),
            "no signature": ({k: v for k, v in headers.items() if k != "Wechatpay-Signature"}, body),
            "signtest probe": ({**headers, "Wechatpay-Signature": "WECHATPAY/SIGNTEST/abc"}, body),
            "other key id": self.fake.notification(self.number_paid, serial="PUB_KEY_ID_0000000000000000000000000000"),
            "wrong apiv3 key": self.fake.notification(self.number_paid, key="f" * 32),
            "stale timestamp": self.fake.notification(self.number_paid, timestamp=int(time.time()) - 7200),
            "wrong amount": self.fake.notification(self.number_paid, amount={"total": 2, "payer_total": 2, "currency": "CNY", "payer_currency": "CNY"}),
            "wrong merchant": self.fake.notification(self.number_paid, mchid="1900000999"),
            "wrong app": self.fake.notification(self.number_paid, appid="wxaaaaaaaaaaaaaaaa"),
            "other order inside": self.fake.notification(self.number_paid, out_trade_no="WX_ORDER_9999"),
            "not a success state": self.fake.notification(self.number_paid, trade_state="NOTPAY"),
            "foreign currency": self.fake.notification(self.number_paid, amount={"total": 1, "payer_total": 1, "currency": "USD", "payer_currency": "USD"}),
        }
        for label, (h, b) in cases.items():
            response = self.client.post(f"/wechat/notify/{self.number_paid}", data=b, headers=h)
            self.assertEqual(response.status_code, 400, label)
            self.assertEqual(response.json["code"], "FAIL", label)
            self.assertEqual(self.order(self.number_paid)["status"], "CREATED", label)
        self.assertEqual(self.order(self.number_paid)["trade_no"], None)

    def test_unknown_orders_and_orders_of_other_channels_are_refused_and_other_events_are_ignored(self):
        headers, body = self.fake.notification(self.number_paid)
        unknown = self.client.post("/wechat/notify/NO_SUCH_ORDER_1", data=body, headers=headers)
        forged = self.client.post(f"/wechat/notify/{self.number_paid}", data=body, headers={**headers, "Wechatpay-Signature": "AAAA"})
        # an unknown order is refused exactly like a bad signature, so the endpoint cannot be used to look orders up
        self.assertEqual((unknown.status_code, unknown.json), (forged.status_code, forged.json))
        self.assertEqual(unknown.status_code, 400)
        with self.store.connect() as db: db.execute("UPDATE orders SET channel='alipay' WHERE out_trade_no=?", (self.number_paid,))
        self.assertEqual(self.client.post(f"/wechat/notify/{self.number_paid}", data=body, headers=headers).status_code, 400)
        with self.store.connect() as db: db.execute("UPDATE orders SET channel='wechat' WHERE out_trade_no=?", (self.number_paid,))
        self.fake.pay(self.number_paid)
        self.assertEqual(self.notice(self.number_paid, event_type="REFUND.SUCCESS").status_code, 204)
        self.assertEqual(self.order(self.number_paid)["status"], "CREATED")  # verified, but not a payment event

    def test_junk_posts_are_rate_limited_and_leave_only_a_few_log_lines(self):
        junk = {"Wechatpay-Serial": self.fake.public_key_id, "Wechatpay-Timestamp": str(int(time.time())), "Wechatpay-Nonce": "n", "Wechatpay-Signature": "AAAA"}
        statuses = [self.client.post(f"/wechat/notify/{self.number_paid}", data=b"{}", headers=junk).status_code for _ in range(40)]
        self.assertEqual((statuses[:30], set(statuses[30:])), ([400] * 30, {429}))  # 30 a minute per order, then WeChat Pay would simply retry later
        rejected = [event for event in self.admin.get("/api/admin/events").json["events"] if event["stage"] == "notify.rejected"]
        self.assertLessEqual(len(rejected), 3)
        self.assertEqual(self.order(self.number_paid)["status"], "CREATED")
        # numbers that cannot be orders share one bucket instead of creating one counter each
        for index in range(5): self.client.post(f"/wechat/notify/not%20an%20order%20{index}", data=b"{}", headers=junk)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM limits WHERE key LIKE 'wechat-notify:%'").fetchone()[0], 2)

    def test_a_refused_notice_never_puts_its_own_text_into_the_log(self):
        headers, body = self.fake.notification(self.number_paid)
        weird = {**headers, "Wechatpay-Timestamp": "9" * 400}
        self.assertEqual(self.client.post(f"/wechat/notify/{self.number_paid}", data=body, headers=weird).status_code, 400)
        reasons = {event["reason"] for event in self.admin.get("/api/admin/events").json["events"] if event["stage"] == "notify.rejected"}
        self.assertEqual(reasons, {"wechat_response_signature_invalid"})

    def test_a_notice_signed_for_one_order_cannot_pay_another(self):
        other = self.create().json["order"]["out_trade_no"]
        self.fake.pay(self.number_paid)
        headers, body = self.fake.notification(self.number_paid)
        response = self.client.post(f"/wechat/notify/{other}", data=body, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual((self.order(other)["status"], self.order(self.number_paid)["status"]), ("CREATED", "CREATED"))

    def test_notices_use_the_credentials_the_order_was_created_with(self):
        self.fake.pay(self.number_paid)
        replacement = FakeWechat()
        replacement.orders = self.fake.orders
        self.save_config(**{"api_v3_key": replacement.api_v3_key, "platform_public_key": replacement.config()["platform_public_key"], "platform_public_key_id": replacement.public_key_id,
                            "merchant_private_key": replacement.config()["merchant_private_key"]})
        self.assertEqual(self.notice(self.number_paid).status_code, 204)  # still verified with the pinned (old) key set
        self.assertEqual(self.order(self.number_paid)["status"], "PAID")

    def test_trace_records_a_rejection_without_secrets(self):
        self.fake.pay(self.number_paid)
        headers, body = self.fake.notification(self.number_paid, key="f" * 32)
        self.client.post(f"/wechat/notify/{self.number_paid}", data=body, headers=headers)
        log = json.dumps(self.admin.get("/api/admin/events").json)
        self.assertIn("wechat_notification_invalid", log)
        for secret in (self.fake.api_v3_key, "BEGIN PRIVATE KEY", headers["Wechatpay-Signature"]):
            self.assertNotIn(secret, log)


class WechatReconcileTests(WechatCase):
    def setUp(self):
        super().setUp()
        self.number_paid = self.create().json["order"]["out_trade_no"]
        self.reconciler = self.app.extensions["reconciler"]

    def test_query_confirms_payment_when_no_notice_arrives(self):
        waiting = self.reconciler.sync(self.number_paid)
        self.assertEqual((waiting["order"]["status"], waiting["order"]["provider_status"], waiting["verification"]["error"]), ("CREATED", "NOTPAY", ""))
        transaction = self.fake.pay(self.number_paid)
        with self.store.connect() as db: db.execute("UPDATE payment_checks SET locked_until=0 WHERE out_trade_no=?", (self.number_paid,))  # skip the query cooldown
        paid = self.reconciler.sync(self.number_paid)
        self.assertEqual((paid["order"]["status"], paid["order"]["trade_no"], paid["verification"]["state"]), ("PAID", transaction, "verified"))

    def test_the_worker_picks_up_wechat_orders(self):
        self.fake.pay(self.number_paid)
        with self.store.connect() as db: db.execute("UPDATE payment_checks SET next_check=0 WHERE out_trade_no=?", (self.number_paid,))
        self.assertTrue(self.reconciler.tick())
        self.assertEqual(self.order(self.number_paid)["status"], "PAID")

    def test_a_closed_or_unknown_order_is_reported_not_paid(self):
        self.fake.orders[self.number_paid]["state"] = "CLOSED"
        self.assertEqual(self.reconciler.sync(self.number_paid)["order"]["status"], "CLOSED")
        ghost = self.store.create_order(self.user, {"out_trade_no": "WX_GHOST_0001", "subject": "x", "amount": "0.01", "product": "page", "channel": "wechat"}, "", self.channels.payment_config(config_id=self.config_id, channel="wechat"))
        result = self.reconciler.sync(ghost["out_trade_no"])
        self.assertEqual((result["order"]["status"], result["verification"]["state"], result["verification"]["error"]), ("CREATED", "awaiting_checkout", ""))

    def test_mismatched_or_unverifiable_answers_are_not_applied(self):
        self.fake.pay(self.number_paid)
        self.fake.orders[self.number_paid]["amount"] = 2  # WeChat answers a different amount than the order we hold
        mismatch = self.reconciler.sync(self.number_paid)
        self.assertEqual((mismatch["order"]["status"], mismatch["verification"]["error"]), ("CREATED", "payment_mismatch"))
        self.fake.orders[self.number_paid]["amount"] = 1
        self.fake.sign_responses = False
        with self.store.connect() as db: db.execute("UPDATE payment_checks SET locked_until=0 WHERE out_trade_no=?", (self.number_paid,))
        unsigned = self.reconciler.sync(self.number_paid)
        self.assertEqual((unsigned["order"]["status"], unsigned["verification"]["error"]), ("CREATED", "wechat_response_signature_invalid"))

    def test_a_late_not_paid_answer_cannot_make_a_paid_order_look_unpaid(self):
        self.fake.pay(self.number_paid)
        self.assertEqual(self.reconciler.sync(self.number_paid)["order"]["status"], "PAID")
        self.assertEqual(self.order(self.number_paid)["provider_status"], "SUCCESS")
        self.fake.orders[self.number_paid]["state"] = "NOTPAY"  # a stale answer arriving afterwards
        with self.store.connect() as db: db.execute("UPDATE payment_checks SET locked_until=0 WHERE out_trade_no=?", (self.number_paid,))
        self.reconciler.sync(self.number_paid)
        stored = self.order(self.number_paid)
        self.assertEqual((stored["status"], stored["provider_status"]), ("PAID", "SUCCESS"))

    def test_console_sync_endpoint_and_return_page_work_for_wechat(self):
        self.fake.pay(self.number_paid)
        synced = self.client.post(f"/api/v1/payments/{self.number_paid}/sync", headers=self.headers)
        self.assertEqual((synced.status_code, synced.json["order"]["status"]), (200, "PAID"))
        wap = self.create(product="wap").json["order"]
        token = json.loads(self.order(wap["out_trade_no"])["payment_payload"])["return_token"]
        self.fake.pay(wap["out_trade_no"])
        result = self.app.test_client().post("/api/payment-result", json={"state": token}, headers={"Origin": "https://pay.example.test"})
        self.assertEqual((result.status_code, result.json["order"]["status"]), (200, "PAID"))


class WechatRefundTests(WechatCase):
    def setUp(self):
        super().setUp()
        self.number_paid = self.create(amount="5.00").json["order"]["out_trade_no"]
        self.fake.pay(self.number_paid)
        self.app.extensions["reconciler"].sync(self.number_paid)
        self.refunds = self.app.extensions["refunds"]

    def refund(self, refund_no, amount, reason="用户申请"):
        return self.admin.post(f"/api/v1/payments/{self.number_paid}/refund", json={"refund_no": refund_no, "amount": amount, "reason": reason}, headers=self.admin_headers)

    def test_partial_and_full_refunds_move_the_order_through_its_states(self):
        first = self.refund("RF_0000000001", "2.00")
        self.assertEqual((first.status_code, first.json["refund"]["status"], first.json["order"]["status"], first.json["order"]["refunded_amount"]), (200, "SUCCESS", "PARTIALLY_REFUNDED", "2.00"))
        sent = json.loads(self.fake.calls[-1][2])
        self.assertEqual((sent["amount"], sent["transaction_id"], sent["reason"]), ({"refund": 200, "total": 500, "currency": "CNY"}, self.order(self.number_paid)["trade_no"], "用户申请"))
        self.assertEqual(self.refund("RF_0000000002", "3.01").json["error"], "refund_exceeds_remaining")
        second = self.refund("RF_0000000002", "3.00")
        self.assertEqual((second.json["refund"]["status"], second.json["order"]["status"]), ("SUCCESS", "REFUNDED"))
        calls = len(self.fake.calls)
        again = self.refund("RF_0000000002", "3.00")  # the same request number never refunds twice
        self.assertEqual((again.status_code, len(self.fake.calls)), (200, calls))

    def test_processing_refund_stays_reserved_until_wechat_confirms_it(self):
        self.fake.refund_status = "PROCESSING"
        pending = self.refund("RF_0000000003", "1.00")
        self.assertEqual((pending.status_code, pending.json["refund"]["status"], pending.json["refund"]["error"], pending.json["order"]["status"]), (202, "UNKNOWN", "refund_processing", "REFUNDING"))
        self.fake.refunds["RF_0000000003"]["status"] = "SUCCESS"
        self.refunds.sync("RF_0000000003")
        self.assertEqual((self.order(self.number_paid)["status"], self.order(self.number_paid)["refunded_amount"]), ("PARTIALLY_REFUNDED", "1.00"))

    def test_closed_refund_is_released_and_abnormal_one_waits_for_a_person(self):
        self.fake.refund_status = "CLOSED"
        closed = self.refund("RF_0000000004", "1.00")
        self.assertEqual((closed.json["refund"]["status"], closed.json["order"]["status"]), ("FAILED", "PAID"))
        self.fake.refund_status = "ABNORMAL"
        abnormal = self.refund("RF_0000000005", "1.00")
        self.assertEqual((abnormal.json["refund"]["status"], abnormal.json["refund"]["error"], abnormal.json["order"]["status"]), ("UNKNOWN", "refund_abnormal", "REFUNDING"))
        self.refunds.sync("RF_0000000005")
        self.assertEqual(self.order(self.number_paid)["status"], "REFUNDING")  # never silently completed or released

    def test_a_definitive_provider_refusal_releases_the_reservation_but_a_server_error_does_not(self):
        self.fake.fail_next = (403, "NOT_ENOUGH")
        refused = self.refund("RF_0000000006", "1.00")
        self.assertEqual((refused.json["refund"]["status"], refused.json["refund"]["error"], refused.json["order"]["status"]), ("FAILED", "wechat_refund_balance", "PAID"))
        self.fake.fail_next = (500, "SYSTEM_ERROR")
        uncertain = self.refund("RF_0000000007", "1.00")
        self.assertEqual((uncertain.json["refund"]["status"], uncertain.json["order"]["status"]), ("UNKNOWN", "REFUNDING"))
        self.refunds.sync("RF_0000000007")  # the request never reached WeChat: still undecided, not invented
        self.assertEqual(self.order(self.number_paid)["status"], "REFUNDING")

    def test_a_bare_client_error_from_a_proxy_is_not_a_refusal(self):
        # a 4xx without WeChat Pay's own error code (a proxy, a CDN, a timeout page) may hide a refund that went through
        original = self.fake.handle
        self.fake.transport = httpx.MockTransport(lambda request: httpx.Response(408, content=b"gateway timeout") if request.url.path.endswith("/refunds") and request.method == "POST" else original(request))
        self.app.extensions["refunds"].wechat_factory = lambda cfg: WechatClient(cfg, transport=self.fake.transport)
        response = self.refund("RF_0000000011", "1.00")
        self.assertEqual((response.json["refund"]["status"], response.json["order"]["status"]), ("UNKNOWN", "REFUNDING"))
        again = self.refund("RF_0000000012", "1.00")  # a second request is refused while the first one is undecided
        self.assertNotEqual(again.status_code, 200)

    def test_a_confirmation_that_disagrees_with_the_request_is_not_accepted(self):
        original = self.fake._refund_view

        def wrong(refund_no):
            view = original(refund_no)
            view["amount"]["refund"] += 1
            return view

        with patch.object(self.fake, "_refund_view", wrong):
            result = self.refund("RF_0000000008", "1.00")
        self.assertEqual((result.json["refund"]["status"], result.json["order"]["status"]), ("UNKNOWN", "REFUNDING"))


class WechatMerchantTests(WechatCase):
    """Epay-compatible merchants pay with type=wxpay: same protocol, WeChat channel behind it."""

    def setUp(self):
        super().setUp()
        self.merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.merchants = self.app.extensions["merchants"]
        self.profile = self.merchants.update(self.user, {"merchant_public_key": public_pem(self.merchant_key)})

    def create_v1(self, **changes):
        fields = {"pid": str(self.profile["merchant_id"]), "type": "wxpay", "out_trade_no": "SHOP_ORDER_01", "name": "商户商品", "money": "0.01", "notify_url": "https://merchant.example/notify", **changes}
        return self.client.post("/mapi.php", data=signed(fields, self.profile["md5_key"], "MD5"))

    def test_wxpay_order_opens_a_cashier_with_the_wechat_code(self):
        response = self.create_v1()
        self.assertEqual(response.json["code"], 1, response.json)
        token = response.json["payurl"].rsplit("/", 1)[1]
        view = self.client.get(f"/api/cashier/{token}").json
        self.assertEqual((view["channel"], view["qr_ready"], view["paid"]), ("wechat", True, False))
        calls = len(self.fake.calls)
        prepared = self.client.post(f"/api/cashier/{token}/prepare", json={}, headers={"Origin": "https://pay.example.test"})
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(len(self.fake.calls), calls)  # the Native code is fixed: nothing is fetched again
        svg = self.client.get(f"/api/cashier/{token}/qr.svg")
        self.assertEqual(svg.status_code, 200)
        self.assertIn("#087c49", svg.text)

    def test_the_merchant_is_notified_as_wxpay_and_can_query_it(self):
        number = self.create_v1().json["trade_no"]
        self.fake.pay(number)
        self.assertEqual(self.notice(number).status_code, 204)
        fields = self.merchants.notification(number)
        self.assertEqual((fields["type"], fields["trade_status"], fields["out_trade_no"]), ("wxpay", "TRADE_SUCCESS", "SHOP_ORDER_01"))
        queried = self.client.post("/api.php", data={"act": "order", "pid": self.profile["merchant_id"], "key": self.profile["md5_key"], "out_trade_no": "SHOP_ORDER_01"}).json
        self.assertEqual((queried["type"], queried["status"]), ("wxpay", 1))

    def test_a_wechat_pay_refusal_reaches_the_merchant_in_the_epay_envelope(self):
        self.save_config(app_id="wx9999999999999999")  # not bound to the merchant number, so WeChat Pay refuses the order
        response = self.create_v1(out_trade_no="SHOP_ORDER_02")
        self.assertEqual((response.status_code, response.json["code"], response.json["error"]), (200, -1, "wechat_app_id_mismatch"))
        self.assertIn("msg", response.json)

    def test_unknown_epay_types_stay_unavailable(self):
        for kind in ("qqpay", "bank", "paypal"):
            self.assertEqual(self.create_v1(type=kind, out_trade_no="SHOP_" + kind).json["msg"], "该收款渠道尚未接入")


class WechatConfigurationTests(WechatCase):
    def describe(self):
        return next(c for c in self.channels.all() if c["config_id"] == self.config_id)

    def rejected(self, **fields):
        response = self.save_config(**fields)
        self.assertNotEqual(response.status_code, 200, fields)
        return response.json

    def test_every_pasted_mistake_gets_a_specific_reason(self):
        merchant_pub = serialization.load_pem_private_key(self.fake.config()["merchant_private_key"].encode(), password=None).public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        cases = [({"merchant_private_key": merchant_pub}, "invalid_channel_key", "merchant_private_key", "public_key"),
                 ({"merchant_private_key": "-----BEGIN PRIVATE KEY-----\nMIIE"}, "invalid_channel_key", "merchant_private_key", "incomplete"),
                 ({"merchant_private_key": "hello"}, "invalid_channel_key", "merchant_private_key", "unreadable"),
                 ({"platform_public_key": self.fake.config()["merchant_private_key"]}, "invalid_channel_key", "platform_public_key", "private_key"),
                 ({"platform_public_key": merchant_pub}, "wechat_public_key_is_merchant_key", "platform_public_key", None),
                 ({"api_v3_key": "short"}, "invalid_channel_field", "api_v3_key", None),
                 ({"platform_public_key_id": "3000000001"}, "invalid_channel_field", "platform_public_key_id", None),
                 ({"app_id": "2026000000000000"}, "invalid_channel_field", "app_id", None)]
        small = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        cases.append(({"merchant_private_key": pem_private(small)}, "invalid_channel_key", "merchant_private_key", "too_short"))
        for fields, code, field, reason in cases:
            body = self.rejected(**fields)
            self.assertEqual((body["error"], body["field"], body.get("reason")), (code, field, reason), fields)

    def test_the_real_world_formats_are_accepted_and_serial_numbers_are_tidied(self):
        key = self.fake.config()["merchant_private_key"]
        pasted = f"root@host:~$ cat apiclient_key.pem\n{key}root@host:~$ "
        serial = "serial=" + ":".join(self.fake.serial.lower()[i:i + 2] for i in range(0, 40, 2))
        self.assertEqual(self.save_config(merchant_private_key=pasted, certificate_serial=serial).status_code, 200)
        saved, _, _ = self.channels.get("wechat", self.config_id)
        self.assertEqual(saved["certificate_serial"], self.fake.serial)
        self.assertTrue(saved["merchant_private_key"].startswith("-----BEGIN RSA PRIVATE KEY-----"))
        self.assertTrue(self.describe()["configured"] and self.describe()["integrated"])

    def test_the_apiv3_key_is_redacted_by_name_even_when_it_looks_harmless(self):
        from bypay import logger
        masked = logger.safe_params({"api_v3_key": self.fake.api_v3_key, "apiv3_key": self.fake.api_v3_key, "merchant_private_key": "x", "out_trade_no": "ORDER000001"})
        self.assertNotIn(self.fake.api_v3_key, json.dumps(masked))
        self.assertEqual(masked["out_trade_no"], "ORDER000001")

    def test_secrets_are_never_returned_or_logged(self):
        text = json.dumps(self.admin.get("/api/admin/channels").json) + json.dumps(self.admin.get("/api/admin/events").json)
        for secret in (self.fake.api_v3_key, "BEGIN PRIVATE KEY", "BEGIN PUBLIC KEY"):
            self.assertNotIn(secret, text)
        self.assertTrue(self.describe()["secrets"]["api_v3_key"])

    def test_without_the_platform_key_the_configuration_is_incomplete(self):
        broken = {**self.fake.config(), "platform_public_key": "", "platform_public_key_id": ""}
        second = self.admin.post("/api/admin/channels", json={"channel": "wechat", "name": "微信备用", "fields": {k: v for k, v in broken.items() if v}}, headers=self.admin_headers)
        self.assertEqual(second.status_code, 201)
        self.assertFalse(second.json["channel"]["configured"])
        number = self.create(config_id=second.json["channel"]["config_id"])
        self.assertEqual((number.status_code, number.json["error"]), (503, "payment_not_configured"))

    def test_online_check_reports_what_is_right_or_wrong_without_creating_orders(self):
        url = f"/api/admin/channel-configs/{self.config_id}/check"
        good = self.admin.post(url, headers=self.admin_headers)
        self.assertEqual((good.status_code, good.json["authenticated"], good.json["verified"]), (200, True, True))
        self.assertEqual(self.fake.orders, {})
        self.assertEqual(self.client.post(url, headers=self.headers).status_code, 403)
        self.assertEqual(self.admin.post("/api/admin/channel-configs/1/check", headers=self.admin_headers).status_code, 422)
        self.save_config(merchant_private_key=FakeWechat().config()["merchant_private_key"])
        bad = self.admin.post(url, headers=self.admin_headers)
        self.assertEqual((bad.json["authenticated"], bad.json["code"]), (False, "wechat_sign_error"))

    def test_diagnostics_and_capabilities_describe_the_wechat_configuration(self):
        report = self.admin.get("/api/admin/diagnostics").json
        wechat = next(c for c in report["configurations"] if c["id"] == "wechat")
        self.assertTrue(wechat["configured"] and all(check["ok"] for check in wechat["checks"]))
        self.assertEqual({check["name"] for check in wechat["checks"]}, {"app_id", "mch_id", "certificate_serial", "api_v3_key", "merchant_private_key", "platform_public_key", "platform_public_key_id"})
        self.assertTrue(report["wechat_notify_ready"])
        caps = self.client.get("/api/capabilities").json
        self.assertIn("wechat", caps["channels"])
        self.assertTrue(next(c for c in caps["payment_configs"] if c["id"] == "wechat")["integrated"])

    def test_pinned_orders_keep_working_after_the_configuration_is_replaced(self):
        number = self.create().json["order"]["out_trade_no"]
        self.save_config(app_id="wx9999999999999999")
        self.assertEqual(self.app.extensions["reconciler"].sync(number)["verification"]["error"], "")  # still queried with the old AppID
        self.assertEqual(self.channels.payment_config(self.order(number))["app_id"], self.fake.app_id)


if __name__ == "__main__":
    unittest.main()
