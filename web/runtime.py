"""One launcher; choose development or HTTPS serving from the site configuration."""
import argparse
import ipaddress
import json
import os
import secrets
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


def local_hosts():
    hosts = {"localhost", "127.0.0.1", "::1"}
    hostname = socket.gethostname()
    if hostname and all(c.isalnum() or c in "-." for c in hostname):
        hosts.add(hostname.lower())
    try:
        hosts.update(row[4][0].split("%")[0] for row in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM))
    except OSError:
        pass
    try:
        result = subprocess.run(["ip", "-j", "address", "show"], capture_output=True, text=True, timeout=3, check=True)
        for link in json.loads(result.stdout):
            for address in link.get("addr_info", []):
                value = address.get("local", "")
                ip = ipaddress.ip_address(value)
                if not ip.is_link_local and not ip.is_unspecified:
                    hosts.add(value)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return sorted(hosts)


def browser_origins(hosts, ports):
    return sorted({f"http://{'[' + host + ']' if ':' in host else host}:{port}" for host in hosts for port in ports})


def resolve_launch():
    requested = os.environ.get("BYPAY_RUN_MODE", "auto")
    if requested not in {"auto", "local", "production", "managed"}:
        raise ValueError("未知运行模式")
    source = os.environ.get("BYPAY_ENV_FILE")
    if not source:
        source = ROOT / (".env.local" if requested == "local" else ".env" if requested == "production" or (ROOT / ".env").exists() or os.environ.get("PUBLIC_BASE_URL") else ".env.local")
    source = Path(source).resolve()
    if requested != "auto":
        return requested, source
    if os.environ.get("BYPAY_ENV_FILE") and not source.is_file():
        raise ValueError("指定的配置文件不存在：" + str(source))
    values = dotenv_values(source) if source.exists() else {}
    origin = os.environ.get("PUBLIC_BASE_URL", values.get("PUBLIC_BASE_URL") or "")
    parsed = urlsplit(origin)
    if not origin or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        return "local", source
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return "production", source
    raise ValueError("PUBLIC_BASE_URL 应为本机 HTTP 地址或服务器 HTTPS 域名")


def env_path():
    return resolve_launch()[1]


