"""「5 回调服务」菜单：本地通知接收 + 公网地址映射 + 真实回调自测。

为什么需要这一套：
    支付宝的**异步通知必须公网可达**。本菜单提供完整的「本地 → 公网」闭环：

        1. 启动本地回调服务（接收 POST 通知 / GET 跳转）
        2. 用 cloudflared 快速隧道映射出公网 https 地址
        3. 把该地址写入配置的 notify_url / return_url
        4. 发起真实支付 → 支付宝回调到你本机 → 自动置单

另外提供「本地自测」：在不花钱的情况下，模拟支付宝发通知，
验证验签、幂等、状态更新这条链路是否正确。
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import constants, logger, tunnel, ui
from ..config_store import ConfigError, ConfigStore
from ..notify_server import NotifyServer
from ..order_store import OrderStore
from ..settings_store import SettingsStore


class NotifyMenu:
    def __init__(self, cfg_store: ConfigStore, set_store: SettingsStore, order_store: OrderStore):
        self.cfg_store = cfg_store
        self.set_store = set_store
        self.order_store = order_store
        self.server: NotifyServer | None = None
        self.tunnel = tunnel.QuickTunnel()

    # ------------------------------------------------------------ 主循环
    def run(self) -> None:
        while True:
            settings = self.set_store.load()
            port = int(settings["notify_port"])
            running = self.server is not None and self.server.is_running
            tunnel_running = self.tunnel.is_running

            ui.clear()
            ui.title("5 · 回调服务（notify / return）")
            print()
            ui.info(f"本地服务 ：{ui.green('运行中') if running else ui.gray('已停止')}    端口 {port}")
            ui.info(
                "公网隧道 ："
                + (ui.green(self.tunnel.url) if tunnel_running else ui.gray("未启动"))
            )

            # 显示当前配置中的回调地址
            try:
                cfg = self.cfg_store.load()
                cfg_notify = cfg.get("notify_url") or ui.gray("(未配置)")
                cfg_return = cfg.get("return_url") or ui.gray("(未配置)")
            except ConfigError:
                cfg_notify = cfg_return = ui.gray("(配置无法读取)")
            ui.info(f"配置 notify_url：{cfg_notify}")
            ui.info(f"配置 return_url：{cfg_return}")

            print()
            print("  1. 启动本地回调服务")
            print("  2. 停止本地回调服务")
            print("  3. 查看收到的通知")
            print("  4. 清空通知记录")
            print("  5. 启动公网隧道（内网穿透）")
            print("  6. 停止公网隧道")
            print("  7. 把公网地址写入配置（notify_url / return_url）")
            print("  8. 本地自测回调（不花钱，验证整条链路）")
            print("  9. 公网连通性检测")
            print("  0. 返回上级")
            print()
            ui.hint("完整流程：1 → 5 → 7 → 去「3 测试支付」下单并付款 → 回调自动置单")

            choice = input("\n  请选择: ").strip()

            if choice == "1":
                self._start(port)
            elif choice == "2":
                self._stop()
            elif choice == "3":
                self._show_records()
            elif choice == "4":
                NotifyServer.clear()
                ui.ok("已清空")
                ui.pause()
            elif choice == "5":
                self._start_tunnel(port)
            elif choice == "6":
                self._stop_tunnel()
            elif choice == "7":
                self._write_urls()
            elif choice == "8":
                self._selftest(port)
            elif choice == "9":
                self._probe()
            elif choice == "0":
                return
            else:
                ui.error("无效选项")

    # ------------------------------------------------------------ 1 启动
    def _start(self, port: int) -> None:
        try:
            cfg = self.cfg_store.load()
        except ConfigError as exc:
            ui.error(str(exc))
            ui.pause()
            return

        if not self._port_free(port):
            ui.error(f"端口 {port} 已被占用，请先释放（或在「2 设置」换端口）")
            ui.pause()
            return

        try:
            self.server = NotifyServer(
                alipay_public_key=cfg.get("alipay_public_key", ""),
                order_store=self.order_store,
                port=port,
                app_id=cfg.get("app_id", ""),
                seller_id=cfg.get("seller_id", ""),
            )
            self.server.start()
        except (OSError, ValueError) as exc:
            ui.error(f"启动失败（端口 {port} 可能被占用）：{exc}")
            self.server = None
            ui.pause()
            return

        ui.ok(f"回调服务已启动，监听 127.0.0.1:{port}")
        urls = self.server.urls()
        ui.hint(f"本机异步通知：{urls['notify']}")
        ui.hint(f"本机同步跳转：{urls['return']}")
        if not cfg.get("alipay_public_key"):
            ui.warn("配置中没有「支付宝公钥」，收到通知时将无法验签")
        ui.pause()

    def _stop(self) -> None:
        if self.server:
            self.server.stop()
            self.server = None
            ui.ok("已停止")
        else:
            ui.warn("服务未在运行")
        ui.pause()

    # ------------------------------------------------------------ 3 记录
    def _show_records(self) -> None:
        ui.clear()
        ui.title("5.1 · 收到的通知")
        records = NotifyServer.recent(20)
        if not records:
            ui.warn("尚未收到任何通知")
            ui.hint("若已支付却收不到，请检查：")
            ui.hint("  1) 公网隧道是否启动（5 启动公网隧道）")
            ui.hint("  2) 配置里的 notify_url 是否为当前隧道地址（7 写入配置）")
            ui.hint("  3) 本地回调服务是否运行（1 启动本地回调服务）")
            ui.pause()
            return

        for rec in records:
            print()
            kind = rec.get("kind", "")
            label = {"notify": "异步通知", "return": "同步跳转"}.get(kind, kind)
            print(ui.cyan(f"  ── {rec.get('time','')}  [{label}]  {rec.get('path','')}"))
            print(f"     来源 IP  = {rec.get('remote','-')}")
            for k, v in rec.get("params", {}).items():
                shown = v if len(str(v)) < 60 else str(v)[:57] + "…"
                print(f"     {k} = {shown}")
            if rec.get("verify_note"):
                flag = ui.green(rec["verify_note"]) if rec.get("verified") else ui.red(rec["verify_note"])
                print(f"     验签     = {flag}")
            if rec.get("handled"):
                print(f"     处理     = {rec['handled']}")
        ui.pause()

    # ------------------------------------------------------------ 5 隧道
    def _start_tunnel(self, port: int) -> None:
        ui.clear()
        ui.title("5.2 · 启动公网隧道")
        print()
        ui.info("将用 Cloudflare 快速隧道把本地服务映射为公网 https 地址。")
        ui.hint("用途：让支付宝服务器能 POST 到你的本机（notify_url 必须公网可达）")
        ui.hint("特点：免费、免注册、免域名；地址每次启动都会变化")
        print()

        if not tunnel.find_binary():
            ui.warn("未检测到 cloudflared（公网穿透工具）")
            if not ui.confirm("是否现在自动下载安装？", default=True):
                ui.pause()
                return
            ui.info("正在下载安装（约 40MB，首次较慢）…")
            ok, msg = tunnel.install()
            if ok:
                ui.ok(msg)
            else:
                ui.error(msg)
                ui.pause()
                return

        ui.info(f"cloudflared 版本：{tunnel.version()}")
        if not self.server or not self.server.is_running:
            ui.warn("提示：本地回调服务尚未启动（可先启动，否则隧道指向空端口）")
            if ui.confirm("是否先启动本地回调服务？", default=True):
                self._start(port)
                if not self.server or not self.server.is_running:
                    return

        print()
        ui.info(f"正在建立隧道（目标 http://127.0.0.1:{port}），请稍候…")
        ok, result = self.tunnel.start(port)
        if ok:
            print()
            ui.ok("公网地址已就绪：")
            print()
            print(ui.green(f"  {result}"))
            print()
            ui.hint(f"异步通知地址：{result}/alipay/notify")
            ui.hint(f"同步跳转地址：{result}/alipay/return")
            print()
            ui.info("下一步：选择「7 把公网地址写入配置」")
        else:
            ui.error(result)
        ui.pause()

    def _stop_tunnel(self) -> None:
        if self.tunnel.stop():
            ui.ok("公网隧道已停止")
        else:
            ui.warn("隧道未在运行")
        ui.pause()

    # ------------------------------------------------------------ 7 写配置
    def _write_urls(self) -> None:
        ui.clear()
        ui.title("5.3 · 写入回调地址到配置")
        print()

        url = self.tunnel.url
        if not url:
            ui.warn("公网隧道未启动，无法获取公网地址")
            ui.hint("请先执行「5 启动公网隧道」")
            ui.pause()
            return

        notify_url = f"{url}/alipay/notify"
        return_url = f"{url}/alipay/return"

        print()
        ui.info("将写入以下配置：")
        print(f"    notify_url = {notify_url}")
        print(f"    return_url = {return_url}")
        print()
        ui.hint("说明：支付宝的异步通知通过 notify_url 回调到本机；")
        ui.hint("      return_url 是用户付款后浏览器跳回的页面。")
        print()

        if not ui.confirm("确认写入？", default=True):
            return

        try:
            cfg = self.cfg_store.load()
        except ConfigError as exc:
            ui.error(str(exc))
            ui.pause()
            return

        cfg["notify_url"] = notify_url
        cfg["return_url"] = return_url
        self.cfg_store.save(cfg)
        logger.info("config", "回调地址已写入配置", notify_url=notify_url, return_url=return_url)

        ui.ok("已写入并加密保存")
        ui.hint("提示：隧道地址会变化。重启隧道后请重新执行本步骤。")
        ui.pause()

    # ------------------------------------------------------------ 8 本地自测
    def _selftest(self, port: int) -> None:
        import subprocess
        import sys
        ui.info("将在隔离的临时数据库中测试签名与回调，不修改真实订单，也不请求支付宝。")
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=constants.PROJECT_ROOT, check=False)
        ui.pause()

    def _make_test_order(self) -> dict:
        amount = str(self.set_store.load()["test_amount"])
        ono = f"BYPTEST{time.strftime('%m%d%H%M%S')}"
        return self.order_store.add(ono, "ByPay 回调自测订单", amount, "")

    def _post_local(self, port: int, path: str, params: dict) -> tuple:
        """向本机回调服务 POST 表单（模拟支付宝）。"""
        data = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        url = f"http://127.0.0.1:{port}{path}"
        req = urllib.request.Request(
            url, data=data.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=10) as resp:
                return True, resp.read(200).decode("utf-8", "replace").strip()
        except urllib.error.HTTPError as exc:
            return True, f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    # ------------------------------------------------------------ 9 连通性
    def _probe(self) -> None:
        ui.clear()
        ui.title("5.5 · 公网连通性检测")
        print()

        url = self.tunnel.url
        if not url:
            ui.warn("公网隧道未启动")
            ui.hint("请先执行「5 启动公网隧道」")
            ui.pause()
            return

        if not self.server or not self.server.is_running:
            ui.warn("本地回调服务未运行，公网地址将无法返回内容")
            ui.pause()
            return

        health = f"{url}/health"
        ui.info(f"从公网视角访问：{health}")
        print()
        ok, msg = tunnel.probe_public(health)
        if ok:
            ui.ok(f"公网可达：{msg}")
            print()
            ui.hint("说明：支付宝服务器可以从公网访问到你的本机了。")
            ui.hint("下一步：选择「7 把公网地址写入配置」，再发起真实支付。")
        else:
            ui.error(f"公网不可达：{msg}")
            ui.hint("可能原因：隧道刚建立尚需几秒生效、网络波动、隧道已失效。")
            ui.hint("可稍等几秒重试，或「6 停止公网隧道」后重新「5 启动公网隧道」。")
        ui.pause()

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False