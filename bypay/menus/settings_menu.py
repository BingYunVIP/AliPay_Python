"""「2 设置」菜单：订单有效期、检测间隔、测试金额等。"""

from __future__ import annotations

from .. import constants, logger, ui
from ..settings_store import SettingsStore


class SettingsMenu:
    def __init__(self, store: SettingsStore):
        self.store = store

    def run(self) -> None:
        while True:
            data = self.store.load()
            ui.clear()
            ui.title("2 · 设置")
            print()
            print(f"  1. 订单有效期           当前 {data['order_timeout']} 秒 "
                  f"（{data['order_timeout'] // 60} 分钟）")
            print(f"  2. 回调 / 状态检测间隔  当前 {data['poll_interval']} 秒")
            print(f"  3. 测试金额             当前 {data['test_amount']} 元")
            print(f"  4. 回调服务端口         当前 {data['notify_port']}")
            print(f"  5. 支付方式             当前 {constants.PAY_METHODS.get(data['pay_method'], data['pay_method'])}")
            print(f"  6. 日志级别             当前 {data.get('log_level', 'INFO')}")
            print("  7. 恢复默认设置")
            print("  0. 返回上级")
            print()
            choice = input("  请选择: ").strip()

            if choice == "1":
                value = ui.ask_int("订单有效期（秒）", data["order_timeout"])
                self.store.set("order_timeout", value)
                ui.ok(f"已设置订单有效期为 {value} 秒")
                ui.pause()
            elif choice == "2":
                value = ui.ask_int("回调 / 状态检测间隔（秒）", data["poll_interval"])
                self.store.set("poll_interval", value)
                ui.ok(f"已设置检测间隔为 {value} 秒")
                ui.pause()
            elif choice == "3":
                value = ui.ask("测试金额（元）", str(data["test_amount"]))
                self.store.set("test_amount", value)
                ui.ok(f"已设置测试金额为 {value} 元")
                ui.pause()
            elif choice == "4":
                value = ui.ask_int("回调服务端口", data["notify_port"])
                self.store.set("notify_port", value)
                ui.ok(f"已设置回调服务端口为 {value}")
                ui.pause()
            elif choice == "5":
                ui.section("可选支付方式")
                keys = list(constants.PAY_METHODS.keys())
                for i, k in enumerate(keys, start=1):
                    print(f"    {i}. {constants.PAY_METHODS[k]}")
                ui.hint("当前版本已实现「电脑网站支付」，其余为后续扩展预留")
                raw = input("  请选择: ").strip()
                if raw.isdigit() and 1 <= int(raw) <= len(keys):
                    self.store.set("pay_method", keys[int(raw) - 1])
                    ui.ok("已保存")
                ui.pause()
            elif choice == "6":
                value = ui.ask_choice(
                    "日志级别", ["DEBUG", "INFO", "WARN", "ERROR"],
                    data.get("log_level", "INFO"),
                )
                self.store.set("log_level", value)
                logger.configure(level=value)
                ui.ok(f"已设置日志级别为 {value}")
                ui.pause()
            elif choice == "7":
                if ui.confirm("恢复默认设置？", default=False):
                    self.store.save(dict(constants.DEFAULT_SETTINGS))
                    ui.ok("已恢复默认")
                    ui.pause()
            elif choice == "0":
                return
            else:
                ui.error("无效选项")
