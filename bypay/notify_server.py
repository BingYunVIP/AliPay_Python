"""本地异步通知（notify）接收服务。

用途：
    支付宝支付成功后，会向 notify_url 发起**服务器间 POST 请求**（异步通知）。
    本服务用于在本地接收、验签、并自动更新订单状态。

    同时接收**同步跳转（return_url）**：用户支付完成后，浏览器会跳回该地址。

为什么需要内网穿透：
    notify_url 必须**公网可达**（是支付宝服务器来访问你，不是你访问支付宝）。
    本地开发时用 `bypay/tunnel.py` 把本服务映射为公网 https 地址即可。

对外路径：
    POST /alipay/notify    异步通知（支付宝服务器 → 本服务）
    GET  /alipay/return    同步跳转（用户浏览器 → 本服务）
    GET  /health           健康检查（用于验证隧道是否打通）
    GET  /                 状态页

安全边界：
    - 异步通知**必须验签**后才可信（用支付宝公钥验签）
    - 验签失败的通知一律忽略（防止伪造到账）
    - 置单是**幂等**的：同一订单重复通知不会重复处理
"""

from __future__ import annotations

import json
import threading
from html import escape
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import constants, logger, ui
from .alipay_client import AlipayClient
from .payment_validation import validate_notification

# 回调记录落盘（便于重启后追溯）
RECORDS_FILE = constants.DATA_DIR / "notify_records.jsonl"
_MAX_RECORDS = 200


