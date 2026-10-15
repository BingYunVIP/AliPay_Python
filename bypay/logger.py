"""分类日志记录（敏感信息自动脱敏，绝不落盘）。

设计目标：
    1. **分类**：按业务维度分文件记录，便于排查
         app     程序运行（启动/退出/菜单）
         config  配置管理（只记「改了什么」，不记「值是什么」）
         order   订单（创建/查询/状态变更/关闭）
         notify  回调（收到通知/验签/处理结果）
         alipay  支付宝接口调用（方法/结果码，不含密钥）
         error   错误（异常堆栈）
    2. **脱敏**：私钥、公钥、sign、密码等敏感字段一律打码后写入
    3. 同时输出到控制台（便于实时观察）

日志文件位置：data/logs/<分类>.log 与 data/logs/all.log
"""

from __future__ import annotations

import json
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path

from . import constants

# ---------------------------------------------------------------- 分类
CATEGORIES = {
    "app": "程序运行",
    "config": "配置管理",
    "order": "订单",
    "notify": "回调",
    "alipay": "支付宝接口",
    "error": "错误",
}

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}

# 命中即视为敏感字段（大小写不敏感，允许前后缀）
_SENSITIVE_KEY = re.compile(
    r"private_key|public_key|password|passwd|pwd|secret|token|api_key|apikey"
    r"|^sign$|^signature$|_sign$|_signature$|buyer|seller_email|logon_id|identity|phone|email",
    re.IGNORECASE,
)

# 形如 PEM 或超长 Base64 的裸值也视为敏感
_B64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")

# ---------------------------------------------------------------- 状态
_lock = threading.Lock()
_log_dir: Path = constants.LOG_DIR
_level = "INFO"
_console = True


def configure(log_dir: Path = None, level: str = "INFO", console: bool = True) -> None:
    """配置日志输出。"""
    global _log_dir, _level, _console
    if log_dir is not None:
        _log_dir = Path(log_dir)
    _level = (level or "INFO").upper()
    _console = console


# ---------------------------------------------------------------- 脱敏
def mask_value(value, keep: int = 4) -> str:
    """对单个值做打码。"""
    s = "" if value is None else str(value)
    if not s:
        return ""
    if _looks_like_key(s):
        return f"<已隐藏:{len(s)}字符>"
    if len(s) <= keep * 2:
        return "*" * len(s)
    return f"{s[:keep]}***{s[-keep:]}"


def _looks_like_key(s: str) -> bool:
    """判断一个长字符串是否像密钥。"""
    if "-----BEGIN" in s:
        return True
    stripped = s.strip()
    return len(stripped) >= 64 and bool(_B64_RE.match(stripped))


def sanitize(obj, _depth: int = 0):
    """递归脱敏：dict / list / 长 Base64 字符串。"""
    if _depth > 8:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY.search(k):
                out[k] = mask_value(v)
            else:
                out[k] = sanitize(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize(v, _depth + 1) for v in obj]
    if isinstance(obj, str) and _looks_like_key(obj):
        return mask_value(obj, keep=6)
    return obj


def safe_params(params: dict) -> dict:
    """对「回调/请求参数」做安全化处理，供日志输出。"""
    return sanitize(dict(params or {}))


# ---------------------------------------------------------------- 写入
def _fmt_fields(fields: dict) -> str:
    if not fields:
        return ""
    safe = sanitize(fields)
    parts = []
    for k, v in safe.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}={v}")
    return " | " + " ".join(parts)


def _write_line(category: str, level: str, message: str, fields: dict) -> None:
    if LEVELS.get(level, 20) < LEVELS.get(_level, 20):
        return

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"{stamp} [{level:<5}] [{category:<7}] {message}{_fmt_fields(fields)}"

    with _lock:
        try:
            _log_dir.mkdir(parents=True, exist_ok=True)
            for name in (f"{category}.log", "all.log"):
                with open(_log_dir / name, "a", encoding="utf-8") as fp:
                    fp.write(line + "\n")
        except OSError:
            pass

    if _console:
        try:
            from . import ui

            color = {
                "DEBUG": ui.gray,
                "INFO": ui.gray,
                "WARN": ui.yellow,
                "ERROR": ui.red,
            }.get(level, ui.gray)
            print(color(f"      · [{category}] {message}{_fmt_fields(fields)}"))
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 对外 API
def log(category: str, message: str, level: str = "INFO", **fields) -> None:
    if category not in CATEGORIES:
        category = "app"
    _write_line(category, level.upper(), message, fields)


def debug(category: str, message: str, **fields) -> None:
    log(category, message, "DEBUG", **fields)


def info(category: str, message: str, **fields) -> None:
    log(category, message, "INFO", **fields)


def warn(category: str, message: str, **fields) -> None:
    log(category, message, "WARN", **fields)


def error(category: str, message: str, exc: BaseException = None, **fields) -> None:
    if exc is not None:
        fields["异常"] = f"{type(exc).__name__}: {exc}"
        fields["堆栈"] = traceback.format_exc(limit=6)
    _write_line(category, "ERROR", message, fields)


# ---------------------------------------------------------------- 查询
def list_logs() -> list:
    """返回 [(分类, 文件名, 字节数, 最后修改时间), ...]"""
    rows = []
    if not _log_dir.exists():
        return rows
    for path in sorted(_log_dir.glob("*.log")):
        stat = path.stat()
        rows.append((
            CATEGORIES.get(path.stem, path.stem),
            path.name,
            stat.st_size,
            datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        ))
    return rows


def tail(category: str = "all", limit: int = 30) -> list:
    """读取某个分类日志的最后 N 行。"""
    path = _log_dir / f"{category}.log"
    if not path.exists():
        return []
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]


def clear() -> None:
    if not _log_dir.exists():
        return
    for path in _log_dir.glob("*.log"):
        try:
            path.unlink()
        except OSError:
            pass
