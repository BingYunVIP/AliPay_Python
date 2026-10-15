"""支付宝官方 SDK 封装。

使用完整官方 SDK：alipay-sdk-python（PyPI 同名包，含全部开放平台接口）。
本模块只做「薄封装」：把配置塞进 SDK，把常用接口包装成简单方法。

已封装：
    create_page_pay_url   电脑网站支付（生成跳转链接）
    query                 交易查询（本地测试检测支付结果的关键）
    close                 交易关闭
    refund                交易退款
    verify_notify         异步通知验签

后续可扩展：wap（手机网站）、precreate（当面付扫码）、app（APP 支付）……
"""

from __future__ import annotations

import json

from . import constants, logger
from .key_utils import normalize_private_key, normalize_public_key


class AlipayError(RuntimeError):
    """支付宝调用相关错误。"""


def _sdk_imports():
    """延迟导入，便于在缺少 SDK 时给出友好提示。"""
    try:
        from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
        from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
        from alipay.aop.api.domain.AlipayTradePagePayModel import (
            AlipayTradePagePayModel,
        )
        from alipay.aop.api.domain.AlipayTradeQueryModel import (
            AlipayTradeQueryModel,
        )
        from alipay.aop.api.domain.AlipayTradeCloseModel import (
            AlipayTradeCloseModel,
        )
        from alipay.aop.api.domain.AlipayTradeRefundModel import (
            AlipayTradeRefundModel,
        )
        from alipay.aop.api.request.AlipayTradePagePayRequest import (
            AlipayTradePagePayRequest,
        )
        from alipay.aop.api.request.AlipayTradeQueryRequest import (
            AlipayTradeQueryRequest,
        )
        from alipay.aop.api.request.AlipayTradeCloseRequest import (
            AlipayTradeCloseRequest,
        )
        from alipay.aop.api.request.AlipayTradeRefundRequest import (
            AlipayTradeRefundRequest,
        )
        from alipay.aop.api.util import SignatureUtils
    except ImportError as exc:  # pragma: no cover
        missing = getattr(exc, "name", None) or str(exc)
        raise AlipayError(
            f"无法导入支付宝官方 SDK（缺失模块：{missing}）。\n"
            "  最常见原因：用了系统 python3，而不是项目虚拟环境。\n"
            "  正确运行方式：./run.sh   或   .venv/bin/python main.py\n"
            "  若确实缺少依赖：.venv/bin/pip install -r requirements.txt"
        ) from exc
    return locals()


# 电脑网站支付的固定产品码
PRODUCT_CODE_PAGE = "FAST_INSTANT_TRADE_PAY"


