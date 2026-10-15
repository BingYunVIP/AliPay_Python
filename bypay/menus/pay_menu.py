"""「3 测试支付」菜单：发起 0.01 元网页支付，并检测支付结果。

本地测试的关键设计：
    支付宝的异步通知（notify）需要公网可达地址。
    若你没有公网地址，本工具用「主动查询订单」（alipay.trade.query）
    按设定间隔轮询，一样能准确判断是否支付成功——且不依赖公网回调。
"""

from __future__ import annotations

import time

from .. import constants, logger, qr_tool, ui
from ..alipay_client import AlipayClient, AlipayError, interpret_close, interpret_query
from ..config_store import ConfigError, ConfigStore
from ..order_store import OrderStore
from ..settings_store import SettingsStore

PAID_STATES = {"TRADE_SUCCESS", "TRADE_FINISHED"}

# 支付宝交易状态 -> 本地订单状态
TRADE_STATUS_MAP = {
    "TRADE_SUCCESS": "PAID",
    "TRADE_FINISHED": "PAID",
    "TRADE_CLOSED": "CLOSED",
    "WAIT_BUYER_PAY": "CREATED",
}


def _make_trade_no() -> str:
    """生成商户订单号：BYP + 时间戳 + 随机数。"""
    import random

    return f"BYP{time.strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"


