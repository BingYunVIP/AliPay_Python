"""微信支付 APIv3 客户端：下单（Native / H5 / JSAPI·小程序 / APP）、查单、退款、回调验签与解密。

移植自 Epay 的「微信官方支付V3」插件（plugins/wxpayn）及其 SDK（includes/vendor/cccyun/wechatpay-sdk/src/V3），
请求签名、应答验签、回调 AEAD_AES_256_GCM 解密、JSAPI/APP 调起参数的规则完全一致，差异只有安全方面：

  - 始终校验 HTTPS 证书（Epay 的 SDK 关闭了证书校验）
  - 每个应答和每个回调都必须通过「微信支付公钥」验签，并核对公钥 ID，缺少公钥时拒绝使用
  - 回调带时间戳新鲜度检查

只实现「微信支付公钥」模式；平台证书模式不支持（微信支付已推荐新接入一律使用公钥）。
官方文档：https://pay.weixin.qq.com/doc/v3/merchant/4012791877（Native 下单）及同目录各接口。
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import logger
from .key_utils import KeyProblem, load_private_key, load_public_key

GATEWAY = "https://api.mch.weixin.qq.com"
REQUIRED = ("app_id", "mch_id", "certificate_serial", "api_v3_key", "merchant_private_key", "platform_public_key", "platform_public_key_id")
FRESHNESS_SECONDS = 600

# 微信支付错误码 → 控制台可解释的稳定代码
PROVIDER_ERRORS = {
    "SIGN_ERROR": "wechat_sign_error", "APPID_MCHID_NOT_MATCH": "wechat_app_id_mismatch", "MCH_NOT_EXISTS": "wechat_mch_not_exists",
    "NO_AUTH": "wechat_no_auth", "OUT_TRADE_NO_USED": "wechat_order_number_used", "ORDER_CLOSED": "wechat_order_closed", "ORDER_PAID": "wechat_order_paid",
    "FREQUENCY_LIMITED": "wechat_rate_limited", "RULE_LIMIT": "wechat_rule_limit", "PARAM_ERROR": "wechat_param_error",
    "INVALID_REQUEST": "wechat_invalid_request", "NOT_ENOUGH": "wechat_refund_balance", "USER_ACCOUNT_ABNORMAL": "wechat_refund_user_abnormal",
    "TRADE_OVERDUE": "wechat_refund_overdue", "REFUND_AMOUNT_EXCEED": "refund_exceeds_remaining", "SYSTEM_ERROR": "wechat_system_error",
}


# WeChat Pay's own "no" to a refund request. Any other failure (a proxy's 4xx, a timeout, SYSTEM_ERROR) may still have been processed.
REFUND_REFUSALS = {"SIGN_ERROR", "APPID_MCHID_NOT_MATCH", "MCH_NOT_EXISTS", "NO_AUTH", "PARAM_ERROR", "INVALID_REQUEST", "NOT_ENOUGH", "USER_ACCOUNT_ABNORMAL",
                   "TRADE_OVERDUE", "REFUND_AMOUNT_EXCEED", "RULE_LIMIT", "RESOURCE_NOT_EXISTS", "ORDER_NOT_EXIST", "INVALID_TRANSACTIONID"}


class WechatError(RuntimeError):
    """微信支付调用失败。`code` 是控制台解释用的稳定代码，`provider_code` 是微信支付返回的原始错误码。"""

    def __init__(self, message: str, code: str = "payment_provider_error", status: int = 0, provider_code: str = ""):
        super().__init__(message)
        self.code, self.status, self.provider_code = code, status, provider_code
        self.signature = None  # only set on error answers, see WechatClient._signature_state


def ready(config: dict) -> bool:
    return all(config.get(key) for key in REQUIRED)


def _nonce() -> str:
    return secrets.token_hex(16)


class WechatClient:
    """一个商户配置对应一个客户端；`transport` 仅用于测试注入。"""

    def __init__(self, config: dict, transport: httpx.BaseTransport | None = None, clock=time.time):
        missing = [key for key in REQUIRED if not config.get(key)]
        if missing:
            raise WechatError("微信支付配置不完整：" + "、".join(missing), "payment_not_configured")
        if len(config["api_v3_key"].encode()) != 32:
            raise WechatError("APIv3 密钥必须是 32 个字符", "payment_not_configured")
        try:
            self._platform = load_public_key(config["platform_public_key"])
        except KeyProblem as problem:
            raise WechatError("微信支付密钥无法使用：" + problem.reason, "payment_not_configured") from None
        self._private_pem, self._private_key = config["merchant_private_key"], None  # parsed on first use: only requests are signed with it, notices are not
        self.app_id, self.mch_id = config["app_id"], config["mch_id"]
        self._serial, self._public_key_id = config["certificate_serial"], config["platform_public_key_id"]
        self._aead = AESGCM(config["api_v3_key"].encode())
        self._transport, self._clock = transport, clock

    # ------------------------------------------------------------------ 签名
    @property
    def _private(self):
        if self._private_key is None:
            try:
                self._private_key = load_private_key(self._private_pem)
            except KeyProblem as problem:
                raise WechatError("微信支付密钥无法使用：" + problem.reason, "payment_not_configured") from None
        return self._private_key

    def _sign(self, message: str) -> str:
        return base64.b64encode(self._private.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()

    def _authorization(self, method: str, path: str, body: str) -> str:
        timestamp, nonce = str(int(self._clock())), _nonce()
        signature = self._sign(f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n")
        return (f'WECHATPAY2-SHA256-RSA2048 mchid="{self.mch_id}",nonce_str="{nonce}",signature="{signature}",'
                f'timestamp="{timestamp}",serial_no="{self._serial}"')

    def verify(self, headers, body: bytes) -> None:
        """应答与回调共用：公钥 ID 必须一致、时间戳在有效期内、签名通过微信支付公钥验证。"""
        serial, timestamp = headers.get("Wechatpay-Serial", ""), headers.get("Wechatpay-Timestamp", "")
        nonce, signature = headers.get("Wechatpay-Nonce", ""), headers.get("Wechatpay-Signature", "")
        if not (serial and timestamp and nonce and signature):
            raise WechatError("微信支付返回缺少签名信息", "wechat_response_signature_invalid")
        if serial != self._public_key_id:
            raise WechatError("微信支付公钥 ID 与配置不一致", "wechat_public_key_mismatch")
        if not (timestamp.isascii() and timestamp.isdigit() and len(timestamp) <= 12) or abs(self._clock() - int(timestamp)) > FRESHNESS_SECONDS:
            raise WechatError("微信支付返回的时间戳超出有效期", "wechat_response_signature_invalid")
        try:
            self._platform.verify(base64.b64decode(signature, validate=True), f"{timestamp}\n{nonce}\n".encode() + body + b"\n", padding.PKCS1v15(), hashes.SHA256())
        except (InvalidSignature, ValueError):
            raise WechatError("微信支付返回内容验签失败", "wechat_response_signature_invalid") from None

    def _signature_state(self, headers, body: bytes):
        """错误应答不作为业务依据，只用于连通性检查：True 验签通过，False 签名或公钥不对，None 应答没有签名。"""
        if not headers.get("Wechatpay-Signature"):
            return None
        try:
            self.verify(headers, body)
        except WechatError:
            return False
        return True

    # ------------------------------------------------------------------ 请求
    def _request(self, method: str, path: str, query: dict | None = None, body: dict | None = None, expected_status: int | None = None) -> dict:
        target = path + ("?" + urlencode(query) if query else "")
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body is not None else ""
        headers = {"Authorization": self._authorization(method, target, payload), "Accept": "application/json",
                   "Wechatpay-Serial": self._public_key_id, "User-Agent": "BingYunPay-wechatpay/1.0"}
        if payload:
            headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(base_url=GATEWAY, transport=self._transport, timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False, trust_env=False) as http:
                response = http.request(method, target, content=payload.encode() or None, headers=headers)
        except httpx.HTTPError as exc:
            logger.error("wechat", "微信支付请求失败", 异常类型=type(exc).__name__, 接口=path)
            raise WechatError("无法连接微信支付：" + type(exc).__name__, "wechat_network_error") from None
        if 200 <= response.status_code < 300:
            self.verify(response.headers, response.content)
            if expected_status is not None and response.status_code != expected_status:
                raise WechatError("微信支付返回了意外的状态码", "wechat_system_error", response.status_code)
            if expected_status == 204 and response.content:
                raise WechatError("微信支付 204 响应不应包含内容", "wechat_system_error", response.status_code)
            try:
                data = response.json() if response.content else {}
            except ValueError:
                raise WechatError("微信支付返回了无法识别的内容", "wechat_system_error", response.status_code) from None
            if not isinstance(data, dict):
                raise WechatError("微信支付返回了无法识别的内容", "wechat_system_error", response.status_code)
            return data
        try:
            detail = response.json()
        except ValueError:
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        provider = str(detail.get("code", ""))[:64]
        if provider == "ORDER_NOT_EXIST":  # 配置检查和对账都会查询可能还不存在的订单，不是故障
            logger.info("wechat", "微信支付查无此订单", 接口=path, 状态码=response.status_code)
        else:
            logger.warn("wechat", "微信支付返回错误", 接口=path, 状态码=response.status_code, 错误码=provider, 说明=str(detail.get("message", ""))[:200])
        failure = WechatError(str(detail.get("message") or "微信支付返回错误")[:300], PROVIDER_ERRORS.get(provider, "payment_provider_error"), response.status_code, provider)
        failure.signature = self._signature_state(response.headers, response.content)
        raise failure

    def _order_body(self, number: str, amount_cents: int, description: str, notify_url: str, time_expire: str, client_ip: str = "") -> dict:
        body = {"appid": self.app_id, "mchid": self.mch_id, "description": description[:127], "out_trade_no": number,
                "time_expire": time_expire, "notify_url": notify_url, "amount": {"total": amount_cents, "currency": "CNY"}}
        if client_ip:
            body["scene_info"] = {"payer_client_ip": client_ip}
        return body

    # ------------------------------------------------------------------ 下单
    def native(self, number, amount_cents, description, notify_url, time_expire, client_ip="") -> dict:
        """扫码支付（网页支付）。返回 {"code_url": "weixin://wxpay/…"}，二维码有效期 2 小时。"""
        data = self._request("POST", "/v3/pay/transactions/native", body=self._order_body(number, amount_cents, description, notify_url, time_expire, client_ip))
        code_url = data.get("code_url")
        if not isinstance(code_url, str) or not code_url.startswith("weixin://wxpay/") or len(code_url) > 512:
            raise WechatError("微信支付没有返回有效的二维码链接", "payment_mismatch")
        logger.info("wechat", "已创建 Native 订单", 订单号=number, 金额分=amount_cents)
        return {"code_url": code_url}

    def h5(self, number, amount_cents, description, notify_url, time_expire, client_ip, app_name="", app_url="") -> dict:
        """H5 支付（手机浏览器）。返回的 h5_url 只有 5 分钟有效期，不能改动，只允许在末尾追加 redirect_url。"""
        body = self._order_body(number, amount_cents, description, notify_url, time_expire)
        body["scene_info"] = {"payer_client_ip": client_ip, "h5_info": {"type": "Wap", **({"app_name": app_name[:64]} if app_name else {}), **({"app_url": app_url[:128]} if app_url else {})}}
        data = self._request("POST", "/v3/pay/transactions/h5", body=body)
        url = data.get("h5_url")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or parsed.hostname != "wx.tenpay.com" or parsed.username or parsed.password or len(url) > 1024:
            raise WechatError("微信支付没有返回有效的 H5 支付链接", "payment_mismatch")
        logger.info("wechat", "已创建 H5 订单", 订单号=number, 金额分=amount_cents)
        return {"h5_url": url}

    def jsapi(self, number, amount_cents, description, notify_url, time_expire, openid, client_ip="") -> dict:
        """公众号 / 小程序支付。返回 wx.requestPayment / WeixinJSBridge 需要的调起参数。"""
        body = self._order_body(number, amount_cents, description, notify_url, time_expire, client_ip)
        body["payer"] = {"openid": openid}
        prepay_id = self._prepay_id(self._request("POST", "/v3/pay/transactions/jsapi", body=body))
        logger.info("wechat", "已创建 JSAPI 订单", 订单号=number, 金额分=amount_cents)
        return self.jsapi_params(prepay_id)

    def app(self, number, amount_cents, description, notify_url, time_expire, client_ip="") -> dict:
        """APP 支付。返回开放平台 SDK 调起微信所需的参数。"""
        prepay_id = self._prepay_id(self._request("POST", "/v3/pay/transactions/app", body=self._order_body(number, amount_cents, description, notify_url, time_expire, client_ip)))
        logger.info("wechat", "已创建 APP 订单", 订单号=number, 金额分=amount_cents)
        return self.app_params(prepay_id)

    @staticmethod
    def _prepay_id(data: dict) -> str:
        prepay_id = data.get("prepay_id")
        if not isinstance(prepay_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", prepay_id):
            raise WechatError("微信支付没有返回有效的预支付交易会话标识", "payment_mismatch")
        return prepay_id

    def jsapi_params(self, prepay_id: str) -> dict:
        timestamp, nonce, package = str(int(self._clock())), _nonce(), f"prepay_id={prepay_id}"
        return {"appId": self.app_id, "timeStamp": timestamp, "nonceStr": nonce, "package": package, "signType": "RSA",
                "paySign": self._sign(f"{self.app_id}\n{timestamp}\n{nonce}\n{package}\n")}

    def app_params(self, prepay_id: str) -> dict:
        timestamp, nonce = str(int(self._clock())), _nonce()
        return {"appid": self.app_id, "partnerid": self.mch_id, "prepayid": prepay_id, "package": "Sign=WXPay", "noncestr": nonce, "timestamp": timestamp,
                "sign": self._sign(f"{self.app_id}\n{timestamp}\n{nonce}\n{prepay_id}\n")}

    # ------------------------------------------------------------------ 查单
    def query(self, number: str) -> dict:
        """按商户订单号查单。订单不存在时返回 is_success=False，其余错误抛出 WechatError。"""
        try:
            data = self._request("GET", f"/v3/pay/transactions/out-trade-no/{number}", query={"mchid": self.mch_id})
        except WechatError as exc:
            if exc.status == 404 or exc.provider_code in {"ORDER_NOT_EXIST", "RESOURCE_NOT_EXISTS"}:
                return {"is_success": False, "sub_code": "ORDER_NOT_EXIST", "trade_state": "", "signature": exc.signature, "sdk_response": {}}
            raise
        amount = data.get("amount") or {}
        result = {"is_success": True, "trade_state": str(data.get("trade_state", "")), "out_trade_no": data.get("out_trade_no"),
                  "transaction_id": data.get("transaction_id") or "", "appid": data.get("appid"), "mchid": data.get("mchid"),
                  "amount_total": amount.get("total"), "currency": amount.get("currency", "CNY"), "success_time": data.get("success_time") or ""}
        result["sdk_response"] = logger.safe_params({k: v for k, v in result.items() if k != "sdk_response"})
        logger.info("wechat", "交易查询完成", 订单号=number, 交易状态=result["trade_state"])
        return result

    # ------------------------------------------------------------------ 关单
    def close(self, number: str) -> dict:
        """关闭未支付订单；只有验签通过的 HTTP 204 才算关闭成功。"""
        try:
            self._request(
                "POST",
                f"/v3/pay/transactions/out-trade-no/{number}/close",
                body={"mchid": self.mch_id},
                expected_status=204,
            )
        except WechatError as exc:
            # 微信明确答复 ORDER_CLOSED 时，订单已经处于目标状态；只有验签
            # 通过才能把这个答复当作 provider 的确定结论，否则结果不确定。
            if exc.provider_code != "ORDER_CLOSED" or exc.signature is not True:
                raise
        logger.info("wechat", "交易关闭完成", 订单号=number)
        return {"is_success": True, "out_trade_no": number, "trade_state": "CLOSED"}
    # ------------------------------------------------------------------ 退款
    @staticmethod
    def _refund_result(data: dict) -> dict:
        amount = data.get("amount") or {}
        return {"status": str(data.get("status", "")), "out_refund_no": data.get("out_refund_no"), "refund_id": data.get("refund_id") or "",
                "out_trade_no": data.get("out_trade_no"), "transaction_id": data.get("transaction_id") or "",
                "amount_refund": amount.get("refund"), "amount_total": amount.get("total")}

    def refund(self, number, refund_no, refund_cents, total_cents, reason="", transaction_id="") -> dict:
        body = {"out_refund_no": refund_no, "amount": {"refund": refund_cents, "total": total_cents, "currency": "CNY"}}
        body.update({"transaction_id": transaction_id} if transaction_id else {"out_trade_no": number})
        if reason:
            body["reason"] = reason[:80]
        result = self._refund_result(self._request("POST", "/v3/refund/domestic/refunds", body=body))
        logger.info("wechat", "退款申请完成", 订单号=number, 退款单号=refund_no, 状态=result["status"])
        return result

    def query_refund(self, refund_no: str) -> dict:
        result = self._refund_result(self._request("GET", f"/v3/refund/domestic/refunds/{refund_no}"))
        logger.info("wechat", "退款查询完成", 退款单号=refund_no, 状态=result["status"])
        return result

    # ------------------------------------------------------------------ 回调
    def read_notification(self, headers, body: bytes) -> dict:
        """验签并解密支付通知，返回 {"event_type", "transaction"}。验签失败时抛出 WechatError。"""
        self.verify(headers, body)
        try:
            document = json.loads(body)
            resource = document["resource"]
            if resource.get("algorithm") != "AEAD_AES_256_GCM":
                raise ValueError("algorithm")
            plain = self._aead.decrypt(resource["nonce"].encode(), base64.b64decode(resource["ciphertext"], validate=True), (resource.get("associated_data") or "").encode() or None)
            transaction = json.loads(plain)
        except (ValueError, KeyError, TypeError, InvalidTag, AttributeError):
            raise WechatError("微信支付通知无法解密，请核对 APIv3 密钥", "wechat_notification_invalid") from None
        return {"event_type": document.get("event_type", ""), "transaction": transaction}

    # ------------------------------------------------------------------ 连通性检查
    def check(self) -> dict:
        """不产生订单的在线检查：用一个不存在的订单号查单。

        authenticated  微信支付接受了商户号 + 证书序列号 + 商户私钥的签名
        verified       True 微信支付公钥和公钥 ID 验证通过；False 公钥或公钥 ID 不对；None 应答没有签名，无法确认
        """
        try:
            result = self.query("BYPAY" + secrets.token_hex(8).upper())
        except WechatError as exc:
            return {"authenticated": False, "verified": None, "code": exc.code, "provider_code": exc.provider_code}
        return {"authenticated": True, "verified": result.get("signature"), "code": "", "provider_code": "ORDER_NOT_EXIST"}