class AlipayClient:
    """对官方 DefaultAlipayClient 的薄封装。"""

    def __init__(self, cfg: dict):
        sdk = _sdk_imports()
        self._sdk = sdk
        self.cfg = cfg

        gateway = cfg.get("gateway") or constants.GATEWAY_BY_ENV.get(
            cfg.get("env", "sandbox"), constants.GATEWAY_SANDBOX
        )

        conf = sdk["AlipayClientConfig"]()
        conf.server_url = gateway
        conf.app_id = cfg.get("app_id", "")
        # 关键：归一化密钥格式
        #   私钥 → PKCS#1 PEM（SDK 底层 rsa 库的硬性要求）
        #   公钥 → 裸 Base64（SDK 会自行补 PEM 头尾）
        try:
            conf.app_private_key = normalize_private_key(cfg.get("app_private_key", ""))
        except Exception as exc:  # noqa: BLE001
            raise AlipayError(f"应用私钥格式无法识别：{exc}") from exc
        try:
            conf.alipay_public_key = normalize_public_key(cfg.get("alipay_public_key", ""))
        except Exception as exc:  # noqa: BLE001
            raise AlipayError(f"支付宝公钥格式无法识别：{exc}") from exc
        conf.sign_type = cfg.get("sign_type") or "RSA2"
        conf.charset = cfg.get("charset") or "utf-8"
        conf.format = "json"
        conf.timeout = 15

        self._client = sdk["DefaultAlipayClient"](alipay_client_config=conf)

    # ---------------------------------------------------------- 电脑网站支付
    def create_page_pay_url(
        self,
        out_trade_no: str,
        amount: str,
        subject: str,
        timeout_express: str = "15m",
        http_method: str = "GET",
    ) -> str:
        """生成电脑网站支付跳转链接（默认 GET，直接返回 URL 字符串）。"""
        model = self._sdk["AlipayTradePagePayModel"]()
        model.out_trade_no = out_trade_no
        model.total_amount = str(amount)
        model.subject = subject
        model.product_code = PRODUCT_CODE_PAGE
        model.timeout_express = timeout_express

        request = self._sdk["AlipayTradePagePayRequest"](biz_model=model)
        if self.cfg.get("notify_url"):
            request.notify_url = self.cfg["notify_url"]
        if self.cfg.get("return_url"):
            request.return_url = self.cfg["return_url"]

        try:
            url = self._client.page_execute(request, http_method=http_method)
        except Exception as exc:  # noqa: BLE001
            logger.error("alipay", "生成支付链接失败", 异常类型=type(exc).__name__, 订单号=out_trade_no)
            raise AlipayError(f"生成支付链接失败：{exc}") from exc

        logger.info(
            "alipay", "已生成支付链接",
            订单号=out_trade_no, 金额=amount, 环境=self.cfg.get("env"),
        )
        return url

    def create_wap_pay_url(self, out_trade_no: str, amount: str, subject: str) -> str:
        """Sign a mobile checkout URL locally; this does not contact Alipay."""
        from alipay.aop.api.domain.AlipayTradeWapPayModel import AlipayTradeWapPayModel
        from alipay.aop.api.request.AlipayTradeWapPayRequest import AlipayTradeWapPayRequest

        model = AlipayTradeWapPayModel()
        model.out_trade_no = out_trade_no
        model.total_amount = amount
        model.subject = subject
        model.product_code = "QUICK_WAP_WAY"
        model.timeout_express = "15m"
        request = AlipayTradeWapPayRequest(biz_model=model)
        request.notify_url = self.cfg.get("notify_url", "")
        request.return_url = self.cfg.get("return_url", "")
        try:
            return self._client.page_execute(request, http_method="GET")
        except Exception as exc:
            raise AlipayError("生成手机网站支付链接失败") from exc

    # ---------------------------------------------------------- 交易查询
    def query(self, out_trade_no: str = "", trade_no: str = "") -> dict:
        """查询交易状态。返回 dict（含 trade_status / trade_no / total_amount 等）。"""
        if not out_trade_no and not trade_no:
            raise AlipayError("查询交易必须提供 out_trade_no 或 trade_no")

        model = self._sdk["AlipayTradeQueryModel"]()
        if out_trade_no:
            model.out_trade_no = out_trade_no
        if trade_no:
            model.trade_no = trade_no

        request = self._sdk["AlipayTradeQueryRequest"](biz_model=model)
        try:
            raw = self._client.execute(request)
        except Exception as exc:  # noqa: BLE001
            logger.error("alipay", "交易查询失败", 异常类型=type(exc).__name__, 订单号=out_trade_no or trade_no)
            raise _wrap_api_error(exc, "交易查询") from exc

        data = _parse_response(raw)
        result = {
            "is_success": data.get("code") == "10000",
            "code": data.get("code", ""),
            "msg": data.get("msg", ""),
            "sub_code": data.get("sub_code", ""),
            "sub_msg": data.get("sub_msg", ""),
            "trade_no": data.get("trade_no", ""),
            "out_trade_no": data.get("out_trade_no", ""),
            "trade_status": data.get("trade_status", ""),
            "total_amount": data.get("total_amount", ""),
            "buyer_logon_id": data.get("buyer_logon_id", ""),
            "send_pay_date": str(data.get("send_pay_date", "")),
            "sdk_response": logger.safe_params(data),
        }
        logger.info(
            "alipay", "交易查询完成",
            订单号=out_trade_no or trade_no,
            交易状态=result.get("trade_status") or "未支付",
            返回码=result.get("code"),
            子错误=result.get("sub_code") or "-",
        )
        return result

    # ---------------------------------------------------------- 交易关闭
    def close(self, out_trade_no: str = "", trade_no: str = "") -> dict:
        model = self._sdk["AlipayTradeCloseModel"]()
        if out_trade_no:
            model.out_trade_no = out_trade_no
        if trade_no:
            model.trade_no = trade_no

        request = self._sdk["AlipayTradeCloseRequest"](biz_model=model)
        try:
            raw = self._client.execute(request)
        except Exception as exc:  # noqa: BLE001
            raise _wrap_api_error(exc, "交易关闭") from exc

        data = _parse_response(raw)
        return {
            "is_success": data.get("code") == "10000",
            "code": data.get("code", ""),
            "msg": data.get("msg", ""),
            "sub_code": data.get("sub_code", ""),
            "sub_msg": data.get("sub_msg", ""),
        }

    # ---------------------------------------------------------- 交易退款
    def refund(self, out_trade_no: str, amount: str, refund_reason: str = "测试退款") -> dict:
        model = self._sdk["AlipayTradeRefundModel"]()
        model.out_trade_no = out_trade_no
        model.refund_amount = str(amount)
        model.refund_reason = refund_reason

        request = self._sdk["AlipayTradeRefundRequest"](biz_model=model)
        try:
            raw = self._client.execute(request)
        except Exception as exc:  # noqa: BLE001
            raise _wrap_api_error(exc, "退款") from exc

        data = _parse_response(raw)
        return {
            "is_success": data.get("code") == "10000",
            "code": data.get("code", ""),
            "msg": data.get("msg", ""),
            "sub_code": data.get("sub_code", ""),
            "sub_msg": data.get("sub_msg", ""),
            "refund_fee": data.get("refund_fee", ""),
        }

    # ---------------------------------------------------------- 异步通知验签
    @staticmethod
    def build_sign_content(params: dict) -> str:
        """构建待验签字符串（严格对齐支付宝官方规则）。

        规则：
          1. 剔除 sign、sign_type
          2. 剔除空值
          3. 按参数名 ASCII 字典序排序
          4. 拼成 k1=v1&k2=v2&...
        """
        items = []
        for key in sorted(params.keys()):
            if key in ("sign", "sign_type"):
                continue
            value = params[key]
            if value is None or value == "":
                continue
            items.append(f"{key}={value}")
        return "&".join(items)

    @staticmethod
    def verify_notify(params: dict, alipay_public_key: str) -> bool:
        """验证支付宝异步通知的签名。

        params 为回调 POST 上来的表单参数（dict）。
        """
        sign = params.get("sign")
        if not sign or params.get("sign_type") != "RSA2":
            return False
        try:
            import base64
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa

            content = AlipayClient.build_sign_content(params)
            raw_key = base64.b64decode(normalize_public_key(alipay_public_key))
            key = serialization.load_der_public_key(raw_key)
            if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
                return False
            key.verify(base64.b64decode(sign, validate=True), content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
            return True
        except Exception:  # noqa: BLE001
            return False


def interpret_query(result: dict) -> str:
    """把查询结果翻译成一句人话，便于理解当前处于什么状态。"""
    code = result.get("code", "")
    sub_code = result.get("sub_code") or ""
    status = result.get("trade_status") or ""

    if code == "10000" and status:
        return {
            "WAIT_BUYER_PAY": "等待买家付款（订单已在支付宝生成，但尚未支付）",
            "TRADE_SUCCESS": "支付成功",
            "TRADE_FINISHED": "交易完结",
            "TRADE_CLOSED": "交易已关闭",
        }.get(status, f"交易状态：{status}")

    if sub_code == "ACQ.TRADE_NOT_EXIST":
        return (
            "订单在支付宝侧「尚未生成」\n"
            "      （电脑网站支付只有在用户打开收银台页面后才会真正建单，\n"
            "        仅生成链接、未打开页面时查不到，属正常现象）"
        )

    return f"未获取到交易状态（{code} {sub_code}）".strip()


def interpret_close(result: dict) -> str:
    """把关闭结果翻译成一句人话。"""
    code = result.get("code", "")
    sub_code = result.get("sub_code") or ""
    if code == "10000":
        return "订单已成功关闭"
    if sub_code == "ACQ.TRADE_NOT_EXIST":
        return (
            "订单在支付宝侧「尚未生成」，无需关闭\n"
            "      （用户从未打开过收银台页面；已支付的订单也不能关闭）"
        )
    return f"关闭失败（{code} {sub_code} {result.get('sub_msg') or ''}）".strip()


def _parse_response(raw) -> dict:
    """把 SDK 的响应统一解析为 dict。

    ⚠️ 重要：alipay-sdk-python 的 `execute()` 返回的是 **JSON 字符串**，
    不是响应对象（这是该 SDK 一个很容易踩的坑）。
    若直接对它做 getattr(resp, "code") 会永远得到空值，
    表现为「查询永远显示未支付」。
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}
        return data if isinstance(data, dict) else {"_raw": text}
    # 兼容将来 SDK 改为返回对象的情况
    if hasattr(raw, "__dict__"):
        return {k: v for k, v in vars(raw).items() if not k.startswith("_")}
    return {}


def _wrap_api_error(exc: Exception, action: str) -> "AlipayError":
    """把 SDK 抛出的晦涩异常翻译成可操作的提示。"""
    msg = str(exc)

    if "response sign verify failed" in msg:
        return AlipayError(
            f"{action}失败：**响应验签失败** —— 「支付宝公钥」配置不正确。\n"
            "  最常见原因：把「应用公钥」填到了「支付宝公钥」的位置（两者很像，极易混淆）。\n"
            "  正确路径：开放平台 → 我的应用 → 应用详情 → 开发设置 → 接口加签方式\n"
            "            → 点击「查看支付宝公钥」复制（不是应用公钥！）\n"
            "  快速自检：进入「6 环境自检」，会直接告诉你是否填反。"
        )

    if "invalid-app-id" in msg or "无效的APPID" in msg:
        return AlipayError(
            f"{action}失败：APPID 无效 —— 多半是「运行环境」与 APPID 不匹配。\n"
            "  正式 APPID（2021 开头）→ 运行环境应选 production\n"
            "  沙箱 APPID（9021 开头）→ 运行环境应选 sandbox\n"
            "  可在「1 配置」中修改运行环境（会自动切换网关地址）。"
        )

    if "isv.invalid-signature" in msg or "sign check fail" in msg.lower():
        return AlipayError(
            f"{action}失败：请求签名无效 —— 「应用私钥」与开放平台配置的「应用公钥」不配对。\n"
            "  请确认开放平台「接口加签方式」中粘贴的应用公钥，"
            "确实由当前配置里的应用私钥生成。"
        )

    return AlipayError(f"{action}失败：{exc}")


def self_check(cfg: dict) -> list:
    """配置完整性 + SDK 可用性自检。返回 [(是否通过, 描述), ...]"""
    results = []
    try:
        _sdk_imports()
        results.append((True, "支付宝官方 SDK 已安装"))
    except AlipayError as exc:
        results.append((False, str(exc).splitlines()[0]))
        return results

    for key in sorted(constants.REQUIRED_KEYS):
        field = constants.FIELD_MAP[key]
        results.append(
            (bool(cfg.get(key)), f"配置项「{field['label']}」{'已填写' if cfg.get(key) else '缺失'}")
        )

    env = cfg.get("env", "sandbox")
    results.append((True, f"当前环境：{env}"))
    if not cfg.get("notify_url"):
        results.append((True, "未配置 notify_url → 将使用「主动查询」检测支付结果（本地测试推荐）"))
    return results
