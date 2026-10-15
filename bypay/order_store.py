"""测试订单记录（JSON 落盘）—— 并发安全版。

并发设计（重要）：
    本模块会被两个执行流同时调用：
      1. 主线程（菜单里的轮询检测、手动查询）
      2. 回调服务线程（NotifyServer 收到支付宝通知后置单）
    因此所有「读-改-写」都必须加锁，否则会丢数据或写坏文件。

    采取两层保护：
      - 进程内：threading.RLock（覆盖多线程）
      - 跨进程：fcntl.flock 文件锁（覆盖多进程 / 多终端同时运行）
    并且写入采用「临时文件 + os.replace」原子替换，避免读到半截文件。

字段说明：
    out_trade_no  商户订单号（我们生成）
    trade_no      支付宝交易号（支付宝返回）
    subject       商品标题
    amount        金额（元，字符串，避免浮点误差）
    status        状态：CREATED / PAID / CLOSED / TIMEOUT / FAILED
    pay_url       支付链接
    created_at    创建时间
    paid_at       支付成功时间
    raw           最近一次查询/回调的原始数据（便于排查）
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import constants, logger

# 进程内线程锁（所有实例共享同一把）
_THREAD_LOCK = threading.RLock()


@contextmanager
def _file_lock(target: Path):
    """跨进程文件锁（POSIX flock；不可用时静默降级）。"""
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(lock_path, "a+", encoding="utf-8")
    try:
        try:
            import fcntl

            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        fp.close()


class OrderStore:
    def __init__(self, path: Path = None):
        self.path = Path(path or constants.ORDERS_FILE)

    # ---------------------------------------------------------- 基础
    def _read_unlocked(self) -> list:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise RuntimeError("订单文件结构损坏，已停止写入")
            return data
        except (json.JSONDecodeError, OSError) as exc:
            # Fail closed; never turn a damaged order ledger into an empty one.
            raise RuntimeError("订单文件无法读取，已停止写入，请先恢复备份") from exc

    def _write_unlocked(self, rows: list) -> None:
        """原子写入：先写临时文件，再 os.replace 替换。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + f".tmp{os.getpid()}")
        tmp.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.chmod(0o600)
        os.replace(tmp, self.path)

    @contextmanager
    def _rw(self):
        """读-改-写事务：线程锁 + 文件锁双重保护。"""
        with _THREAD_LOCK:
            with _file_lock(self.path):
                yield

    # ---------------------------------------------------------- 业务
    def add(self, out_trade_no: str, subject: str, amount: str, pay_url: str = "") -> dict:
        order = {
            "out_trade_no": out_trade_no,
            "trade_no": "",
            "subject": subject,
            "amount": amount,
            "status": "CREATED",
            "pay_url": pay_url,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "paid_at": "",
            "raw": {},
        }
        with self._rw():
            rows = self._read_unlocked()
            if any(row.get("out_trade_no") == out_trade_no for row in rows):
                raise ValueError("订单号已存在")
            rows.append(order)
            self._write_unlocked(rows)
        logger.info("order", "订单已创建", 订单号=out_trade_no, 金额=amount, 标题=subject)
        return order

    def get(self, out_trade_no: str) -> dict | None:
        with self._rw():
            rows = self._read_unlocked()
        for row in rows:
            if row.get("out_trade_no") == out_trade_no:
                return row
        return None

    def update(self, out_trade_no: str, **fields) -> dict | None:
        with self._rw():
            rows = self._read_unlocked()
            target = None
            for row in rows:
                if row.get("out_trade_no") == out_trade_no:
                    if row.get("status") == "PAID" and fields.get("status", "PAID") != "PAID":
                        fields = {k: v for k, v in fields.items() if k != "status"}
                    row.update(fields)
                    target = dict(row)
                    break
            if target is not None:
                self._write_unlocked(rows)

        if target is not None:
            logger.info(
                "order", "订单已更新",
                订单号=out_trade_no,
                变更字段=",".join(sorted(fields.keys())),
                状态=target.get("status"),
            )
        return target

    def mark_paid(self, out_trade_no: str, trade_no: str = "", raw: dict = None) -> dict | None:
        """置为已支付。**幂等**：已 PAID 的订单不会被重复处理。"""
        with self._rw():
            rows = self._read_unlocked()
            target = None
            for row in rows:
                if row.get("out_trade_no") == out_trade_no:
                    from .payment_validation import money
                    if not raw or money(raw.get("total_amount", "")) != money(row["amount"]):
                        raise ValueError("支付金额与订单不符")
                    if raw.get("out_trade_no") != out_trade_no or not trade_no:
                        raise ValueError("支付订单号或交易号不符")
                    if row.get("trade_no") and row["trade_no"] != trade_no:
                        raise ValueError("支付交易号不符")
                    if row.get("status") == "PAID":
                        # 幂等：重复通知不再改写
                        target = dict(row)
                        break
                    row.update(
                        status="PAID",
                        trade_no=trade_no,
                        paid_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                        raw=raw or {},
                    )
                    target = dict(row)
                    break
            if target is not None:
                self._write_unlocked(rows)

        if target is not None and target.get("status") == "PAID":
            logger.info("order", "订单已置为已支付", 订单号=out_trade_no, 支付宝交易号=trade_no)
        return target

    def list(self, limit: int = 20) -> list:
        with self._rw():
            rows = self._read_unlocked()
        return list(reversed(rows))[:limit]

    def count(self) -> int:
        with self._rw():
            return len(self._read_unlocked())

    def clear(self) -> None:
        with self._rw():
            self._write_unlocked([])
        logger.warn("order", "订单记录已清空")