def load_runtime():
    mode, source = resolve_launch()
    values = {k: v for k, v in dotenv_values(source).items() if v is not None} if source.exists() else {}
    if mode == "managed":
        from .management import direct_origins
        port = int(os.environ.get("BYPAY_FRONTEND_PORT", "888"))
        values.setdefault("PUBLIC_BASE_URL", f"http://localhost:{port}")
        values.setdefault("DATABASE", str(ROOT / "data/web.sqlite3"))
        values.setdefault("BYPAY_LOG_FILE", str(Path(values["DATABASE"]).parent / "logs/payment.jsonl"))
        values["BYPAY_RUN_MODE"] = "managed"
        values["BYPAY_BROWSER_ORIGINS"] = direct_origins(values["PUBLIC_BASE_URL"], port) if values.get("BYPAY_ALLOW_HTTP") == "1" else ""
        return values
    if mode == "local":
        port = int(os.environ.get("BYPAY_FRONTEND_PORT", "888"))
        api_port = int(os.environ.get("BYPAY_BACKEND_PORT", str(port + 1)))
        config = {
            "ALIPAY_ENV": "sandbox", "ALIPAY_APP_ID": "", "ALIPAY_SELLER_ID": "",
            "ALIPAY_PRIVATE_KEY": "", "ALIPAY_PUBLIC_KEY": "",
            "ALIPAY_PRIVATE_KEY_FILE": "", "ALIPAY_PUBLIC_KEY_FILE": "", "ALIPAY_NOTIFY_URL": "",
            "DATABASE": str(ROOT / "data/local/web.sqlite3"),
            "BYPAY_LOG_FILE": str(ROOT / "data/local/logs/payment.jsonl"),
            "BYPAY_ADMIN_EMAIL": "", "BYPAY_ADMIN_PASSWORD": "",
            "ALLOW_REGISTRATION": "true", "TEST_MAX_AMOUNT": "3000.00", **values,
            "PUBLIC_BASE_URL": f"http://localhost:{port}", "BYPAY_RUN_MODE": "local", "BYPAY_TRUST_PROXY": "0",
        }
        hosts = local_hosts()
        allowed = set(browser_origins(hosts, {port, api_port}))
        # Explicit additional frontend origins, for an intentional port forward or Vite instance.
        for value in os.environ.get("BYPAY_EXTRA_ORIGINS", "").split(","):
            if not value: continue
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or any(ord(c) < 33 for c in value):
                raise ValueError("--origin 必须是完整访问地址，不包含路径、参数或用户信息")
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError("--origin 的端口无效")
            allowed.add(value)
        config["BYPAY_BROWSER_ORIGINS"] = ",".join(sorted(allowed))
        return config
    if mode != "production":
        raise ValueError("未知运行模式")
    if not source.exists() and not os.environ.get("PUBLIC_BASE_URL"):
        raise ValueError("站点配置不存在，请运行 ./ByPay.sh 并选择部署")
    # systemd EnvironmentFile / explicit environment overrides retain precedence in production.
    keys = {"PUBLIC_BASE_URL", "DATABASE", "TEST_MAX_AMOUNT", "ALLOW_REGISTRATION"}
    values.update({k: v for k, v in os.environ.items() if k.startswith(("ALIPAY_", "BYPAY_")) or k in keys})
    parsed = urlsplit(values.get("PUBLIC_BASE_URL", ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("站点地址不正确，请运行 ./ByPay.sh 并选择部署")
    values["BYPAY_RUN_MODE"] = "production"
    values["BYPAY_BROWSER_ORIGINS"] = ""
    return values


def _quote(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def prepare():
    os.umask(0o077)
    mode, source = resolve_launch()
    local = mode == "local"
    if local and not source.exists():
        data = Path(os.environ.get("BYPAY_LOCAL_DATA_DIR", str(ROOT / "data/local"))).resolve()
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        values = {
            "DATABASE": str(data / "web.sqlite3"), "BYPAY_LOG_FILE": str(data / "logs/payment.jsonl"),
            "BYPAY_ADMIN_EMAIL": os.environ.get("BYPAY_LOCAL_ADMIN_EMAIL", "admin@local.bingyun"),
            "BYPAY_ADMIN_PASSWORD": os.environ.get("BYPAY_LOCAL_ADMIN_PASSWORD") or secrets.token_urlsafe(20),
            "ALIPAY_ENV": "sandbox", "ALLOW_REGISTRATION": "true", "TEST_MAX_AMOUNT": "3000.00",
            "BYPAY_CALLBACK_HOSTS": "test.bingyun.vip",
        }
        source.parent.mkdir(parents=True, exist_ok=True)
        with source.open("x", encoding="utf-8") as fp:
            fp.write("\n".join(f"{k}={_quote(v)}" for k, v in values.items()) + "\n")
        source.chmod(0o600)
    config = load_runtime()
    from .app import create_app
    from werkzeug.security import check_password_hash
    app = create_app(config)
    with app.extensions["store"].connect() as db:
        row = db.execute("SELECT email,password FROM users WHERE role='admin'").fetchone()
    if row:
        print("管理员账号：" + row["email"])
        if local and config.get("BYPAY_ADMIN_PASSWORD") and check_password_hash(row["password"], config["BYPAY_ADMIN_PASSWORD"]):
            target = Path(config["DATABASE"]).parent / ".admin-credentials"
            target.write_text("账号：" + row["email"] + "\n密码：" + config["BYPAY_ADMIN_PASSWORD"] + "\n", encoding="utf-8")
            target.chmod(0o600)
            print("管理员凭据：" + str(target))
    else:
        raise ValueError("管理员未配置，请设置当前环境的 BYPAY_ADMIN_EMAIL 和 BYPAY_ADMIN_PASSWORD")
    print("配置文件：" + str(source))
    print("数据文件：" + str(config.get("DATABASE", ROOT / "data/web.sqlite3")))
    print("访问地址：" + config["PUBLIC_BASE_URL"] + "/login")
    payment_cfg = app.extensions["channels"].payment_config()
    print("支付核验：自动查询支付宝并验签，校验订单号、金额与商户配置")
    print("支付宝通知：" + (payment_cfg["notify_url"] or "未配置公网入口，使用主动查询确认付款"))
    if local:
        print("本机别名：http://127.0.0.1:" + os.environ.get("BYPAY_FRONTEND_PORT", "888") + "/login")


def check_ports():
    front = int(os.environ["BYPAY_FRONTEND_PORT"])
    back = int(os.environ["BYPAY_BACKEND_PORT"])
    host = os.environ.get("BYPAY_BIND_HOST", "127.0.0.1")
    ports = [(host, front)]
    if os.environ.get("BYPAY_RUN_MODE") == "local":
        if front == back: raise ValueError("网页端口与 API 端口不能相同")
        ports.append(("127.0.0.1", back))
    for bind, port in ports:
        family = socket.AF_INET6 if ":" in bind else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            # Match Gunicorn/Vite restart behavior: TIME_WAIT is not a live service.
            # SO_REUSEADDR still rejects a conflicting active TCP listener.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try: sock.bind((bind, port))
            except OSError as exc:
                raise ValueError(f"端口 {bind}:{port} 已占用，请停止旧启动进程或通过 --port / --api-port 更换端口") from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--check-ports", action="store_true")
    parser.add_argument("--resolve", action="store_true")
    args = parser.parse_args()
    try:
        if args.resolve:
            mode, source = resolve_launch()
            print(mode)
            print(source)
        if args.check_ports: check_ports()
        if args.prepare: prepare()
    except Exception as exc:
        print("启动检查失败：" + str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
