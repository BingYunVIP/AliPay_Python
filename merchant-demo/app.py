"""Independent merchant demo. It knows only the Pay HTTP API and its public signing key."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from pay_http import request_json

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def create_app(settings=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(PAY_ORIGIN=os.getenv("PAY_ORIGIN", "https://pay.bingyun.vip"),
                      DEMO_ORIGIN=os.getenv("DEMO_ORIGIN", "https://test.bingyun.vip"),
                      PAY_API_KEY=os.getenv("PAY_API_KEY", ""), DEMO_USER=os.getenv("DEMO_USER", "tester"),
                      DEMO_PASSWORD=os.getenv("DEMO_PASSWORD", ""),
                      PAY_PUBLIC_KEY_FILE=os.getenv("PAY_PUBLIC_KEY_FILE", str(ROOT / "secrets/bypay-public.pem")),
                      DATABASE=os.getenv("DATABASE", str(ROOT / "data/demo.sqlite3")),
                      MAX_CONTENT_LENGTH=32768)
    app.config.update(settings or {})
    if len(app.config["DEMO_PASSWORD"]) < 12:
        raise ValueError("Set DEMO_PASSWORD to at least 12 characters")
    pay_origin = app.config["PAY_ORIGIN"].rstrip("/")
    parsed_origin = urlsplit(pay_origin)
    if parsed_origin.scheme != "https" or not parsed_origin.hostname or parsed_origin.path or parsed_origin.query or parsed_origin.fragment or parsed_origin.username:
        raise ValueError("PAY_ORIGIN must be an HTTPS origin")
    callback_url = app.config["DEMO_ORIGIN"].rstrip("/") + "/notify"
    database = Path(app.config["DATABASE"])
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def db():
        connection = sqlite3.connect(database, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    with db() as connection:
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS orders (number TEXT PRIMARY KEY, amount TEXT NOT NULL, status TEXT NOT NULL,
                pay_url TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, trade_no TEXT, paid_at INTEGER);
            CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, body_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY, time INTEGER NOT NULL, stage TEXT NOT NULL,
                number TEXT, detail TEXT NOT NULL);
        """)
    database.chmod(0o600)

    def log(stage, number=None, detail=None):
        with db() as connection:
            connection.execute("INSERT INTO logs(time,stage,number,detail) VALUES (?,?,?,?)", (int(time.time()), stage, number, json.dumps(detail or {}, ensure_ascii=False)))
            connection.execute("DELETE FROM logs WHERE id < (SELECT COALESCE(MAX(id),0)-5000 FROM logs)")

    def pay_request(method, path, payload=None):
        return request_json(method, pay_origin + path, payload=payload, api_key=app.config["PAY_API_KEY"])

    @app.before_request
    def authenticate():
        if request.path in {"/notify", "/health"}:
            return None
        credentials = request.authorization
        if (not credentials or not hmac.compare_digest((credentials.username or "").encode(), app.config["DEMO_USER"].encode()) or
                not hmac.compare_digest((credentials.password or "").encode(), app.config["DEMO_PASSWORD"].encode())):
            return "Sign in to the merchant demo", 401, {"WWW-Authenticate": 'Basic realm="Pay merchant demo", charset="UTF-8"'}
        if request.method == "POST":
            if request.headers.get("Origin") != app.config["DEMO_ORIGIN"] or request.headers.get("X-Demo-Request") != "1" or not request.is_json:
                return jsonify(error="invalid_request"), 403

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                                 "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        return response

    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.get("/")
    def home():
        return send_from_directory(ROOT / "static", "index.html")

    @app.get("/static/<path:path>")
    def static_files(path):
        return send_from_directory(ROOT / "static", path)

    @app.get("/api/state")
    def state():
        with db() as connection:
            orders = [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 30")]
            logs = [{**dict(row), "detail": json.loads(row["detail"])} for row in connection.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 80")]
        return jsonify(orders=orders, logs=logs, pay_origin=pay_origin, callback_url=callback_url,
                       key_configured=bool(app.config["PAY_API_KEY"]), verification_key_configured=Path(app.config["PAY_PUBLIC_KEY_FILE"]).is_file())

    @app.post("/api/orders")
    def create_order():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="invalid_request"), 400
        amount = payload.get("amount", "")
        if not isinstance(amount, str) or not re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?", amount) or not Decimal("0") < Decimal(amount) <= Decimal("10"):
            return jsonify(error="amount_between_001_and_10"), 400
        amount = format(Decimal(amount), ".2f")
        number = "DEMO_" + str(int(time.time())) + "_" + secrets.token_hex(6)
        with db() as connection:
            connection.execute("INSERT INTO orders(number,amount,status,created_at) VALUES (?,?,'CREATING',?)", (number, amount, int(time.time())))
        data = {"out_trade_no": number, "amount": amount, "subject": "独立商户回调测试", "channel": "alipay", "product": payload.get("product", "page"), "notify_url": callback_url}
        log("create.request", number, data)
        response, code = pay_request("POST", "/api/v1/payments", data)
        order = response.get("order")
        if not isinstance(order, dict) or not isinstance(order.get("pay_url"), str):
            order = {}
        valid_url = urlsplit(order.get("pay_url", ""))
        if code not in {200, 201} or valid_url.scheme != "https" or valid_url.hostname not in {"openapi.alipay.com", "openapi-sandbox.dl.alipaydev.com"}:
            with db() as connection:
                connection.execute("UPDATE orders SET status='CREATE_FAILED' WHERE number=? AND status='CREATING'", (number,))
            log("create.failed", number, {"http_status": code, "error": response.get("error", "invalid_checkout_url")})
            return jsonify(error=response.get("error", "invalid_checkout_url")), 502
        with db() as connection:
            connection.execute("UPDATE orders SET status=CASE WHEN status='CREATING' THEN 'WAITING' ELSE status END,pay_url=? WHERE number=?", (order["pay_url"], number))
        log("create.accepted", number, {"http_status": code, "status": order["status"], "amount": order["amount"], "note": "本地订单已创建，等待支付回调。"})
        return jsonify(order=order), 201

    @app.post("/notify")
    def notify():
        raw = request.get_data()
        try:
            stamp = request.headers.get("X-ByPay-Timestamp", "")
            if abs(time.time() - int(stamp)) > 300:
                raise ValueError("expired_timestamp")
            public = serialization.load_pem_public_key(Path(app.config["PAY_PUBLIC_KEY_FILE"]).read_bytes())
            if not isinstance(public, Ed25519PublicKey):
                raise ValueError("invalid_key_type")
            public.verify(base64.b64decode(request.headers.get("X-ByPay-Signature", ""), validate=True), stamp.encode() + b"." + raw)
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("id") != request.headers.get("X-ByPay-Event-Id") or payload.get("event") != "payment.paid" or payload.get("status") != "PAID" or payload.get("currency") != "CNY" or payload.get("channel") != "alipay":
                raise ValueError("invalid_event")
            number = payload.get("out_trade_no")
            if not isinstance(number, str) or not isinstance(payload.get("amount"), str) or not re.fullmatch(r"\d{1,8}\.\d{2}", payload["amount"]):
                raise ValueError("invalid_fields")
            if not isinstance(payload.get("trade_no"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", payload["trade_no"]) or not isinstance(payload.get("paid_at"), int) or payload["paid_at"] <= 0:
                raise ValueError("invalid_trade")
            body_hash = hashlib.sha256(raw).hexdigest()
            with db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute("SELECT body_hash FROM receipts WHERE id=?", (payload["id"],)).fetchone()
                if previous:
                    if previous["body_hash"] != body_hash:
                        raise ValueError("reused_event_id")
                    return "success", 200, {"Content-Type": "text/plain"}
                order = connection.execute("SELECT * FROM orders WHERE number=?", (number,)).fetchone()
                if not order or Decimal(order["amount"]) != Decimal(payload["amount"]):
                    raise ValueError("order_or_amount_mismatch")
                if order["trade_no"] and order["trade_no"] != payload.get("trade_no"):
                    raise ValueError("trade_mismatch")
                connection.execute("UPDATE orders SET status='PAID',trade_no=?,paid_at=? WHERE number=?", (payload.get("trade_no"), payload.get("paid_at"), number))
                connection.execute("INSERT INTO receipts VALUES (?,?)", (payload["id"], body_hash))
                connection.execute("INSERT INTO logs(time,stage,number,detail) VALUES (?,'notify.verified',?,?)", (int(time.time()), number, json.dumps(payload, ensure_ascii=False)))
        except (ValueError, OSError, InvalidSignature, TypeError, KeyError):
            log("notify.rejected", detail={"reason": "签名、时间或订单金额校验失败，未更新订单。"})
            return "failure", 400, {"Content-Type": "text/plain"}
        return "success", 200, {"Content-Type": "text/plain"}

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8100)
