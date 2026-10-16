"""Independent merchant demo. It knows only the Pay HTTP API and its public signing key."""
import base64
import hashlib
import fcntl
import json
import os
import re
import secrets
import ipaddress
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
from configuration import Configuration, ConfigurationError, local_hosts, valid_public_key

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def create_app(settings=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(PAY_ORIGIN=os.getenv("PAY_ORIGIN", "http://127.0.0.1:888"),
                      DEMO_ORIGIN=os.getenv("DEMO_ORIGIN", "http://127.0.0.1:8100"),
                      PAY_API_KEY=os.getenv("PAY_API_KEY", ""), DEMO_PORT=os.getenv("DEMO_PORT", "8100"),
                      PAY_PUBLIC_KEY_FILE=os.getenv("PAY_PUBLIC_KEY_FILE", str(ROOT / "secrets/bypay-public.pem")),
                      DATABASE=os.getenv("DATABASE", str(ROOT / "data/demo.sqlite3")),
                      MAX_CONTENT_LENGTH=32768)
    app.config.update(settings or {})
    port = int(app.config["DEMO_PORT"])
    allowed_origins = {app.config["DEMO_ORIGIN"].rstrip("/")} | {
        f"http://{'[' + host + ']' if ':' in host else host}:{port}" for host in local_hosts()
    }
    database = Path(app.config["DATABASE"])
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = app.config.get("CONFIG_FILE", database.parent / '.env' if app.testing else ROOT / '.env')
    config = Configuration(source, app.config, database.parent / 'config.lock')
    initial = config.read()
    app.extensions['configuration'] = config

    def can_manage():
        # Local browser / WSL / LAN development, without a login or management link
        try:
            peer = ipaddress.ip_address(request.remote_addr or '')
            local_peer = peer.is_loopback or peer.is_private
        except ValueError:
            local_peer = False
        return (local_peer and request.scheme == 'http'
                and urlsplit(request.host_url).hostname in local_hosts()
                and not any(k.lower() == 'forwarded' or k.lower().startswith('x-forwarded-') for k in request.headers.keys()))

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
        columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
        if 'public_key_pem' not in columns:
            connection.execute("ALTER TABLE orders ADD COLUMN public_key_pem TEXT NOT NULL DEFAULT ''")
        if valid_public_key(initial.get('PAY_PUBLIC_KEY_PEM')):
            connection.execute("UPDATE orders SET public_key_pem=? WHERE public_key_pem=''", (initial['PAY_PUBLIC_KEY_PEM'],))
    database.chmod(0o600)

    def write_log(stage, number=None, detail=None):
        record = json.dumps({"time":int(time.time()),"stage":stage,"number":number,"detail":detail or {}},ensure_ascii=False)
        log_path=database.parent/'logs/demo.jsonl'
        log_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (log_path.parent/'demo.lock').open('a') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            if log_path.exists() and log_path.stat().st_size>5_000_000: log_path.replace(log_path.with_suffix('.jsonl.1'))
            fd=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try: os.write(fd,(record+'\n').encode())
            finally: os.close(fd)
        print(record,flush=True)

    def log(stage, number=None, detail=None):
        with db() as connection:
            connection.execute("INSERT INTO logs(time,stage,number,detail) VALUES (?,?,?,?)", (int(time.time()), stage, number, json.dumps(detail or {}, ensure_ascii=False)))
            connection.execute("DELETE FROM logs WHERE id < (SELECT COALESCE(MAX(id),0)-5000 FROM logs)")
        write_log(stage, number, detail)

    def pay_request(settings, method, path, payload=None):
        if not settings.get("PAY_API_KEY"): return {"error":"pay_key_missing"}, 503
        return request_json(method, settings['PAY_ORIGIN'] + path, payload=payload, api_key=settings['PAY_API_KEY'])

    @app.before_request
    def authenticate():
        if request.path in {"/notify", "/health"}:
            return None
        if request.method == "POST":
            if request.headers.get("Origin") not in allowed_origins | {config.read()["DEMO_ORIGIN"]} or request.headers.get("X-Demo-Request") != "1" or not request.is_json:
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
            orders = [dict(row) for row in connection.execute("SELECT number,amount,status,pay_url,created_at,trade_no,paid_at FROM orders ORDER BY created_at DESC LIMIT 30")]
            logs = [{**dict(row), "detail": json.loads(row["detail"])} for row in connection.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 80")]
        return jsonify(orders=orders, logs=logs, **config.public(editable=can_manage()))

    @app.get('/api/config')
    def get_config():
        return jsonify(**config.public(editable=can_manage()),
                       local_pay_origin='http://127.0.0.1:888', local_demo_origin=f'http://127.0.0.1:{port}')

    @app.post('/api/config')
    @app.post('/api/config/check')
    def update_config():
        if not can_manage():
            return jsonify(error='local_configuration_only'), 403
        save = request.path == '/api/config'
        try:
            result = config.apply(request.get_json(silent=True), save=save)
        except ConfigurationError as error:
            log('config.rejected', detail={'error':error.code, 'http_status':error.upstream_status, 'saved':False})
            return jsonify(error=error.code, upstream_status=error.upstream_status, key_valid=error.key_valid, saved=False), error.http_status
        except OSError:
            log('config.rejected', detail={'error':'config_write_failed', 'saved':False})
            return jsonify(error='config_write_failed', saved=False), 500
        log('config.saved' if save else 'config.checked', detail={'pay_origin':result['pay_origin'], 'callback_url':result['callback_url'], 'saved':save})
        return jsonify(**result, saved=save, verified=True)

    @app.post("/api/orders")
    def create_order():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="invalid_request"), 400
        settings = config.read()
        if not settings.get('PAY_API_KEY'):
            return jsonify(error='pay_key_missing'), 400
        amount = payload.get("amount", "")
        if not isinstance(amount, str) or not re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?", amount) or not Decimal("0") < Decimal(amount) <= Decimal("10"):
            return jsonify(error="amount_between_001_and_10"), 400
        amount = format(Decimal(amount), ".2f")
        number = "DEMO_" + str(int(time.time())) + "_" + secrets.token_hex(6)
        with db() as connection:
            connection.execute("INSERT INTO orders(number,amount,status,created_at,public_key_pem) VALUES (?,?,'CREATING',?,?)", (number, amount, int(time.time()), settings.get("PAY_PUBLIC_KEY_PEM", "")))
        data = {"out_trade_no": number, "amount": amount, "subject": "独立商户回调测试", "channel": "alipay", "product": payload.get("product", "page"), "notify_url": settings["DEMO_ORIGIN"] + "/notify"}
        log("create.request", number, data)
        response, code = pay_request(settings, "POST", "/api/v1/payments", data)
        order = response.get("order")
        if not isinstance(order, dict) or not isinstance(order.get("pay_url"), str):
            order = {}
        valid_url = urlsplit(order.get("pay_url", ""))
        if code not in {200, 201} or valid_url.scheme != "https" or valid_url.hostname not in {"openapi.alipay.com", "openapi-sandbox.dl.alipaydev.com"}:
            with db() as connection:
                connection.execute("UPDATE orders SET status='CREATE_FAILED' WHERE number=? AND status='CREATING'", (number,))
            if code == 401:
                config.mark_key(settings, 'invalid')
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
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get('out_trade_no'), str):
                raise ValueError('invalid_event')
            with db() as connection:
                key_order = connection.execute('SELECT public_key_pem FROM orders WHERE number=?', (payload['out_trade_no'],)).fetchone()
            pem = key_order['public_key_pem'] if key_order and key_order['public_key_pem'] else config.read().get('PAY_PUBLIC_KEY_PEM', '')
            public = serialization.load_pem_public_key(pem.encode())
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
        write_log("notify.verified", number, payload)
        return "success", 200, {"Content-Type": "text/plain"}

    log("app.started", detail={"pay_origin":initial["PAY_ORIGIN"],"callback_url":initial["DEMO_ORIGIN"] + "/notify"})
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8100)