def _append_record_file(record: dict) -> None:
    try:
        RECORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(RECORDS_FILE, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_records(limit: int = 20) -> list:
    """从落盘文件读取最近的通知记录。"""
    if not RECORDS_FILE.exists():
        return []
    try:
        lines = RECORDS_FILE.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


def clear_records_file() -> None:
    try:
        RECORDS_FILE.unlink()
    except FileNotFoundError:
        pass


class _NotifyHandler(BaseHTTPRequestHandler):
    server_version = "ByPay-Notify/0.2"
    protocol_version = "HTTP/1.1"

    # 由 NotifyServer 注入
    alipay_public_key = ""
    order_store = None
    debug_skip_verify = False
    app_id = ""
    seller_id = ""
    records: list = []

    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass

    # ------------------------------------------------------ POST：异步通知
    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        self.connection.settimeout(10)
        if path not in {"/payments/notify", "/alipay/notify"} or urlparse(self.path).query:
            self.close_connection = True
            self._respond(404, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 32768 or self.headers.get_content_type() != "application/x-www-form-urlencoded":
                raise ValueError("invalid body")
            values = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True, max_num_fields=64)
            if any(len(v) != 1 for v in values.values()):
                raise ValueError("duplicate field")
            params = {k: v[0] for k, v in values.items()}
        except (ValueError, OSError):
            self.close_connection = True
            self._respond(400, "failure")
            return

        if path in {"/payments/notify", "/alipay/notify"}:
            self._handle_notify(path, params)
            return

        # 其它路径的 POST 只记录，不处理
        self._record({
            "kind": "other",
            "method": "POST",
            "path": path,
            "remote": self.client_address[0],
            "params": params,
        })
        self._respond(404, "not found")

    def _handle_notify(self, path: str, params: dict) -> None:
        out_trade_no = params.get("out_trade_no", "")
        trade_status = params.get("trade_status", "")

        verified = False
        if self.alipay_public_key:
            verified = AlipayClient.verify_notify(params, self.alipay_public_key)
            verify_note = "验签通过" if verified else "验签失败"
        else:
            verify_note = "未配置支付宝公钥，无法验签"

        if verified:
            try:
                validate_notification(params, self.order_store.get(out_trade_no) if self.order_store else None, self.app_id, self.seller_id)
            except ValueError as exc:
                verified = False
                verify_note = f"业务校验失败：{exc}"
        handled = ""
        if not verified:
            handled = "验签未通过，已忽略（不会置单）"
        elif trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            if self.order_store and out_trade_no:
                before = self.order_store.get(out_trade_no)
                existed = before is not None
                self.order_store.mark_paid(
                    out_trade_no, params.get("trade_no", ""), params
                )
                handled = "已置为支付成功" + ("" if existed else "（本地无此订单，已仅记录）")
            else:
                handled = "交易成功，但本地无订单存储，未置单"
        elif trade_status == "TRADE_CLOSED":
            if self.order_store and out_trade_no:
                self.order_store.update(out_trade_no, status="CLOSED", raw=params)
            handled = "交易已关闭，本地状态置 CLOSED"
        elif trade_status == "WAIT_BUYER_PAY":
            handled = "买家待付款，无需处理"
        else:
            handled = f"交易状态 {trade_status}，未处理"

        self._record({
            "kind": "notify",
            "method": "POST",
            "path": path,
            "remote": self.client_address[0],
            "params": params,
            "verified": verified,
            "verify_note": verify_note,
            "handled": handled,
        })

        logger.info(
            "notify", "收到异步通知",
            路径=path,
            来源=self.client_address[0],
            订单号=out_trade_no,
            交易状态=trade_status,
            验签=verify_note,
            处理=handled,
            参数=logger.safe_params(params),
        )

        print()
        print(ui.green(f"  [回调] {datetime.now():%H:%M:%S} 收到支付宝异步通知"))
        ui.hint(f"来源 IP  ：{self.client_address[0]}")
        ui.hint(f"订单号   ：{out_trade_no}")
        ui.hint(f"交易状态 ：{trade_status}")
        ui.hint(f"验签结果 ：{verify_note}")
        ui.hint(f"处理结果 ：{handled}")

        # 支付宝要求：处理成功必须返回纯文本 "success"，否则会重复通知
        if verified:
            self._respond(200, "success")
        else:
            self._respond(200, "failure")

    # ------------------------------------------------------ GET：同步跳转 / 健康检查
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}

        if path in {"/payment/return", "/alipay/return"}:
            self._handle_return(path, params)
            return

        if path == "/health":
            payload = json.dumps(
                {"ok": True, "service": "bypay-notify", "time": datetime.now().isoformat()},
                ensure_ascii=False,
            )
            self._respond(200, payload, content_type="application/json; charset=utf-8")
            return

        # 状态页
        self._respond(
            200,
            "<html><head><meta charset='utf-8'><title>ByPay 回调服务</title></head>"
            "<body style='font-family:system-ui,sans-serif;padding:40px;line-height:1.8'>"
            "<h2>ByPay · 本地回调服务运行中</h2>"
            "<p>可用路径：</p>"
            "<ul>"
            "<li><code>POST /payments/notify</code> — 支付异步通知</li>"
            "<li><code>GET /payment/return</code> — 支付完成同步跳转</li>"
            "<li><code>GET /health</code> — 健康检查</li>"
            "</ul>"
            "<p style='color:#888'>这是本地测试服务，请勿用于生产环境。</p>"
            "</body></html>",
            content_type="text/html; charset=utf-8",
        )

    def _handle_return(self, path: str, params: dict) -> None:
        out_trade_no = params.get("out_trade_no", "")
        trade_no = params.get("trade_no", "")
        amount = params.get("total_amount", "")
        seller_id = params.get("seller_id", "")

        # 同步跳转不做验签（仅用于展示）；到账以异步通知/主动查询为准
        self._record({
            "kind": "return",
            "method": "GET",
            "path": path,
            "remote": self.client_address[0],
            "params": params,
        })
        logger.info(
            "notify", "收到同步跳转",
            路径=path,
            来源=self.client_address[0],
            订单号=out_trade_no,
            参数=logger.safe_params(params),
        )

        def row(label: str, value: str) -> str:
            if not value:
                return ""
            return (
                f"<tr><td style='padding:6px 16px 6px 0;color:#666'>{label}</td>"
                f"<td style='padding:6px 0'><b>{escape(value)}</b></td></tr>"
            )

        html = (
            "<html><head><meta charset='utf-8'><title>支付完成</title></head>"
            "<body style='font-family:system-ui,sans-serif;padding:48px;line-height:1.7'>"
            "<h2 style='color:#1677ff'>支付流程已返回</h2>"
            "<table>"
            + row("商户订单号", out_trade_no)
            + row("支付宝交易号", trade_no)
            + row("订单金额", (f"￥{amount}" if amount else ""))
            + row("收款账号", seller_id)
            + "</table>"
            "<p style='color:#b45309;background:#fffbeb;border-left:4px solid #f59e0b;"
            "padding:12px 16px;max-width:640px'>"
            "<b>注意：</b>同步跳转只代表用户走完了页面流程，"
            "<b>不能作为「已到账」的依据</b>。到账请以异步通知或订单查询为准。</p>"
            "</body></html>"
        )
        self._respond(200, html, content_type="text/html; charset=utf-8")

    # ------------------------------------------------------ 工具
    def _record(self, record: dict) -> None:
        record = logger.safe_params(record)
        record["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cls = self.__class__
        cls.records.append(record)
        if len(cls.records) > _MAX_RECORDS:
            del cls.records[:-_MAX_RECORDS]
        _append_record_file(record)

    def _respond(self, code: int, text: str, content_type: str = "text/plain; charset=utf-8"):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)


