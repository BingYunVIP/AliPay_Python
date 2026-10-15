import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import generate_password_hash

from bypay.payment_validation import validate_notification


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


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
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, out_trade_no TEXT UNIQUE NOT NULL REFERENCES orders(out_trade_no),
                    user_id INTEGER NOT NULL, url TEXT NOT NULL, payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL,
                    locked_until INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT NOT NULL DEFAULT '');
            """)
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
        if len(password) < 12:
            raise ValueError("BYPAY_ADMIN_PASSWORD must contain at least 12 characters")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                return
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
                row = db.execute("SELECT u.id,u.email,u.role FROM api_keys k JOIN users u ON u.id=k.user_id WHERE k.hash=?", (digest(token),)).fetchone()
            else:
                row = db.execute("SELECT u.id,u.email,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>?", (digest(token), int(time.time()))).fetchone()
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

    def create_order(self, user_id, payload, pay_url, cfg):
        with self.connect() as db:
            db.execute("INSERT INTO orders(out_trade_no,user_id,subject,amount,product,pay_url,app_id,seller_id,created_at,notify_url) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (payload["out_trade_no"], user_id, payload["subject"], payload["amount"], payload["product"], pay_url, cfg["app_id"], cfg["seller_id"], int(time.time()), payload.get("notify_url", "")))
        return self.order(payload["out_trade_no"])

    def orders(self, user_id, admin=False):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100" if admin else "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 100", () if admin else (user_id,)).fetchall()
            return [dict(row) for row in rows]

    def apply_notification(self, params, app_id, seller_id):
        number = params.get("out_trade_no", "")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM orders WHERE out_trade_no=?", (number,)).fetchone()
            order = dict(row) if row else None
            validate_notification(params, order, app_id, seller_id)
            if order["app_id"] != app_id or order["seller_id"] != seller_id:
                raise ValueError("order_merchant_mismatch")
            status = params["trade_status"]
            if status in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
                db.execute("UPDATE orders SET status='PAID',trade_no=?,paid_at=COALESCE(paid_at,?) WHERE out_trade_no=?", (params["trade_no"], int(time.time()), number))
                if order["status"] != "PAID" and order["notify_url"]:
                    event_id = str(uuid.uuid4())
                    payload = json.dumps({"id": event_id, "event": "payment.paid", "out_trade_no": number,
                                          "trade_no": params["trade_no"], "amount": order["amount"], "currency": "CNY",
                                          "channel": "alipay", "status": "PAID", "paid_at": int(time.time())}, separators=(",", ":"))
                    db.execute("INSERT INTO outbox(id,out_trade_no,user_id,url,payload,next_attempt) VALUES (?,?,?,?,?,?)", (event_id, number, order["user_id"], order["notify_url"], payload, int(time.time())))
            elif status == "TRADE_CLOSED" and order["status"] != "PAID":
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
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM outbox WHERE status='pending' AND next_attempt<=? AND locked_until<=? ORDER BY next_attempt LIMIT 1", (now, now)).fetchone()
            if not row:
                return None
            db.execute("UPDATE outbox SET locked_until=?,attempts=attempts+1 WHERE id=?", (now + 60, row["id"]))
            return {**dict(row), "attempts": row["attempts"] + 1}

    def finish_delivery(self, job, success, error=""):
        delays = [30, 120, 600, 1800, 3600]
        status = "delivered" if success else "failed" if job["attempts"] > len(delays) else "pending"
        delay = delays[min(job["attempts"] - 1, len(delays) - 1)]
        with self.connect() as db:
            db.execute("UPDATE outbox SET status=?,last_error=?,next_attempt=?,locked_until=0 WHERE id=?", (status, error, int(time.time()) + delay, job["id"]))
        return status

    def events(self, user_id=None):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100" if user_id is None else "SELECT * FROM events WHERE user_id=? ORDER BY id DESC LIMIT 100", () if user_id is None else (user_id,))
            return [{**dict(row), "details": json.loads(row["details"])} for row in rows]
