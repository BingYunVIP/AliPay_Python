#!/usr/bin/env python3
import argparse
import getpass
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pay_http import request_json

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="配置独立商户测试站")
    parser.add_argument("--native", action="store_true", help="生成 Linux / systemd 本机路径；默认使用 Docker 路径")
    args = parser.parse_args()
    os.umask(0o077)
    target = ROOT / ".env"
    if target.exists():
        raise SystemExit(".env 已存在，未覆盖。")
    api_key = getpass.getpass("从 Pay 普通用户控制台生成的 API 密钥（不回显）: ")
    if not api_key.startswith("byp_") or len(api_key) < 30:
        raise SystemExit("密钥格式不正确。")
    password = getpass.getpass("本测试站访问密码（至少 12 个字符）: ")
    if len(password) < 12 or password != getpass.getpass("再次输入密码: "):
        raise SystemExit("密码长度不足或两次输入不一致。")
    try:
        response, status = request_json("GET", "https://pay.bingyun.vip/api/webhooks/public-key")
        if status != 200 or response.get("algorithm") != "Ed25519":
            raise ValueError("Cannot retrieve Pay public key")
        public_key = response["public_key"]
        if not isinstance(serialization.load_pem_public_key(public_key.encode()), Ed25519PublicKey):
            raise ValueError("Invalid public key")
    except Exception:
        raise SystemExit("无法读取 Pay 回调验签公钥。请先部署 pay.bingyun.vip，确认 /api/webhooks/public-key 可访问后重试。未保存密钥。")
    secrets_dir = ROOT / "secrets"
    secrets_dir.mkdir(mode=0o700, exist_ok=True)
    key_file = secrets_dir / "bypay-public.pem"
    key_file.write_text(public_key)
    key_file.chmod(0o600)
    if not args.native and os.geteuid() == 0:
        os.chown(secrets_dir, 10001, 10001)
        os.chown(key_file, 10001, 10001)
    values = {"PAY_ORIGIN": "https://pay.bingyun.vip", "DEMO_ORIGIN": "https://test.bingyun.vip",
              "PAY_API_KEY": api_key, "PAY_PUBLIC_KEY_FILE": str(key_file) if args.native else "/app/secrets/bypay-public.pem",
              "DEMO_USER": "tester", "DEMO_PASSWORD": password,
              "DATABASE": str(ROOT / "data/demo.sqlite3") if args.native else "/app/data/demo.sqlite3"}
    def quote(value):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    with target.open("x") as fp:
        fp.write("\n".join(f"{k}={quote(v)}" for k, v in values.items()) + "\n")
    print("已配置独立商户测试站。登录用户名：tester")
    if args.native:
        print("本机启动：./run.sh（监听 127.0.0.1:8100）")
        print("将 test.bingyun.vip 的现有 Nginx 反向代理指向 127.0.0.1:8100。")
        return
    print("独立 VPS 启动：docker compose up -d --build")
    print("如 VPS 已有 HTTPS 反代：docker compose -f compose.yaml -f compose.existing-proxy.yaml up -d --build")
    print("已有反代时，将 test.bingyun.vip 指向本机 127.0.0.1:8100。")
    if os.geteuid() != 0:
        print("容器读取公钥前执行：sudo chown -R 10001:10001 secrets")


if __name__ == "__main__":
    main()