class NotifyServer:
    """把 ThreadingHTTPServer 包成可启动/停止的对象。"""

    def __init__(
        self,
        alipay_public_key: str,
        order_store,
        port: int,
        debug_skip_verify: bool = False,
        app_id: str = "",
        seller_id: str = "",
    ):
        if debug_skip_verify:
            raise ValueError("不允许跳过验签，请使用隔离的 tests 自测")
        self.alipay_public_key = alipay_public_key
        self.order_store = order_store
        self.port = port
        self.debug_skip_verify = debug_skip_verify
        self.app_id = app_id
        self.seller_id = seller_id
        self._httpd = None
        self._thread = None

    # ------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self.is_running:
            return
        if not self.app_id or not self.seller_id:
            raise ValueError("回调服务必须配置 APP ID 与收款方 UID")
        handler = type("BoundNotifyHandler", (_NotifyHandler,), {
            "alipay_public_key": self.alipay_public_key, "order_store": self.order_store,
            "app_id": self.app_id, "seller_id": self.seller_id,
        })
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info(
            "notify", "回调服务已启动",
            端口=self.port,
            验签="已启用" if self.alipay_public_key else "未配置公钥",
            调试跳过验签=self.debug_skip_verify,
        )

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        self._httpd = None
        self._thread = None
        logger.info("notify", "回调服务已停止")

    # ------------------------------------------------------ 运行时调整
    def set_debug_skip_verify(self, enabled: bool) -> None:
        if enabled:
            raise ValueError("禁止跳过回调验签")

    def set_public_key(self, key: str) -> None:
        if self.is_running:
            raise ValueError("停止回调服务后才能更换公钥")
        self.alipay_public_key = key

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    # ------------------------------------------------------ 记录
    @staticmethod
    def recent(limit: int = 10, include_persisted: bool = True) -> list:
        """内存中的最近记录；为空时可回退读取落盘记录。

        ⚠️ 注意：本方法在内存为空时会回退读文件，
        因此**不要**用它做「本次新增了几条」的计数基线
        （基线会偏高，导致新记录被漏判）。计数请用 recent_memory()。
        """
        rows = list(reversed(_NotifyHandler.records))[:limit]
        if rows:
            return rows
        if include_persisted:
            return load_records(limit)
        return []

    @staticmethod
    def recent_memory(limit: int = 100) -> list:
        """**仅**返回本次进程内收到的记录（绝不回退读文件）。

        适合做「新增记录计数」与实时监听：
        基线恒为 0，新增一条就多一条，计数单调递增。
        """
        return list(reversed(_NotifyHandler.records))[:limit]

    @staticmethod
    def memory_count() -> int:
        """本次进程内累计收到的记录数（单调递增，不受 limit 影响）。"""
        return len(_NotifyHandler.records)

    @staticmethod
    def clear() -> None:
        _NotifyHandler.records.clear()
        clear_records_file()

    def urls(self) -> dict:
        base = f"http://127.0.0.1:{self.port}"
        return {
            "notify": f"{base}/payments/notify",
            "return": f"{base}/payment/return",
            "health": f"{base}/health",
        }
