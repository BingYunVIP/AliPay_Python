#!/usr/bin/env python3
"""Interactive VPS configuration. Writes only local, ignored secrets."""
import getpass
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def ask(label, default=""):
    return input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip() or default


def main():
    os.umask(0o077)
    target = ROOT / ".env"
    if target.exists():
        raise SystemExit(".env 已存在，未覆盖。请直接编辑该文件后重启服务。")
    domain = ask("冰云支付站点域名", "pay.bingyun.vip")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", domain):
        raise SystemExit("请输入不带 https:// 和路径的域名。")
    email = ask("管理员邮箱")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise SystemExit("邮箱格式不正确。")
    password = getpass.getpass("管理员密码（至少 12 个字符，不回显）: ")
    if len(password) < 12 or len(password) > 128 or password != getpass.getpass("再次输入密码: "):
        raise SystemExit("密码长度不符或两次输入不一致。")
    env = ask("支付宝环境 production / sandbox", "production")
    if env not in {"production", "sandbox"}:
        raise SystemExit("环境必须为 production 或 sandbox。")
    app_id = ask("支付宝 APP ID")
    seller_id = ask("收款方支付宝 UID（2088 开头的 16 位数字）")
    if not app_id.isdigit() or not re.fullmatch(r"2088\d{12}", seller_id):
        raise SystemExit("APP ID 或收款方 UID 格式不正确。")
    private = Path(ask("应用私钥 PEM 文件的绝对路径")).expanduser()
    public = Path(ask("支付宝公钥 PEM 文件的绝对路径")).expanduser()
    if not private.is_file() or not public.is_file():
        raise SystemExit("未找到密钥文件。")
    allow = ask("开放普通用户注册？true / false", "false")
    if allow not in {"true", "false"}:
        raise SystemExit("请填写 true 或 false。")
    secret_dir = ROOT / "secrets"
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    for source, name in [(private, "app-private.pem"), (public, "alipay-public.pem")]:
        dest = secret_dir / name
        if source.resolve() != dest.resolve():
            shutil.copyfile(source, dest)
        dest.chmod(0o600)
    (ROOT / "data").mkdir(mode=0o700, exist_ok=True)
    values = {"PUBLIC_BASE_URL": f"https://{domain}", "ALIPAY_ENV": env,
              "ALIPAY_APP_ID": app_id, "ALIPAY_SELLER_ID": seller_id,
              "ALIPAY_PRIVATE_KEY_FILE": str(secret_dir / "app-private.pem"), "ALIPAY_PUBLIC_KEY_FILE": str(secret_dir / "alipay-public.pem"),
              "BYPAY_ADMIN_EMAIL": email, "BYPAY_ADMIN_PASSWORD": password, "ALLOW_REGISTRATION": allow,
              "TEST_MAX_AMOUNT": "10.00", "DATABASE": str(ROOT / "data/web.sqlite3"),
              "BYPAY_LOG_FILE": str(ROOT / "data/logs/payment.jsonl"), "BYPAY_TRUST_PROXY": "1",
              "BYPAY_CALLBACK_HOSTS": "test.bingyun.vip"}
    def quote(value):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    with target.open("x") as fp:
        fp.write("\n".join(f"{key}={quote(value)}" for key, value in values.items()) + "\n")
    print(f"\n已保存配置。回调地址：https://{domain}/alipay/notify")
    print("原生启动：./run-web.sh（监听 127.0.0.1:8000，同时启动回调 worker）")
    print("systemd 服务模板位于 deploy/；现有 Nginx 将 pay.bingyun.vip 代理到 127.0.0.1:8000。")
    print("访问站点登录后，在控制台查看订单、事件日志与回调检查。")


if __name__ == "__main__":
    main()
