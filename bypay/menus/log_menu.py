"""「7 日志」菜单：按分类查看日志（敏感信息已自动打码）。"""

from __future__ import annotations

from .. import constants, logger, ui


class LogMenu:
    def run(self) -> None:
        while True:
            ui.clear()
            ui.title("7 · 日志")
            print()
            ui.info("日志按业务分类记录，私钥/公钥/签名等敏感信息已自动打码。")
            print()
            print("  1. 查看日志（按分类）")
            print("  2. 查看最近日志（全部）")
            print("  3. 清空日志")
            print("  4. 显示日志目录")
            print("  0. 返回上级")
            print()
            choice = input("  请选择: ").strip()

            if choice == "1":
                self._by_category()
            elif choice == "2":
                self._recent()
            elif choice == "3":
                if ui.confirm("确认清空所有日志？", default=False):
                    logger.clear()
                    ui.ok("已清空")
                    ui.pause()
            elif choice == "4":
                ui.info(f"日志目录：{constants.LOG_DIR}")
                ui.pause()
            elif choice == "0":
                return
            else:
                ui.error("无效选项")

    # ------------------------------------------------------------ 按分类
    def _by_category(self) -> None:
        ui.clear()
        ui.title("7.1 · 日志文件列表")
        rows = logger.list_logs()
        if not rows:
            ui.warn("暂无日志文件")
            ui.pause()
            return

        print()
        print(f"  {'#':<3} {'分类':<12} {'文件':<14} {'大小':>8}  {'最后写入'}")
        print("  " + "─" * 62)
        for i, (label, name, size, mtime) in enumerate(rows, start=1):
            size_str = f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"
            print(f"  {i:<3} {label:<12} {name:<14} {size_str:>8}  {mtime}")

        print()
        print("  输入序号查看该分类日志 | 0 返回")
        raw = input("  请选择: ").strip()
        if not (raw.isdigit() and 1 <= int(raw) <= len(rows)):
            return

        name = rows[int(raw) - 1][1]
        category = name[:-4]  # 去掉 .log
        self._show(category)

    def _show(self, category: str, limit: int = 60) -> None:
        ui.clear()
        ui.title(f"7.2 · 日志内容 [{logger.CATEGORIES.get(category, category)}]")
        lines = logger.tail(category, limit)
        print()
        if not lines:
            ui.warn("该分类暂无日志")
        else:
            for line in lines:
                print("  " + line)
        print()
        ui.hint(f"文件：{constants.LOG_DIR / (category + '.log')}")
        ui.pause()

    # ------------------------------------------------------------ 最近
    def _recent(self, limit: int = 40) -> None:
        ui.clear()
        ui.title("7.3 · 最近日志（全部）")
        lines = logger.tail("all", limit)
        print()
        if not lines:
            ui.warn("暂无日志")
        else:
            for line in lines:
                print("  " + line)
        ui.pause()