def _seconds_to_express(seconds: int) -> str:
    """把秒数转成支付宝的 timeout_express 格式（如 15m / 1h）。"""
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{max(1, seconds // 60)}m"


class PayMenu:
    def __init__(self, cfg_store: ConfigStore, set_store: SettingsStore, order_store: OrderStore):
        self.cfg_store = cfg_store
        self.set_store = set_store
        self.order_store = order_store

    # ------------------------------------------------------------ 主循环
    def run(self) -> None:
        while True:
            data = self.set_store.load()
            ui.clear()
            ui.title("3 · 测试支付")
            print()
            print(f"  1. 发起一笔网页支付     （金额 {data['test_amount']} 元）")
            print("  2. 查询订单状态         （主动查询 alipay.trade.query）")
            print("  3. 重新显示支付链接")
            print("  4. 关闭订单             （alipay.trade.close）")
            print("  5. 生成支付二维码       （可用手机扫码支付）")
            print("  6. 同步订单状态         （与支付宝核对后修正本地状态）")
            print("  0. 返回上级")
            print()
            choice = input("  请选择: ").strip()

            if choice == "1":
                self._create_and_watch()
            elif choice == "2":
                self._query()
            elif choice == "3":
                self._show_link()
            elif choice == "4":
                self._close()
            elif choice == "5":
                self._make_qr()
            elif choice == "6":
                self._sync_status()
            elif choice == "0":
                return
            else:
                ui.error("无效选项")

    # ------------------------------------------------------------ 取配置
    def _load_cfg(self) -> dict | None:
        try:
            cfg = self.cfg_store.load()
        except ConfigError as exc:
            ui.error(str(exc))
            ui.pause()
            return None
        missing = self.cfg_store.missing_required(cfg)
        if missing:
            labels = "、".join(constants.FIELD_MAP[k]["label"] for k in missing)
            ui.error(f"配置不完整，缺少：{labels}")
            ui.hint("请先进入「1 配置 → 引导式填写全部配置」")
            ui.pause()
            return None
        return cfg

    # ------------------------------------------------------------ 1 发起支付
    def _create_and_watch(self) -> None:
        ui.clear()
        ui.title("3.1 · 发起网页支付")
        cfg = self._load_cfg()
        if not cfg:
            return
        settings = self.set_store.load()

        out_trade_no = _make_trade_no()
        amount = str(settings["test_amount"])
        subject = "ByPay 测试订单"
        express = _seconds_to_express(int(settings["order_timeout"]))

        print()
        ui.info(f"商户订单号：{out_trade_no}")
        ui.info(f"金额      ：{amount} 元")
        ui.info(f"有效期    ：{settings['order_timeout']} 秒（{express}）")
        ui.info(f"环境      ：{cfg.get('env')}")
        print()

        try:
            client = AlipayClient(cfg)
            pay_url = client.create_page_pay_url(
                out_trade_no=out_trade_no,
                amount=amount,
                subject=subject,
                timeout_express=express,
            )
        except AlipayError as exc:
            ui.error(str(exc))
            ui.pause()
            return

        self.order_store.add(out_trade_no, subject, amount, pay_url)
        logger.info("order", "发起测试支付", 订单号=out_trade_no, 金额=amount, 环境=cfg.get("env"))

        ui.ok("支付链接已生成")
        print()
        print(ui.cyan("  ┌─ 请在浏览器打开下面的链接完成支付 ──────────────────────"))
        print()
        print(pay_url)
        print()
        print(ui.cyan("  └───────────────────────────────────────────────────────"))
        print()
        if cfg.get("env") == "sandbox":
            ui.warn("沙箱环境：请用「沙箱买家账号」登录支付（开放平台 → 沙箱环境 可查看账号密码）")
        ui.hint("提示：金额很小（默认 0.01 元），支付后本工具会自动检测结果")

        print()
        if ui.confirm("是否生成二维码图片（可用手机支付宝扫码支付）？", default=True):
            self._render_qr(out_trade_no, pay_url, amount)

        print()
        if not ui.confirm("是否开始自动检测支付结果？", default=True):
            ui.pause()
            return

        self._watch(out_trade_no, int(settings["poll_interval"]), int(settings["order_timeout"]))

    # ------------------------------------------------------------ 同步状态
    def _sync_status(self) -> None:
        """与支付宝核对，修正本地订单状态。"""
        ui.clear()
        ui.title("3.6 · 同步订单状态")
        cfg = self._load_cfg()
        if not cfg:
            return

        orders = self.order_store.list(20)
        if not orders:
            ui.warn("暂无订单记录")
            ui.pause()
            return

        print()
        ui.info("将逐个查询支付宝，核对并修正本地状态（已支付的订单会跳过）")
        print()

        client = AlipayClient(cfg)
        changed = 0
        checked = 0

        for o in orders:
            ono = o["out_trade_no"]
            local = o["status"]
            if local == "PAID":
                print(f"  {ono}  本地={local}  {ui.gray('已终态，跳过')}")
                continue

            checked += 1
            try:
                r = client.query(out_trade_no=ono)
            except AlipayError as exc:
                print(f"  {ono}  查询失败：{exc}")
                continue

            remote = r.get("trade_status") or ""
            mapped = TRADE_STATUS_MAP.get(remote)
            code = r.get("code", "")

            if mapped and mapped != local:
                self.order_store.update(ono, status=mapped, raw=r)
                if mapped == "PAID":
                    self.order_store.mark_paid(ono, r.get("trade_no", ""), r)
                print(f"  {ono}  本地={local}  →  {ui.green(mapped)}  （支付宝：{remote}）")
                changed += 1
            elif mapped:
                print(f"  {ono}  本地={local}  支付宝={remote}  {ui.gray('一致')}")
            else:
                hint = f"{code} {r.get('sub_code') or ''}".strip()
                print(f"  {ono}  本地={local}  支付宝未返回状态（{hint}）")

        print()
        ui.ok(f"核对 {checked} 笔，修正 {changed} 笔")
        ui.pause()

    # ------------------------------------------------------------ 二维码
    def _render_qr(self, out_trade_no: str, pay_url: str, amount: str) -> None:
        """生成美化后的支付二维码。"""
        try:
            path = qr_tool.make_beautified_png(
                pay_url,
                constants.QRCODE_DIR / f"{out_trade_no}.png",
                amount=amount,
                out_trade_no=out_trade_no,
            )
        except RuntimeError as exc:
            ui.warn(str(exc))
            return

        info = qr_tool.qr_version_info(pay_url)
        ui.ok(f"二维码已生成：{path}")
        ui.hint(f"规格：版本 {info['version']}，{info['modules']} 个模块（{info['density']}）")
        ui.hint("用手机支付宝「扫一扫」打开即可支付")

        if ui.confirm("是否顺便生成一张「无装饰」的纯二维码？", default=False):
            plain = qr_tool.make_plain_png(
                pay_url, constants.QRCODE_DIR / f"{out_trade_no}_plain.png"
            )
            ui.ok(f"纯二维码已生成：{plain}")

    def _make_qr(self) -> None:
        """为已有订单生成二维码。"""
        ui.clear()
        ui.title("3.5 · 生成支付二维码")
        cfg = self._load_cfg()
        if not cfg:
            return

        orders = self.order_store.list(10)
        if not orders:
            ui.warn("暂无订单记录")
            ui.pause()
            return

        print()
        for i, o in enumerate(orders, start=1):
            print(f"  {i:>2}. {o['out_trade_no']}  {o['amount']} 元  {o['status']}")
        raw = input("\n  请选择要生成二维码的订单: ").strip()
        if not (raw.isdigit() and 1 <= int(raw) <= len(orders)):
            return

        order = orders[int(raw) - 1]
        pay_url = order.get("pay_url") or ""
        if not pay_url:
            ui.warn("该订单没有保存支付链接，无法生成二维码")
            ui.pause()
            return

        self._render_qr(order["out_trade_no"], pay_url, order["amount"])
        ui.pause()

    # ------------------------------------------------------------ 轮询检测
    def _watch(self, out_trade_no: str, interval: int, timeout: int) -> None:
        ui.clear()
        ui.title("3.1 · 正在检测支付结果")
        print()
        ui.info(f"检测间隔：{interval} 秒      订单有效期：{timeout} 秒")
        ui.hint("按 Ctrl+C 可随时中断检测")
        print()

        cfg = self._load_cfg()
        if not cfg:
            return
        client = AlipayClient(cfg)

        started = time.time()
        round_no = 0
        while True:
            elapsed = int(time.time() - started)
            if elapsed > timeout:
                ui.warn(f"已超过订单有效期（{timeout} 秒），停止检测")
                self.order_store.update(out_trade_no, status="TIMEOUT")
                break

            round_no += 1
            stamp = time.strftime("%H:%M:%S")
            try:
                result = client.query(out_trade_no=out_trade_no)
            except AlipayError as exc:
                ui.error(f"[{stamp}] 查询失败：{exc}")
                result = {}

            status = result.get("trade_status") or "未支付"
            code = result.get("code", "")
            sub_msg = result.get("sub_msg", "")

            line = f"[{stamp}] 第 {round_no} 次检测  →  状态：{status}"
            if status in PAID_STATES:
                print(ui.green("  " + line))
                self.order_store.mark_paid(out_trade_no, result.get("trade_no", ""), result)
                logger.info("order", "检测到支付成功", 订单号=out_trade_no,
                            支付宝交易号=result.get("trade_no", ""))
                print()
                ui.ok("支付成功！")
                ui.kv_table([
                    ("商户订单号", out_trade_no),
                    ("支付宝交易号", result.get("trade_no", "-")),
                    ("实付金额", result.get("total_amount", "-")),
                    ("付款账号", result.get("buyer_logon_id", "-")),
                    ("付款时间", result.get("send_pay_date", "-")),
                ])
                break

            if code == "40004" and "交易不存在" in (sub_msg or ""):
                print(f"  {line}  {ui.gray('(订单尚未创建/未扫码)')}")
            else:
                print(f"  {line}")

            # 等待下一次
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                print()
                ui.warn("已中断检测（订单记录已保留）")
                break

        ui.pause()

    # ------------------------------------------------------------ 2 查询
    def _query(self) -> None:
        ui.clear()
        ui.title("3.2 · 查询订单状态")
        cfg = self._load_cfg()
        if not cfg:
            return

        orders = self.order_store.list(10)
        if not orders:
            ui.warn("暂无订单记录，请先发起一笔测试支付")
            ui.pause()
            return

        print()
        for i, o in enumerate(orders, start=1):
            print(f"  {i:>2}. {o['out_trade_no']}  {o['amount']} 元  {o['status']}  {o['created_at']}")
        print("   0. 手动输入订单号")
        raw = input("\n  请选择要查询的订单: ").strip()

        if raw == "0":
            out_trade_no = ui.ask("商户订单号")
        elif raw.isdigit() and 1 <= int(raw) <= len(orders):
            out_trade_no = orders[int(raw) - 1]["out_trade_no"]
        else:
            return

        try:
            result = AlipayClient(cfg).query(out_trade_no=out_trade_no)
        except AlipayError as exc:
            ui.error(str(exc))
            ui.pause()
            return

        print()
        ui.kv_table([
            ("商户订单号", out_trade_no),
            ("是否成功", str(result.get("is_success"))),
            ("返回码", result.get("code", "-")),
            ("返回信息", result.get("msg", "-")),
            ("子错误码", result.get("sub_code", "-") or "-"),
            ("子错误信息", result.get("sub_msg", "-") or "-"),
            ("支付宝交易号", result.get("trade_no", "-") or "-"),
            ("交易状态", result.get("trade_status", "-") or "未支付"),
            ("订单金额", result.get("total_amount", "-") or "-"),
            ("付款账号", result.get("buyer_logon_id", "-") or "-"),
        ])
        print()
        ui.info("状态解读：" + interpret_query(result))

        if result.get("trade_status") in PAID_STATES:
            self.order_store.mark_paid(out_trade_no, result.get("trade_no", ""), result)
            print()
            ui.ok("已更新本地订单为「已支付」")
        ui.pause()

    # ------------------------------------------------------------ 3 显示链接
    def _show_link(self) -> None:
        ui.clear()
        ui.title("3.3 · 重新显示支付链接")
        orders = self.order_store.list(10)
        if not orders:
            ui.warn("暂无订单记录")
            ui.pause()
            return

        print()
        for i, o in enumerate(orders, start=1):
            print(f"  {i:>2}. {o['out_trade_no']}  {o['amount']} 元  {o['status']}")
        raw = input("\n  请选择: ").strip()
        if not (raw.isdigit() and 1 <= int(raw) <= len(orders)):
            return

        order = orders[int(raw) - 1]
        if not order.get("pay_url"):
            ui.warn("该订单没有保存支付链接")
        else:
            print()
            print(order["pay_url"])
        ui.pause()

    # ------------------------------------------------------------ 4 关闭订单
    def _close(self) -> None:
        ui.clear()
        ui.title("3.4 · 关闭订单")
        cfg = self._load_cfg()
        if not cfg:
            return

        orders = self.order_store.list(10)
        if not orders:
            ui.warn("暂无订单记录")
            ui.pause()
            return

        print()
        for i, o in enumerate(orders, start=1):
            print(f"  {i:>2}. {o['out_trade_no']}  {o['amount']} 元  {o['status']}")
        raw = input("\n  请选择要关闭的订单: ").strip()
        if not (raw.isdigit() and 1 <= int(raw) <= len(orders)):
            return

        out_trade_no = orders[int(raw) - 1]["out_trade_no"]
        if not ui.confirm(f"确认关闭订单 {out_trade_no}？", default=False):
            return

        try:
            result = AlipayClient(cfg).close(out_trade_no=out_trade_no)
        except AlipayError as exc:
            ui.error(str(exc))
            ui.pause()
            return

        print()
        if result.get("is_success"):
            self.order_store.update(out_trade_no, status="CLOSED", raw=result)
            ui.ok("订单已在支付宝侧关闭，本地状态已置 CLOSED")
            ui.pause()
            return

        sub_code = result.get("sub_code") or ""
        if sub_code == "ACQ.TRADE_NOT_EXIST":
            ui.warn("该订单在支付宝侧「尚未生成」")
            ui.hint("原因：用户从未进入收银台页面，支付宝侧没有这笔交易。")
            ui.hint("这类订单无需调用支付宝接口关闭，可直接在本地关单。")
            print()
            if ui.confirm("是否在本地关闭该订单（标记为 CLOSED）？", default=True):
                self.order_store.update(out_trade_no, status="CLOSED", raw=result)
                logger.info("order", "本地关单", 订单号=out_trade_no, 原因="支付宝侧交易不存在")
                ui.ok("已在本地关闭，状态置为 CLOSED")
            else:
                ui.info("已取消")
        else:
            ui.warn(interpret_close(result))
        ui.pause()
