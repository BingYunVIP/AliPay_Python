#!/usr/bin/env python3
"""ByPay · 支付宝（AliPay）测试工具 —— 主入口。

用法：
    python3 main.py

菜单：
    1. 配置      填写支付宝参数（加密保存，无需每次重复配置）
    2. 设置      订单有效期 / 回调检测间隔 / 测试金额
    3. 测试支付  发起一笔 0.01 元网页支付并自动检测结果
    4. 订单记录  查看历史测试订单
    5. 回调服务  本地通知接收 + 公网地址映射（notify/return）
    6. 环境自检  检查 SDK / 配置 / 网络
    0. 退出
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_project_venv() -> None:
    """确保使用项目内的虚拟环境。

    背景：本项目依赖（alipay-sdk-python 等）安装在项目内的 .venv 中。
    若直接用系统 python3 运行，会出现「找不到依赖」的问题
    （而 cryptography 恰好系统里也有，所以程序能启动，更容易迷惑人）。

    这里在导入业务模块之前，自动切换到 .venv 的解释器重新运行。
    """
    root = Path(__file__).resolve().parent
    venv_dir = root / ".venv"
    venv_python = venv_dir / "bin" / "python"

    if not venv_python.exists():
        print()
        print("  [!] 未找到项目虚拟环境（.venv）")
        print("      请先执行以下任一命令：")
        print("        ./run.sh                                # 一键启动（推荐）")
        print("        python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
        print()
        return

    # 已经在项目 venv 中运行？
    try:
        if Path(sys.prefix).resolve() == venv_dir.resolve():
            return
    except OSError:
        pass

    # 防止反复切换
    if os.environ.get("BYPY_VENV_REEXEC") == "1":
        return
    os.environ["BYPY_VENV_REEXEC"] = "1"

    print()
    print(f"  [i] 检测到当前不是项目虚拟环境，已自动切换：")
    print(f"      {venv_python}")
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execv(str(venv_python), [str(venv_python), str(root / "main.py"), *sys.argv[1:]])
    except OSError as exc:
        print(f"  [×] 自动切换失败：{exc}")
        print("      请手动执行：./run.sh")


_ensure_project_venv()

from bypay import constants, logger, ui
from bypay.config_store import ConfigStore
from bypay.menus.config_menu import ConfigMenu
from bypay.menus.notify_menu import NotifyMenu
from bypay.menus.log_menu import LogMenu
from bypay.menus.orders_menu import OrdersMenu
from bypay.menus.pay_menu import PayMenu
from bypay.menus.selfcheck_menu import SelfCheckMenu
from bypay.menus.settings_menu import SettingsMenu
from bypay.order_store import OrderStore
from bypay.settings_store import SettingsStore


def _banner() -> None:
    cfg_store = ConfigStore()
    ready = False
    try:
        cfg = cfg_store.load()
        ready = cfg_store.is_ready(cfg)
    except Exception:  # noqa: BLE001
        ready = False

    ui.clear()
    ui.title(f"{constants.APP_NAME}  v{constants.APP_VERSION}")
    if ready:
        ui.ok("配置状态：已就绪")
    else:
        ui.warn("配置状态：未就绪（请先进入「1 配置」）")


def main() -> int:
    cfg_store = ConfigStore()
    set_store = SettingsStore()
    order_store = OrderStore()

    _settings = set_store.load()
    logger.configure(level=_settings.get("log_level", "INFO"))
    logger.info("app", "程序启动", 版本=constants.APP_VERSION)

    while True:
        _banner()
        print()
        print("  1. 配置        支付宝参数（加密保存）")
        print("  2. 设置        订单有效期 / 检测间隔 / 测试金额")
        print("  3. 测试支付    发起 0.01 元网页支付")
        print("  4. 订单记录    查看历史测试订单")
        print("  5. 回调服务    本地通知接收 + 公网地址映射")
        print("  6. 环境自检    检查 SDK / 配置 / 网络")
        print("  7. 日志        按分类查看运行日志")
        print("  0. 退出")
        print()

        try:
            choice = input("  请选择: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if choice == "1":
            ConfigMenu(cfg_store).run()
        elif choice == "2":
            SettingsMenu(set_store).run()
        elif choice == "3":
            PayMenu(cfg_store, set_store, order_store).run()
        elif choice == "4":
            OrdersMenu(order_store).run()
        elif choice == "5":
            NotifyMenu(cfg_store, set_store, order_store).run()
        elif choice == "6":
            SelfCheckMenu(cfg_store).run()
        elif choice == "7":
            LogMenu().run()
        elif choice == "0":
            break
        else:
            ui.error("无效选项")

    logger.info("app", "程序退出")
    print()
    print("  已退出，再见。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  已中断退出。")
        sys.exit(130)
