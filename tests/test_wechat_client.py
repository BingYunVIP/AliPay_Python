import base64
import json
import re
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from bypay import logger
from bypay.wechat_client import WechatClient, WechatError, ready
from wechat_fake import FakeWechat

EXPIRE = "2026-10-17T12:00:00+08:00"
NOTIFY = "https://pay.example.test/wechat/notify/ORDER000001"


class WechatClientTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        logger.configure(Path(folder.name), console=False)  # a test run leaves nothing in data/logs
        self.fake = FakeWechat()
        self.client = WechatClient(self.fake.config(), transport=self.fake.transport)

    def create(self, **kw):
        return self.client.native(kw.pop("number", "ORDER000001"), kw.pop("cents", 1), kw.pop("description", "冰云支付测试订单"), NOTIFY, EXPIRE, **kw)

    # ---------------------------------------------------------------- creation
    def test_native_signs_the_request_and_returns_a_scannable_link(self):
        result = self.create(client_ip="203.0.113.7")
        self.assertRegex(result["code_url"], r"^weixin://wxpay/bizpayurl\?pr=[0-9a-f]{9}$")
        method, target, body = self.fake.calls[0]
        self.assertEqual((method, target), ("POST", "/v3/pay/transactions/native"))
        sent = json.loads(body)
        self.assertEqual(sent["amount"], {"total": 1, "currency": "CNY"})
        self.assertEqual((sent["appid"], sent["mchid"], sent["time_expire"], sent["notify_url"]), (self.fake.app_id, self.fake.mch_id, EXPIRE, NOTIFY))
        self.assertEqual(sent["scene_info"], {"payer_client_ip": "203.0.113.7"})

    def test_h5_jsapi_and_app_orders(self):
        h5 = self.client.h5("ORDER000002", 150, "手机支付", NOTIFY, EXPIRE, "203.0.113.7", app_name="冰云支付", app_url="https://pay.example.test")
        self.assertTrue(h5["h5_url"].startswith("https://wx.tenpay.com/cgi-bin/mmpayweb-bin/checkmweb?prepay_id=wx"))
        self.assertEqual(json.loads(self.fake.calls[-1][2])["scene_info"]["h5_info"], {"type": "Wap", "app_name": "冰云支付", "app_url": "https://pay.example.test"})
        js = self.client.jsapi("ORDER000003", 990, "小程序", NOTIFY, EXPIRE, "oFixtureOpenId0123456789ab")
        app = self.client.app("ORDER000004", 2000, "APP 支付", NOTIFY, EXPIRE)
        public = self.fake.merchant_key.public_key()
        verify = lambda message, signature: public.verify(base64.b64decode(signature), message.encode(), padding.PKCS1v15(), hashes.SHA256())
        verify(f"{js['appId']}\n{js['timeStamp']}\n{js['nonceStr']}\n{js['package']}\n", js["paySign"])
        verify(f"{app['appid']}\n{app['timestamp']}\n{app['noncestr']}\n{app['prepayid']}\n", app["sign"])
        self.assertEqual((js["signType"], js["package"].split("=")[0], app["package"], app["partnerid"]), ("RSA", "prepay_id", "Sign=WXPay", self.fake.mch_id))
        self.assertEqual(self.fake.orders["ORDER000003"]["openid"], "oFixtureOpenId0123456789ab")

    def test_description_is_cut_to_the_wechat_limit_and_non_ascii_survives_signing(self):
        self.create(description="商" * 200)
        self.assertEqual(len(json.loads(self.fake.calls[0][2])["description"]), 127)

    def test_wrong_merchant_private_key_is_reported_as_a_sign_error(self):
        other = FakeWechat()
        client = WechatClient({**self.fake.config(), "merchant_private_key": other.config()["merchant_private_key"]}, transport=self.fake.transport)
        with self.assertRaises(WechatError) as caught:
            client.native("ORDER000001", 1, "x", NOTIFY, EXPIRE)
        self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.provider_code), ("wechat_sign_error", 401, "SIGN_ERROR"))

    def test_provider_errors_get_stable_codes(self):
        for status, provider, code in ((400, "APPID_MCHID_NOT_MATCH", "wechat_app_id_mismatch"), (403, "NO_AUTH", "wechat_no_auth"), (403, "OUT_TRADE_NO_USED", "wechat_order_number_used"), (429, "FREQUENCY_LIMITED", "wechat_rate_limited"), (500, "SYSTEM_ERROR", "wechat_system_error"), (400, "SOMETHING_NEW", "payment_provider_error")):
            self.fake.fail_next = (status, provider)
            with self.assertRaises(WechatError) as caught:
                self.create()
            self.assertEqual((caught.exception.code, caught.exception.provider_code), (code, provider))

    def test_duplicate_number_with_different_amount_is_rejected_by_the_gateway(self):
        self.create(cents=1)
        with self.assertRaises(WechatError) as caught:
            self.create(cents=2)
        self.assertEqual(caught.exception.code, "wechat_order_number_used")

    # ---------------------------------------------------------------- answers must verify
    def test_tampered_answer_is_rejected(self):
        original = self.fake._reply

        def tamper(status, data=None):
            response = original(status, data)
            return httpx.Response(response.status_code, content=response.content.replace(b"weixin://", b"weixin:/ /"), headers=response.headers)

        with patch.object(self.fake, "_reply", tamper), self.assertRaises(WechatError) as caught:
            self.create()
        self.assertEqual(caught.exception.code, "wechat_response_signature_invalid")

    def test_unsigned_stale_and_foreign_key_answers_are_rejected(self):
        self.fake.sign_responses = False
        with self.assertRaises(WechatError) as unsigned:
            self.create()
        self.fake.sign_responses = True
        self.fake.skew = 3600
        with self.assertRaises(WechatError) as stale:
            self.create(number="ORDER000010")
        self.fake.skew = 0
        self.fake.platform_key = FakeWechat().platform_key  # signed by someone else, claiming the configured key id
        with self.assertRaises(WechatError) as foreign:
            self.create(number="ORDER000011")
        self.assertEqual([e.exception.code for e in (unsigned, stale, foreign)], ["wechat_response_signature_invalid"] * 3)

    def test_public_key_id_problems_are_named(self):
        self.fake.answer_serial = "PUB_KEY_ID_0000000000000000000000000000"  # the answer is signed by a different key id
        with self.assertRaises(WechatError) as rotated:
            self.create()
        self.assertEqual(rotated.exception.code, "wechat_public_key_mismatch")
        self.fake.answer_serial = None
        client = WechatClient({**self.fake.config(), "platform_public_key_id": "PUB_KEY_ID_0000000000000000000000000000"}, transport=self.fake.transport)
        with self.assertRaises(WechatError) as mistyped:  # WeChat refuses a request that names an unknown key id
            client.native("ORDER000001", 1, "x", NOTIFY, EXPIRE)
        self.assertEqual(mistyped.exception.code, "wechat_invalid_request")

    def test_certificate_validation_and_redirects_are_never_disabled(self):
        with patch("bypay.wechat_client.httpx.Client", wraps=httpx.Client) as factory:
            self.create()
        kwargs = factory.call_args.kwargs
        self.assertNotIn("verify", kwargs)
        self.assertIs(kwargs["follow_redirects"], False)

    def test_network_failures_do_not_leak_details(self):
        def broken(request): raise httpx.ConnectError("secret detail 10.0.0.5")
        client = WechatClient(self.fake.config(), transport=httpx.MockTransport(broken))
        with self.assertRaises(WechatError) as caught:
            client.native("ORDER000001", 1, "x", NOTIFY, EXPIRE)
        self.assertEqual(caught.exception.code, "wechat_network_error")
        self.assertNotIn("10.0.0.5", str(caught.exception))

    def test_a_malformed_link_from_the_gateway_is_refused(self):
        for kind, field, value in (("native", "code_url", "https://evil.example/pay"), ("h5", "h5_url", "https://evil.example/pay"), ("h5", "h5_url", "http://wx.tenpay.com/x")):
            with patch.object(self.fake, "_create", lambda product, data, field=field, value=value: self.fake._reply(200, {field: value})), self.assertRaises(WechatError) as caught:
                if kind == "native":
                    self.create()
                else:
                    self.client.h5("ORDER000012", 1, "x", NOTIFY, EXPIRE, "203.0.113.7")
            self.assertEqual(caught.exception.code, "payment_mismatch")

    # ---------------------------------------------------------------- query and refund
    def test_query_before_and_after_payment(self):
        self.create()
        waiting = self.client.query("ORDER000001")
        self.assertEqual((waiting["is_success"], waiting["trade_state"], waiting["amount_total"], waiting["transaction_id"]), (True, "NOTPAY", 1, ""))
        transaction = self.fake.pay("ORDER000001")
        paid = self.client.query("ORDER000001")
        self.assertEqual((paid["trade_state"], paid["transaction_id"], paid["mchid"], paid["appid"]), ("SUCCESS", transaction, self.fake.mch_id, self.fake.app_id))
        self.assertEqual(self.fake.calls[-1][1], f"/v3/pay/transactions/out-trade-no/ORDER000001?mchid={self.fake.mch_id}")
        missing = self.client.query("NOSUCHORDER1")
        self.assertEqual((missing["is_success"], missing["sub_code"]), (False, "ORDER_NOT_EXIST"))

    # ---------------------------------------------------------------- close
    def test_close_uses_signed_endpoint_and_returns_closed(self):
        self.create()
        result = self.client.close("ORDER000001")
        self.assertEqual(result, {"is_success": True, "out_trade_no": "ORDER000001", "trade_state": "CLOSED"})
        method, target, body = self.fake.calls[-1]
        self.assertEqual((method, target, json.loads(body)), ("POST", "/v3/pay/transactions/out-trade-no/ORDER000001/close", {"mchid": self.fake.mch_id}))
        self.assertEqual(self.fake.orders["ORDER000001"]["state"], "CLOSED")
        authorization = self.fake.request_authorizations[-1]
        match = re.fullmatch(r'WECHATPAY2-SHA256-RSA2048 mchid="(?P<mchid>[^"]+)",nonce_str="(?P<nonce>[^"]+)",signature="(?P<signature>[^"]+)",timestamp="(?P<timestamp>[^"]+)",serial_no="(?P<serial>[^"]+)"', authorization)
        self.assertIsNotNone(match)
        self.assertEqual((match["mchid"], match["serial"]), (self.fake.mch_id, self.fake.serial))
        signed = f"{method}\n{target}\n{match['timestamp']}\n{match['nonce']}\n{body}\n"
        self.fake.merchant_key.public_key().verify(base64.b64decode(match["signature"]), signed.encode(), padding.PKCS1v15(), hashes.SHA256())

    def test_close_refuses_a_paid_order(self):
        self.create()
        self.fake.pay("ORDER000001")
        with self.assertRaises(WechatError) as caught:
            self.client.close("ORDER000001")
        self.assertEqual((caught.exception.code, caught.exception.provider_code), ("wechat_order_paid", "ORDER_PAID"))
        self.assertEqual(self.fake.orders["ORDER000001"]["state"], "SUCCESS")

    def test_close_accepts_a_signed_already_closed_answer_idempotently(self):
        self.create()
        self.assertEqual(self.client.close("ORDER000001")["trade_state"], "CLOSED")
        self.assertEqual(self.client.close("ORDER000001"), {"is_success": True, "out_trade_no": "ORDER000001", "trade_state": "CLOSED"})
        self.assertEqual(self.fake.orders["ORDER000001"]["state"], "CLOSED")

    def test_close_rejects_an_invalid_success_signature(self):
        self.create()
        original = self.fake._reply

        def tamper(status, data=None):
            response = original(status, data)
            if status == 204:
                headers = {**response.headers, "Wechatpay-Signature": response.headers["Wechatpay-Signature"] + "tampered"}
                return httpx.Response(status, content=response.content, headers=headers)
            return response

        with patch.object(self.fake, "_reply", tamper), self.assertRaises(WechatError) as caught:
            self.client.close("ORDER000001")
        self.assertEqual(caught.exception.code, "wechat_response_signature_invalid")

    def test_close_requires_an_empty_http_204_answer(self):
        self.create()
        original = self.fake._reply

        def wrong_status(status, data=None):
            response = original(status, data)
            if status == 204:
                return httpx.Response(200, content=response.content, headers=response.headers)
            return response

        with patch.object(self.fake, "_reply", wrong_status), self.assertRaises(WechatError) as caught:
            self.client.close("ORDER000001")
        self.assertEqual((caught.exception.code, caught.exception.status), ("wechat_system_error", 200))
    def test_refund_states_and_query(self):
        self.create(cents=500)
        self.fake.pay("ORDER000001")
        done = self.client.refund("ORDER000001", "RF_ABC12345", 200, 500, "质量问题" * 40)
        self.assertEqual((done["status"], done["amount_refund"], done["amount_total"], done["out_refund_no"]), ("SUCCESS", 200, 500, "RF_ABC12345"))
        body = json.loads(self.fake.calls[-1][2])
        self.assertEqual((body["out_trade_no"], len(body["reason"]), body["amount"]), ("ORDER000001", 80, {"refund": 200, "total": 500, "currency": "CNY"}))
        self.fake.refund_status = "PROCESSING"
        self.assertEqual(self.client.refund("ORDER000001", "RF_ABC12346", 100, 500)["status"], "PROCESSING")
        self.fake.refunds["RF_ABC12346"]["status"] = "CLOSED"
        self.assertEqual(self.client.query_refund("RF_ABC12346")["status"], "CLOSED")
        with self.assertRaises(WechatError):
            self.client.query_refund("RF_UNKNOWN01")

    # ---------------------------------------------------------------- notices
    def test_notification_is_verified_then_decrypted(self):
        self.create()
        transaction = self.fake.pay("ORDER000001")
        headers, body = self.fake.notification("ORDER000001")
        notice = self.client.read_notification(headers, body)
        self.assertEqual(notice["event_type"], "TRANSACTION.SUCCESS")
        self.assertEqual({k: notice["transaction"][k] for k in ("out_trade_no", "transaction_id", "trade_state", "mchid", "appid")},
                         {"out_trade_no": "ORDER000001", "transaction_id": transaction, "trade_state": "SUCCESS", "mchid": self.fake.mch_id, "appid": self.fake.app_id})
        self.assertEqual(notice["transaction"]["amount"]["total"], 1)

    def test_forged_or_replayed_notices_are_refused(self):
        self.create()
        self.fake.pay("ORDER000001")
        headers, body = self.fake.notification("ORDER000001")
        forged_body = body.replace(b"TRANSACTION.SUCCESS", b"TRANSACTION.SUCCES!")
        probe = {**headers, "Wechatpay-Signature": "WECHATPAY/SIGNTEST/" + headers["Wechatpay-Signature"]}
        stale_headers, stale_body = self.fake.notification("ORDER000001", timestamp=int(time.time()) - 3600)
        other_serial_headers, other_serial_body = self.fake.notification("ORDER000001", serial="PUB_KEY_ID_9999999999999999999999999999")
        wrong_key_headers, wrong_key_body = self.fake.notification("ORDER000001", key="ffffffffffffffffffffffffffffffff")
        results = []
        for case, (h, b) in {"tampered": (headers, forged_body), "signtest probe": (probe, body), "stale": (stale_headers, stale_body), "other key id": (other_serial_headers, other_serial_body), "wrong apiv3 key": (wrong_key_headers, wrong_key_body), "missing signature": ({k: v for k, v in headers.items() if k != "Wechatpay-Signature"}, body)}.items():
            with self.assertRaises(WechatError, msg=case) as caught:
                self.client.read_notification(h, b)
            results.append((case, caught.exception.code))
        self.assertEqual(dict(results), {"tampered": "wechat_response_signature_invalid", "signtest probe": "wechat_response_signature_invalid", "stale": "wechat_response_signature_invalid",
                                         "other key id": "wechat_public_key_mismatch", "wrong apiv3 key": "wechat_notification_invalid", "missing signature": "wechat_response_signature_invalid"})

    def test_notification_with_an_unknown_algorithm_is_refused(self):
        self.create()
        self.fake.pay("ORDER000001")
        headers, body = self.fake.notification("ORDER000001")
        document = json.loads(body)
        document["resource"]["algorithm"] = "AEAD_NULL"
        tampered = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
        with self.assertRaises(WechatError) as caught:
            self.client.read_notification(self.fake.signed_headers(tampered), tampered.encode())
        self.assertEqual(caught.exception.code, "wechat_notification_invalid")

    # ---------------------------------------------------------------- configuration and checks
    def test_incomplete_or_bad_configuration_is_refused_before_any_request(self):
        for override in ({"api_v3_key": ""}, {"api_v3_key": "short"}, {"platform_public_key": ""}, {"platform_public_key_id": ""}, {"mch_id": ""}):
            with self.assertRaises(WechatError, msg=str(override)) as caught:
                WechatClient({**self.fake.config(), **override}, transport=self.fake.transport)
            self.assertEqual(caught.exception.code, "payment_not_configured")
        # the private key is only parsed when a request has to be signed: still before any request leaves, and notices never need it
        unusable = WechatClient({**self.fake.config(), "merchant_private_key": "not a key"}, transport=self.fake.transport)
        with self.assertRaises(WechatError) as caught:
            unusable.native("ORDER000001", 1, "x", NOTIFY, EXPIRE)
        self.assertEqual(caught.exception.code, "payment_not_configured")
        self.create()
        self.fake.pay("ORDER000001")
        headers, body = self.fake.notification("ORDER000001")
        self.assertEqual(unusable.read_notification(headers, body)["event_type"], "TRANSACTION.SUCCESS")
        self.assertEqual(len(self.fake.calls), 1)  # only the order the working client created
        self.assertTrue(ready(self.fake.config()))
        self.assertFalse(ready({**self.fake.config(), "platform_public_key": ""}))

    def test_odd_timestamps_and_answers_are_refused_cleanly(self):
        self.create()
        self.fake.pay("ORDER000001")
        headers, body = self.fake.notification("ORDER000001")
        for stamp in ("9" * 400, "٣" * 10, "-5", "1e9", "1" * 13, " 1"):
            with self.assertRaises(WechatError, msg=stamp) as caught:
                self.client.read_notification({**headers, "Wechatpay-Timestamp": stamp}, body)
            self.assertEqual(caught.exception.code, "wechat_response_signature_invalid", stamp)
        # a gateway answer that is not the JSON object we expect is an error with a code, never an uncaught exception
        fake = FakeWechat()
        signed_replies = {text: httpx.Response(200, content=text.encode(), headers=fake.signed_headers(text)) for text in ("[1,2]", "not json", '"text"')}
        for text, reply in signed_replies.items():
            client = WechatClient(fake.config(), transport=httpx.MockTransport(lambda request, reply=reply: reply))
            with self.assertRaises(WechatError, msg=text) as caught:
                client.query("ORDER000001")
            self.assertEqual(caught.exception.code, "wechat_system_error", text)
        for status, content in ((400, b'["x"]'), (502, b"<html>bad gateway</html>"), (408, b"")):
            client = WechatClient(fake.config(), transport=httpx.MockTransport(lambda request, status=status, content=content: httpx.Response(status, content=content)))
            with self.assertRaises(WechatError) as caught:
                client.query("ORDER000001")
            self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.provider_code), ("payment_provider_error", status, ""))

    def test_connectivity_check_distinguishes_credential_problems(self):
        good = self.client.check()
        self.assertEqual((good["authenticated"], good["verified"]), (True, True))
        self.assertEqual(len(self.fake.orders), 0)  # a probe never creates an order
        wrong_key = WechatClient({**self.fake.config(), "merchant_private_key": FakeWechat().config()["merchant_private_key"]}, transport=self.fake.transport).check()
        self.assertEqual((wrong_key["authenticated"], wrong_key["code"]), (False, "wechat_sign_error"))
        wrong_serial = WechatClient({**self.fake.config(), "certificate_serial": "AB" * 20}, transport=self.fake.transport).check()
        self.assertFalse(wrong_serial["authenticated"])
        impostor = FakeWechat()
        impostor_key = WechatClient({**self.fake.config(), "platform_public_key": impostor.config()["platform_public_key"]}, transport=self.fake.transport).check()
        self.assertEqual((impostor_key["authenticated"], impostor_key["verified"]), (True, False))
        self.fake.sign_responses = False
        self.assertIsNone(self.client.check()["verified"])


if __name__ == "__main__":
    unittest.main()
