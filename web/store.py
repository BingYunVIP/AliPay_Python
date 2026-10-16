import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from decimal import Decimal
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import generate_password_hash

from bypay.payment_validation import validate_notification
from .settings import TIMING_DEFAULTS


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def present_order(order):
    result = {k: v for k, v in dict(order).items() if k not in {"app_id", "seller_id", "user_id", "channel_version", "request_hash", "provider_notify_url", "provider_return_url"}}
    result["payment_payload"] = json.loads(result.get("payment_payload") or "{}")
    if not result["payment_payload"] and result.get("pay_url"):
        result["payment_payload"] = {"kind": "url", "url": result["pay_url"]}
    result["refundable_amount"] = format(max(Decimal("0"), Decimal(result["amount"]) - Decimal(result.get("refunded_amount", "0"))), ".2f") if result.get("paid_at") else "0.00"
    if result["status"] == "CREATED" and result.get("expires_at", 0) <= int(time.time()): result["status"] = "EXPIRED"
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user'
                    CHECK(role IN ('admin','user')), created_at INTEGER NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_admin ON users(role) WHERE role='admin';
                CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    csrf TEXT NOT NULL, expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS api_keys (
                    hash TEXT PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
                    suffix TEXT NOT NULL, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (
                    out_trade_no TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    subject TEXT NOT NULL, amount TEXT NOT NULL, product TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'alipay', status TEXT NOT NULL DEFAULT 'CREATED',
                    pay_url TEXT NOT NULL, trade_no TEXT UNIQUE,
                    app_id TEXT NOT NULL, seller_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL, paid_at INTEGER, notify_url TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS user_orders ON orders(user_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, out_trade_no TEXT, accepted INTEGER NOT NULL,
                    reason TEXT NOT NULL, created_at INTEGER NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'notification', details TEXT NOT NULL DEFAULT '{}', user_id INTEGER);
                CREATE TABLE IF NOT EXISTS limits (key TEXT PRIMARY KEY, count INTEGER, expires INTEGER);
                CREATE TABLE IF NOT EXISTS console_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS service_health (name TEXT PRIMARY KEY, last_seen INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS channel_versions (id INTEGER PRIMARY KEY, channel TEXT NOT NULL, payload BLOB NOT NULL, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS channel_current (channel TEXT PRIMARY KEY, version INTEGER NOT NULL REFERENCES channel_versions(id));
                CREATE TABLE IF NOT EXISTS channel_configs (
                    id INTEGER PRIMARY KEY, channel TEXT NOT NULL, name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, is_default INTEGER NOT NULL DEFAULT 0,
                    version INTEGER REFERENCES channel_versions(id), revision INTEGER NOT NULL DEFAULT 1);
                CREATE UNIQUE INDEX IF NOT EXISTS default_channel_config ON channel_configs(channel) WHERE is_default=1;
                CREATE TABLE IF NOT EXISTS payment_checks (
                    out_trade_no TEXT PRIMARY KEY REFERENCES orders(out_trade_no),
                    next_check INTEGER NOT NULL, locked_until INTEGER NOT NULL DEFAULT 0,
                    lease TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                    checked_at INTEGER, state TEXT NOT NULL DEFAULT 'scheduled', error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS due_payment_checks ON payment_checks(next_check);
                CREATE TABLE IF NOT EXISTS payment_returns (
                    hash TEXT PRIMARY KEY, out_trade_no TEXT NOT NULL REFERENCES orders(out_trade_no),
                    expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS checkout_jobs (
                    out_trade_no TEXT PRIMARY KEY REFERENCES orders(out_trade_no),
                    locked_until INTEGER NOT NULL DEFAULT 0, lease TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS refunds (
                    refund_no TEXT PRIMARY KEY, out_trade_no TEXT NOT NULL REFERENCES orders(out_trade_no),
                    amount TEXT NOT NULL, reason TEXT NOT NULL, actor INTEGER NOT NULL,
                    status TEXT NOT NULL, created_at INTEGER NOT NULL, confirmed_at INTEGER,
                    locked_until INTEGER NOT NULL DEFAULT 0, next_query INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS order_refunds ON refunds(out_trade_no,created_at DESC);
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, out_trade_no TEXT UNIQUE NOT NULL REFERENCES orders(out_trade_no),
                    user_id INTEGER NOT NULL, url TEXT NOT NULL, payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL,
                    locked_until INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT NOT NULL DEFAULT '');
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "disabled" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0")
            order_columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}
            if "channel_version" not in order_columns:
                db.execute("ALTER TABLE orders ADD COLUMN channel_version INTEGER")
            for name, definition in {"expires_at": "INTEGER", "payment_payload": "TEXT NOT NULL DEFAULT '{}'", "request_hash": "TEXT", "provider_notify_url": "TEXT", "provider_return_url": "TEXT", "payment_config_id": "INTEGER", "deleted_at": "INTEGER", "refunded_amount": "TEXT NOT NULL DEFAULT '0.00'", "provider_status": "TEXT NOT NULL DEFAULT ''", "cashier_qr": "TEXT NOT NULL DEFAULT ''", "active_refund_no": "TEXT"}.items():
                if name not in order_columns: db.execute(f"ALTER TABLE orders ADD COLUMN {name} {definition}")
            db.execute("UPDATE orders SET expires_at=created_at+900 WHERE expires_at IS NULL")
            now = int(time.time())
            saved = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM console_settings")}
            db.execute("INSERT OR IGNORE INTO payment_checks(out_trade_no,next_check) SELECT out_trade_no,? FROM orders WHERE status='CREATED' AND channel='alipay' AND created_at>=?", (now + saved.get("poll_interval_seconds", 10), now - saved.get("query_window_minutes", 1440) * 60))
            db.execute("DELETE FROM payment_returns WHERE expires<?", (now,))
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def bootstrap(self, email, password):
        if not email or not password:
            return
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                return
            if not 4 <= len(password) <= 128:
                raise ValueError("BYPAY_ADMIN_PASSWORD must contain 4 to 128 characters")
            db.execute("INSERT INTO users(email,password,role,created_at) VALUES (?,?,?,?)",
                       (email.lower().strip(), generate_password_hash(password), "admin", int(time.time())))

    def user_by_email(self, email):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            return dict(row) if row else None

    def register(self, email, password):
        with self.connect() as db:
            cur = db.execute("INSERT INTO users(email,password,created_at) VALUES (?,?,?)",
                             (email, generate_password_hash(password), int(time.time())))
            return {"id": cur.lastrowid, "email": email, "role": "user"}

    def session(self, user_id):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                       (digest(token), user_id, csrf, int(time.time()) + 43200))
        return token, csrf

    def authenticate(self, token, api=False):
        with self.connect() as db:
            if api:
                row = db.execute("SELECT u.id,u.email,u.role FROM api_keys k JOIN users u ON u.id=k.user_id WHERE k.hash=? AND u.disabled=0", (digest(token),)).fetchone()
            else:
                row = db.execute("SELECT u.id,u.email,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>? AND u.disabled=0", (digest(token), int(time.time()))).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE hash=?", (digest(token),))

    def rotate_key(self, user_id):
        token = "byp_" + secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM api_keys WHERE user_id=?", (user_id,))
            db.execute("INSERT INTO api_keys VALUES (?,?,?,?)", (digest(token), user_id, token[-4:], int(time.time())))
        return token

    def key_info(self, user_id):
        with self.connect() as db:
            row = db.execute("SELECT suffix,created_at FROM api_keys WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def rate_limit(self, key, max_count, window=60):
        now = int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM limits WHERE expires<=?", (now,))
            db.execute("INSERT INTO limits VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1", (key, now + window))
            return db.execute("SELECT count FROM limits WHERE key=?", (key,)).fetchone()[0] <= max_count

    def order(self, number):
        with self.connect() as db:
            row = db.execute("SELECT * FROM orders WHERE out_trade_no=?", (number,)).fetchone()
            return dict(row) if row else None

    def create_order(self, user_id, payload, pay_url, cfg, return_token=None, payment_payload=None, request_hash=None):
        with self.connect() as db:
            db.execute("INSERT INTO orders(out_trade_no,user_id,subject,amount,product,pay_url,app_id,seller_id,created_at,notify_url,channel_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (payload["out_trade_no"], user_id, payload["subject"], payload["amount"], payload["product"], pay_url, cfg["app_id"], cfg["seller_id"], int(time.time()), payload.get("notify_url", ""), cfg.get("channel_version")))
            db.execute("UPDATE orders SET expires_at=?,payment_payload=?,request_hash=? WHERE out_trade_no=?", (cfg.get("expires_at", int(time.time()) + 900), json.dumps(payment_payload or {}), request_hash, payload["out_trade_no"]))
            db.execute("UPDATE orders SET provider_notify_url=?,provider_return_url=? WHERE out_trade_no=?", (cfg.get("notify_url", ""), cfg.get("return_url", ""), payload["out_trade_no"]))
            db.execute("UPDATE orders SET payment_config_id=? WHERE out_trade_no=?", (cfg.get("payment_config_id"), payload["out_trade_no"]))
            db.execute("INSERT INTO payment_checks(out_trade_no,next_check) VALUES (?,?)", (payload["out_trade_no"], int(time.time()) + self.setting("poll_interval_seconds", 10)))
            if hasattr(self,'operations'):self.operations.snapshot(db,user_id,payload['out_trade_no'],payload['amount'])
            if return_token:
                db.execute("INSERT INTO payment_returns VALUES (?,?,?)", (digest(return_token), payload["out_trade_no"], int(time.time()) + 86400))
        return self.order(payload["out_trade_no"])

    def claim_checkout(self, number):
        now, lease = int(time.time()), secrets.token_hex(16)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("SELECT payment_payload FROM orders WHERE out_trade_no=?", (number,)).fetchone()
            if json.loads(order["payment_payload"]): return None
            db.execute("INSERT OR IGNORE INTO checkout_jobs(out_trade_no) VALUES (?)", (number,))
            if db.execute("SELECT locked_until FROM checkout_jobs WHERE out_trade_no=?", (number,)).fetchone()[0] > now: return None
            db.execute("UPDATE checkout_jobs SET locked_until=?,lease=? WHERE out_trade_no=?", (now + 60, lease, number))
        return lease

    def finish_checkout(self, number, lease, payment=None, trade_no=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT lease FROM checkout_jobs WHERE out_trade_no=?", (number,)).fetchone()
            if not current or current["lease"] != lease: return
            if payment:
                order = db.execute("SELECT trade_no FROM orders WHERE out_trade_no=?", (number,)).fetchone()
                if order["trade_no"] and trade_no and order["trade_no"] != trade_no: raise ValueError("trade_mismatch")
                db.execute("UPDATE orders SET payment_payload=?,pay_url=?,trade_no=COALESCE(trade_no,?) WHERE out_trade_no=?", (json.dumps(payment), payment.get("url", ""), trade_no, number))
            db.execute("UPDATE checkout_jobs SET locked_until=0 WHERE out_trade_no=? AND lease=?", (number, lease))

    def policy(self):
        with self.connect() as db:
            rows = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM console_settings")}
        return {key: rows.get(key, value) for key, value in TIMING_DEFAULTS.items()}

    def return_order(self, token):
        if not isinstance(token, str) or not 40 <= len(token) <= 128:
            return None
        with self.connect() as db:
            row = db.execute("SELECT o.* FROM payment_returns r JOIN orders o USING(out_trade_no) WHERE r.hash=? AND r.expires>?", (digest(token), int(time.time()))).fetchone()
            return dict(row) if row else None

    def issue_return_token(self, number):
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO payment_returns VALUES (?,?,?)", (digest(token), number, int(time.time()) + 86400))
        return token

    def payment_check(self, number):
        with self.connect() as db:
            row = db.execute("SELECT * FROM payment_checks WHERE out_trade_no=?", (number,)).fetchone()
            return dict(row) if row else None

    def claim_payment_check(self, number=None):
        """Lease a query across workers and browser requests, including a cooldown."""
        now, lease = int(time.time()), secrets.token_hex(16)
        window = self.policy()["query_window_minutes"] * 60
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if number:
                db.execute("INSERT OR IGNORE INTO payment_checks(out_trade_no,next_check) SELECT out_trade_no,? FROM orders WHERE out_trade_no=? AND status='CREATED'", (now, number))
                where, args = "o.out_trade_no=?", [number]
            else:
                where, args = "c.next_check<=? AND o.created_at>=?", [now, now - window]
            row = db.execute(f"SELECT c.* FROM payment_checks c JOIN orders o USING(out_trade_no) WHERE o.status='CREATED' AND o.channel='alipay' AND c.locked_until<=? AND {where} ORDER BY c.next_check LIMIT 1", [now, *args]).fetchone()
            if not row:
                return None
            db.execute("UPDATE payment_checks SET lease=?,locked_until=?,attempts=attempts+1 WHERE out_trade_no=?", (lease, now + 60, row["out_trade_no"]))
            return {**dict(row), "lease": lease, "attempts": row["attempts"] + 1}

    def finish_payment_check(self, job, state, error=""):
        now = int(time.time())
        order = self.order(job["out_trade_no"])
        age = now - order["created_at"]
        interval = self.policy()["poll_interval_seconds"]
        delay = interval if age < 180 else max(interval, 30) if age < 900 else max(interval, 120) if age < 3600 else max(interval, 600)
        if error:
            delay = max(delay, min(600, 30 * 2 ** min(job["attempts"] - 1, 5)))
        with self.connect() as db:
            db.execute("UPDATE payment_checks SET next_check=?,locked_until=?,checked_at=?,state=?,error=? WHERE out_trade_no=? AND lease=?", (now + delay, now + interval, now, state, error, job["out_trade_no"], job["lease"]))

    def orders(self, user_id, admin=False):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM orders WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 100" if admin else "SELECT * FROM orders WHERE user_id=? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 100", () if admin else (user_id,)).fetchall()
            return [dict(row) for row in rows]

    def apply_notification(self, params, app_id, seller_id):
        number = params.get("out_trade_no", "")
        first_delay = self.policy()["callback_first_delay_seconds"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM orders WHERE out_trade_no=?", (number,)).fetchone()
            order = dict(row) if row else None
            validate_notification(params, order, app_id, seller_id)
            if order["app_id"] != app_id or order["seller_id"] != seller_id:
                raise ValueError("order_merchant_mismatch")
            status = params["trade_status"]
            db.execute("UPDATE orders SET provider_status=? WHERE out_trade_no=?", (status,number))
            if status in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
                new_status = order["status"] if order["status"] in {"REFUNDING", "PARTIALLY_REFUNDED", "REFUNDED"} else "PAID"
                db.execute("UPDATE orders SET status=?,trade_no=?,paid_at=COALESCE(paid_at,?) WHERE out_trade_no=?", (new_status,params["trade_no"], int(time.time()), number))
                if not order['paid_at'] and hasattr(self,'operations'):self.operations.paid(db,order)
                if not order["paid_at"] and order["notify_url"]:
                    event_id = str(uuid.uuid4())
                    payload = json.dumps({"id": event_id, "event": "payment.paid", "out_trade_no": number,
                                          "trade_no": params["trade_no"], "amount": order["amount"], "currency": "CNY",
                                          "channel": "alipay", "status": "PAID", "paid_at": int(time.time())}, separators=(",", ":"))
                    db.execute("INSERT INTO outbox(id,out_trade_no,user_id,url,payload,next_attempt) VALUES (?,?,?,?,?,?)", (event_id, number, order["user_id"], order["notify_url"], payload, int(time.time()) + first_delay))
            elif status == "TRADE_CLOSED" and not order["paid_at"]:
                db.execute("UPDATE orders SET status='CLOSED',trade_no=? WHERE out_trade_no=?", (params["trade_no"], number))
            db.execute("INSERT INTO events(out_trade_no,accepted,reason,created_at,stage,user_id) VALUES (?,1,?,?,'order.updated',?)", (number, status, int(time.time()), order["user_id"]))

    def rejected_notification(self, reason):
        with self.connect() as db:
            db.execute("INSERT INTO events(accepted,reason,created_at) VALUES (0,?,?)", (reason, int(time.time())))

    def record_event(self, stage, reason, details, number=None, user_id=None, accepted=True):
        with self.connect() as db:
            db.execute("INSERT INTO events(out_trade_no,accepted,reason,created_at,stage,details,user_id) VALUES (?,?,?,?,?,?,?)",
                       (number, int(accepted), reason, int(time.time()), stage, json.dumps(details, ensure_ascii=False), user_id))
            db.execute("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0)-20000 FROM events)")

    def claim_delivery(self):
        now = int(time.time())
        lease_seconds = max(60, 3 * self.policy()["callback_timeout_seconds"] + 15)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM outbox WHERE status='pending' AND next_attempt<=? AND locked_until<=? ORDER BY next_attempt LIMIT 1", (now, now)).fetchone()
            if not row:
                return None
            db.execute("UPDATE outbox SET locked_until=?,attempts=attempts+1 WHERE id=?", (now + lease_seconds, row["id"]))
            return {**dict(row), "attempts": row["attempts"] + 1}

    def finish_delivery(self, job, success, error=""):
        delays = self.policy()["callback_retry_delays"]
        status = "delivered" if success else "failed" if job["attempts"] > len(delays) else "pending"
        delay = delays[min(job["attempts"] - 1, len(delays) - 1)] if delays else 0
        with self.connect() as db:
            db.execute("UPDATE outbox SET status=?,last_error=?,next_attempt=?,locked_until=0 WHERE id=?", (status, error, int(time.time()) + delay, job["id"]))
        return status

    def events(self, user_id=None):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100" if user_id is None else "SELECT * FROM events WHERE user_id=? ORDER BY id DESC LIMIT 100", () if user_id is None else (user_id,))
            return [{**dict(row), "details": json.loads(row["details"])} for row in rows]

    def setting(self, key, default):
        with self.connect() as db:
            row = db.execute("SELECT value FROM console_settings WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else default

    def heartbeat(self, name):
        with self.connect() as db:
            db.execute("INSERT INTO service_health VALUES (?,?) ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen", (name, int(time.time())))
