"""Process-safe rotating JSON logs, without credentials or payer identities."""
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from bypay.logger import safe_params


def bounded(value):
    if isinstance(value, dict):
        return {k: bounded(v) for k, v in list(value.items())[:64]}
    if isinstance(value, list):
        return [bounded(v) for v in value[:64]]
    if isinstance(value, str) and len(value) > 4096:
        return value[:4096] + "… [truncated]"
    return value


def create_trace(store, log_path):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def append(record):
        lock_fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(lock_fd, "w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if path.exists() and path.stat().st_size >= 5_000_000:
                for index in range(4, 0, -1):
                    previous = Path(f"{path}.{index}")
                    if previous.exists():
                        previous.replace(f"{path}.{index + 1}")
                path.replace(f"{path}.1")
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")

    def trace(stage, reason, details=None, number=None, user_id=None, accepted=True):
        safe = bounded(safe_params(details or {}))
        store.record_event(stage, reason, safe, number, user_id, accepted)
        append({"time": datetime.now(timezone.utc).isoformat(), "stage": stage, "reason": reason,
                "out_trade_no": number, "accepted": accepted, "details": safe})
    return trace
