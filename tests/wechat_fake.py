"""An in-process stand-in for the WeChat Pay APIv3 gateway, used by the tests (no network).

It plays WeChat's side of the contract: it checks the merchant's request signature with the merchant's public key,
answers with a platform signature, creates orders, queries, refunds, and builds encrypted, signed payment notices.
"""
import base64
import json
import re
import secrets
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AUTHORIZATION = re.compile(r'WECHATPAY2-SHA256-RSA2048 mchid="(?P<mchid>[^"]+)",nonce_str="(?P<nonce>[^"]+)",signature="(?P<signature>[^"]+)",timestamp="(?P<timestamp>[^"]+)",serial_no="(?P<serial>[^"]+)"')


def pem_private(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def pem_public(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


class FakeWechat:
    def __init__(self, mch_id="1900000109", app_id="wx0123456789abcdef", api_v3_key="0123456789abcdef0123456789abcdef"):
        self.mch_id, self.app_id, self.api_v3_key = mch_id, app_id, api_v3_key
        self.merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.platform_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.serial = "4C3B2A1908F7E6D5C4B3A2910817263544332211"
        self.public_key_id = "PUB_KEY_ID_0119000001092026101700000001"
        self.orders, self.refunds, self.calls = {}, {}, []
        self.refund_status = "SUCCESS"
        self.answer_serial = None  # make answers claim another key id (a rotated key)
        self.sign_responses = True
        self.skew = 0
        self.fail_next = None
        self.transport = httpx.MockTransport(self.handle)

    # ------------------------------------------------------------------ what the console would hold
    def config(self, **overrides):
        return {"app_id": self.app_id, "mch_id": self.mch_id, "certificate_serial": self.serial, "api_v3_key": self.api_v3_key,
                "merchant_private_key": pem_private(self.merchant_key), "platform_public_key": pem_public(self.platform_key),
                "platform_public_key_id": self.public_key_id, **overrides}

    # ------------------------------------------------------------------ signing helpers
    def _sign(self, message):
        return base64.b64encode(self.platform_key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()

    def signed_headers(self, body, timestamp=None, serial=None):
        timestamp, nonce = str(timestamp if timestamp is not None else int(time.time()) + self.skew), secrets.token_hex(16)
        return {"Wechatpay-Timestamp": timestamp, "Wechatpay-Nonce": nonce, "Wechatpay-Serial": serial or self.public_key_id,
                "Wechatpay-Signature": self._sign(f"{timestamp}\n{nonce}\n{body}\n")}

    def _reply(self, status, data=None):
        body = json.dumps(data, separators=(",", ":")) if data is not None else ""
        headers = {"Content-Type": "application/json"}
        if self.sign_responses and status != 401:
            headers.update(self.signed_headers(body, serial=self.answer_serial))
        return httpx.Response(status, content=body.encode(), headers=headers)

    def _error(self, status, code, message="error"):
        return self._reply(status, {"code": code, "message": message})

    # ------------------------------------------------------------------ the gateway
    def handle(self, request):
        body = request.content.decode()
        target = request.url.raw_path.decode()
        match = AUTHORIZATION.fullmatch(request.headers.get("Authorization", ""))
        self.calls.append((request.method, target, body))
        if not match or match["mchid"] != self.mch_id or match["serial"] != self.serial:
            return self._error(401, "SIGN_ERROR", "bad authorization")
        message = f"{request.method}\n{target}\n{match['timestamp']}\n{match['nonce']}\n{body}\n"
        try:
            self.merchant_key.public_key().verify(base64.b64decode(match["signature"]), message.encode(), padding.PKCS1v15(), hashes.SHA256())
        except Exception:
            return self._error(401, "SIGN_ERROR", "signature mismatch")
        if abs(time.time() - int(match["timestamp"])) > 300:
            return self._error(401, "SIGN_ERROR", "timestamp")
        if request.headers.get("Wechatpay-Serial") != self.public_key_id:
            return self._error(400, "INVALID_REQUEST", "missing Wechatpay-Serial")
        if self.fail_next:
            status, code = self.fail_next
            self.fail_next = None
            return self._error(status, code)
        path = urlsplit(target).path
        data = json.loads(body) if body else {}
        if request.method == "POST" and path in {"/v3/pay/transactions/native", "/v3/pay/transactions/h5", "/v3/pay/transactions/jsapi", "/v3/pay/transactions/app"}:
            return self._create(path.rsplit("/", 1)[1], data)
        if request.method == "GET" and path.startswith("/v3/pay/transactions/out-trade-no/"):
            return self._query(path.rsplit("/", 1)[1], parse_qs(urlsplit(target).query))
        if request.method == "POST" and path == "/v3/refund/domestic/refunds":
            return self._refund(data)
        if request.method == "GET" and path.startswith("/v3/refund/domestic/refunds/"):
            return self._refund_state(path.rsplit("/", 1)[1])
        return self._error(404, "RESOURCE_NOT_EXISTS", "unknown interface")

    def _create(self, product, data):
        number = data.get("out_trade_no", "")
        if data.get("appid") != self.app_id or data.get("mchid") != self.mch_id:
            return self._error(400, "APPID_MCHID_NOT_MATCH")
        if not re.fullmatch(r"[0-9A-Za-z_|*-]{6,32}", number) or not data.get("description") or len(data["description"]) > 127:
            return self._error(400, "PARAM_ERROR", "out_trade_no or description")
        if not str(data.get("notify_url", "")).startswith("https://") or "?" in data["notify_url"]:
            return self._error(400, "PARAM_ERROR", "notify_url")
        amount = data.get("amount") or {}
        if type(amount.get("total")) is not int or amount["total"] < 1 or amount.get("currency", "CNY") != "CNY":
            return self._error(400, "PARAM_ERROR", "amount")
        try:
            datetime.fromisoformat(data["time_expire"])
        except (KeyError, ValueError):
            return self._error(400, "PARAM_ERROR", "time_expire")
        scene = data.get("scene_info") or {}
        if product == "h5" and not (scene.get("payer_client_ip") and (scene.get("h5_info") or {}).get("type") == "Wap"):
            return self._error(400, "PARAM_ERROR", "scene_info")
        if product in {"jsapi"} and not (data.get("payer") or {}).get("openid"):
            return self._error(400, "PARAM_ERROR", "payer.openid")
        existing = self.orders.get(number)
        if existing and (existing["amount"] != amount["total"] or existing["product"] != product):
            return self._error(403, "OUT_TRADE_NO_USED")
        self.orders.setdefault(number, {"product": product, "amount": amount["total"], "state": "NOTPAY", "transaction_id": "", "notify_url": data["notify_url"], "time_expire": data["time_expire"], "openid": (data.get("payer") or {}).get("openid", "")})
        if product == "native":
            return self._reply(200, {"code_url": "weixin://wxpay/bizpayurl?pr=" + uuid.uuid4().hex[:9]})
        if product == "h5":
            return self._reply(200, {"h5_url": "https://wx.tenpay.com/cgi-bin/mmpayweb-bin/checkmweb?prepay_id=wx" + uuid.uuid4().hex[:24] + "&package=" + str(secrets.randbelow(10**9))})
        return self._reply(200, {"prepay_id": "wx" + uuid.uuid4().hex[:28]})

    def _query(self, number, query):
        if query.get("mchid") != [self.mch_id]:
            return self._error(400, "PARAM_ERROR", "mchid")
        order = self.orders.get(number)
        if not order:
            return self._error(404, "ORDER_NOT_EXIST", "order not exist")
        return self._reply(200, self.transaction(number))

    def transaction(self, number, **overrides):
        order = self.orders[number]
        data = {"appid": self.app_id, "mchid": self.mch_id, "out_trade_no": number, "transaction_id": order["transaction_id"], "trade_type": order["product"].upper().replace("H5", "MWEB"),
                "trade_state": order["state"], "trade_state_desc": "state", "bank_type": "OTHERS", "success_time": "2026-10-17T10:20:30+08:00",
                "payer": {"openid": order["openid"] or "oFixtureOpenId0123456789ab"}, "amount": {"total": order["amount"], "payer_total": order["amount"], "currency": "CNY", "payer_currency": "CNY"}}
        data.update(overrides)
        return data

    def pay(self, number, state="SUCCESS"):
        """The buyer pays: WeChat now knows a transaction id for this order."""
        order = self.orders[number]
        order["state"] = state
        order["transaction_id"] = order["transaction_id"] or "4200" + str(secrets.randbelow(10**24)).zfill(24)
        return order["transaction_id"]

    def _refund(self, data):
        number = data.get("out_trade_no") or next((n for n, o in self.orders.items() if o["transaction_id"] == data.get("transaction_id")), "")
        order = self.orders.get(number)
        amount = data.get("amount") or {}
        if not order or order["state"] not in {"SUCCESS", "REFUND"}:
            return self._error(404, "RESOURCE_NOT_EXISTS", "order not paid")
        if amount.get("total") != order["amount"] or type(amount.get("refund")) is not int or not 0 < amount["refund"] <= order["amount"]:
            return self._error(400, "PARAM_ERROR", "amount")
        refund_no = data.get("out_refund_no", "")
        if not re.fullmatch(r"[0-9A-Za-z_|*@-]{1,64}", refund_no):
            return self._error(400, "PARAM_ERROR", "out_refund_no")
        if refund_no not in self.refunds:
            self.refunds[refund_no] = {"out_trade_no": number, "status": self.refund_status, "refund": amount["refund"], "total": order["amount"], "reason": data.get("reason", "")}
        return self._reply(200, self._refund_view(refund_no))

    def _refund_state(self, refund_no):
        if refund_no not in self.refunds:
            return self._error(404, "RESOURCE_NOT_EXISTS", "refund not exist")
        return self._reply(200, self._refund_view(refund_no))

    def _refund_view(self, refund_no):
        refund = self.refunds[refund_no]
        order = self.orders[refund["out_trade_no"]]
        return {"refund_id": "5030000000" + uuid.uuid4().hex[:12], "out_refund_no": refund_no, "transaction_id": order["transaction_id"], "out_trade_no": refund["out_trade_no"],
                "channel": "ORIGINAL", "status": refund["status"], "create_time": "2026-10-17T10:30:00+08:00",
                "amount": {"total": refund["total"], "refund": refund["refund"], "payer_total": refund["total"], "payer_refund": refund["refund"]}}

    # ------------------------------------------------------------------ notices
    def notification(self, number, event_type="TRANSACTION.SUCCESS", key=None, timestamp=None, serial=None, **overrides):
        """Return (headers, body bytes) exactly like WeChat's callback for a paid order."""
        plain = json.dumps(self.transaction(number, **overrides), separators=(",", ":")).encode()
        nonce, aad = secrets.token_hex(6), "transaction"
        ciphertext = AESGCM((key or self.api_v3_key).encode()).encrypt(nonce.encode(), plain, aad.encode())
        document = {"id": str(uuid.uuid4()), "create_time": "2026-10-17T10:20:31+08:00", "resource_type": "encrypt-resource", "event_type": event_type, "summary": "支付成功",
                    "resource": {"original_type": "transaction", "algorithm": "AEAD_AES_256_GCM", "ciphertext": base64.b64encode(ciphertext).decode(), "associated_data": aad, "nonce": nonce}}
        body = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
        return {**self.signed_headers(body, timestamp, serial), "Content-Type": "application/json"}, body.encode()
