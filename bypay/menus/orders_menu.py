"""「4 订单记录」菜单：查看历史测试订单。"""

from __future__ import annotations

from .. import ui
from ..order_store import OrderStore


class OrdersMenu:
    def __init__(self, store: OrderStore):
        self.store = store

    def run(self) -> None:
        while True:
            ui.clear()
            ui.title("4 · 订单记录")
            rows = self.store.list(50)

            if not rows:
                ui.warn("暂无订单记录")
                ui.pause()
                return

            print()
            print(f"  {'#':<3} {'商户订单号':<22} {'金额':<8} {'状态':<10} {'创建时间'}")
            print("  " + "─" * 66)
            for i, o in enumerate(rows, start=1):
                status = o.get("status", "-")
                color = ui.green if status == "PAID" else ui.gray
                print(
                    f"  {i:<3} {o['out_trade_no']:<22} {o['amount']:<8} "
                    f"{color(status):<10} {o.get('created_at', '')}"
                )

            print()
            print("  输入序号查看详情 | c 清空记录 | 0 返回")
            raw = input("  请选择: ").strip()

            if raw == "0":
                return
            if raw.lower() == "c":
                if ui.confirm("确认清空所有订单记录？", default=False):
                    self.store.clear()
                    ui.ok("已清空")
                    ui.pause()
                continue
            if raw.isdigit() and 1 <= int(raw) <= len(rows):
                self._detail(rows[int(raw) - 1])
            else:
                ui.error("无效输入")

    def _detail(self, order: dict) -> None:
        ui.clear()
        ui.title("4.1 · 订单详情")
        print()
        ui.kv_table([
            ("商户订单号", order.get("out_trade_no", "-")),
            ("支付宝交易号", order.get("trade_no", "-") or "-"),
            ("商品标题", order.get("subject", "-")),
            ("金额", f"{order.get('amount', '-')} 元"),
            ("状态", order.get("status", "-")),
            ("创建时间", order.get("created_at", "-")),
            ("支付时间", order.get("paid_at", "-") or "-"),
        ])
        if order.get("pay_url"):
            print()
            ui.info("支付链接：")
            print("  " + order["pay_url"])
        if order.get("raw"):
            print()
            ui.info("最近一次查询/回调原始数据：")
            for k, v in order["raw"].items():
                print(f"    {k} = {v}")
        ui.pause()
