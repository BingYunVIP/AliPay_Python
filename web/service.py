"""One supervised process for systemd and foreground runs: web + payment worker."""
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from .management import ROOT, configuration, atomic_text


def main():
    os.umask(0o077)
    state, source, values = configuration()
    port = state["port"]
    data = Path(values["DATABASE"]).parent
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (data / "service.lock").open("a")
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit("ByPay 已在运行")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
        check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: check.bind((state["host"], port))
        except OSError: raise SystemExit(f"端口 {port} 已被其他程序占用")
    log = data / "logs/server.log"
    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if log.exists() and log.stat().st_size > 10_000_000: log.replace(log.with_suffix('.log.1'))
    env = {**os.environ, "BYPAY_ENV_FILE":str(source), "BYPAY_RUN_MODE":"managed", "BYPAY_FRONTEND_PORT":str(port), "PYTHONUNBUFFERED":"1"}
    commands = [[str(ROOT / ".venv/bin/gunicorn"), "--bind", f"{state['host']}:{port}", "--workers", "2", "--threads", "4", "--timeout", "30", "--umask", "0077", "--no-control-socket", "web.wsgi:app"],
                [sys.executable, "-m", "web.worker"]]
    children = []
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGHUP, stop)
    with log.open("ab", buffering=0) as output:
        try:
            for command in commands:
                children.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True))
            atomic_text(data / "service.json", json.dumps({"pid":os.getpid(),"children":[p.pid for p in children],"port":port}) + "\n")
            print(f"ByPay 已启动 · 端口 {port}", flush=True)
            while not stopping and all(child.poll() is None for child in children): time.sleep(.3)
            code = 0 if stopping else 1
        finally:
            for child in children:
                if child.poll() is None:
                    try: os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
            deadline = time.monotonic() + 20
            for child in children:
                try: child.wait(timeout=max(.1,deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL); child.wait()
            (data / "service.json").unlink(missing_ok=True)
    raise SystemExit(code)


if __name__ == "__main__": main()
