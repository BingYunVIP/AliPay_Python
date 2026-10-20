import hashlib
import json
import re
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
from .payment_terms import matches,method_label,two


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def present_order(order,admin=False):
    result = {k: v for k, v in dict(order).items() if k not in {"app_id", "seller_id", "user_id", "channel_version", "request_hash", "provider_notify_url", "provider_return_url"}}
    required={'fee_cents','fee_reversed_cents','merchant_order_no'} | ({'merchant_id','merchant_email'} if admin else set())
    if required-set(result):
        from flask import current_app,has_app_context
        if has_app_context():
            with current_app.extensions['store'].connect() as db:
                extra=db.execute("SELECT COALESCE(f.fee,0) fee_cents,COALESCE(f.fee_reversed,0) fee_reversed_cents,mo.merchant_no merchant_order_no,m.pid merchant_id,u.email merchant_email FROM orders o LEFT JOIN financial_orders f ON f.number=o.out_trade_no LEFT JOIN merchant_orders mo ON mo.platform_no=o.out_trade_no LEFT JOIN merchant_accounts m ON m.user_id=o.user_id LEFT JOIN users u ON u.id=o.user_id WHERE o.out_trade_no=?",(result['out_trade_no'],)).fetchone()
                if extra:
                    for key in required-set(result):result[key]=extra[key]
    result['merchant_order_no']=result.get('merchant_order_no') or result['out_trade_no']
    result["payment_payload"] = json.loads(result.get("payment_payload") or "{}")
    result["server_now"]=int(time.time())
    result["progress_frozen_at"]=result.get("crypto_seen_at") or result.get("paid_at")
    if not result["payment_payload"] and result.get("pay_url"):
        result["payment_payload"] = {"kind": "url", "url": result["pay_url"]}
    result["refundable_amount"] = format(max(Decimal("0"), Decimal(result["amount"]) - Decimal(result.get("refunded_amount", "0"))), ".2f") if result.get("paid_at") else "0.00"
    if result["status"] == "CREATED" and result.get("expires_at", 0) <= int(time.time()): result["status"] = "EXPIRED"
    result['order_amount']=two(result['amount'])
    result['order_currency']='CNY'
    result['payment_amount']=two(result['crypto_amount']) if result['channel']=='crypto' else two(result['amount'])
    result['payment_currency']=result['crypto_asset'] if result['channel']=='crypto' else 'CNY'
    result['payment_method']=method_label(result['channel'],result.get('crypto_network',''))
    fee=int(result.get('fee_cents',0));reversed_fee=int(result.get('fee_reversed_cents',0))
    result['fee_cny']=two(Decimal(fee-reversed_fee)/100)
    result['net_income_cny']=two(Decimal(result['amount'])-Decimal(result.get('refunded_amount','0.00'))-Decimal(result['fee_cny'])) if result.get('paid_at') else None
    hidden={'crypto_rate','crypto_rate_updated_at','crypto_rate_source','crypto_quoted_at'}
    if not admin:hidden|={'fixed_rate_snapshot','market_rate_snapshot','rate_snapshot_at','merchant_id','merchant_email','merchant_server_ip'}
    for key in hidden:result.pop(key,None)
    result['payment_payload']={k:v for k,v in result['payment_payload'].items() if k not in {'rate_cny','rate_updated_at','rate_source','rate_observed_at','fixed_rate_snapshot','market_rate_snapshot','rate_snapshot_at','scan_from','identity'}}
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1): raise ValueError('unsupported_database_schema')
            if version==0:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                    raise ValueError('fresh_database_required')
                schema=Path(__file__).with_name('schema.sql').read_text()
                db.executescript('BEGIN IMMEDIATE;\n'+schema+'\nPRAGMA user_version=1; COMMIT;')
        from .groups import MerchantGroups
        self.groups=MerchantGroups(self)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def bootstrap(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone(): return
            db.execute("INSERT INTO users(email,password,role,created_at,group_id,must_change_credentials) VALUES (?,?, 'admin',?,2,1)",
                       ('admin@pay.com',generate_password_hash('Admin123'),int(time.time())))

    def user_by_email(self, email):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            return dict(row) if row else None

    def register(self, email, password, *, manual=False, inviter=None):
        encoded=generate_password_hash(password)
        from .platform_rules import PlatformRules,cents
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rules=PlatformRules(self).read(db)
            cur=db.execute("INSERT INTO users(email,password,role,group_id,created_at) VALUES (?,?,'user',1,?)",
                           (email.strip().lower(),encoded,int(time.time())))
            identity=cur.lastrowid
            db.execute('INSERT INTO merchant_access(user_id,review,review_required,fee_due,invite_code,inviter) VALUES (?,?,?,?,?,?)',
                       (identity,'approved' if manual else 'pending',int(not manual),
                        0 if manual or not rules['registration_paid'] else cents(rules['registration_fee']),
                        secrets.token_urlsafe(9),inviter))
            return {'id':identity,'email':email.strip().lower(),'role':'user'}

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
                row = db.execute("SELECT u.id,u.email,u.role,u.must_change_credentials FROM api_keys k JOIN users u ON u.id=k.user_id WHERE k.hash=? AND u.disabled=0", (digest(token),)).fetchone()
            else:
                row = db.execute("SELECT u.id,u.email,u.role,u.must_change_credentials,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>? AND u.disabled=0", (digest(token), int(time.time()))).fetchone()
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

    def short_link(self, number):
        """One stable token per order: a short URL keeps the mobile-checkout QR small enough to scan."""
        with self.connect() as db:
            for _ in range(5):
                row = db.execute("SELECT token FROM short_links WHERE out_trade_no=?", (number,)).fetchone()
                if row: return row["token"]
                try:
                    token = secrets.token_urlsafe(9)
                    db.execute("INSERT INTO short_links(token,out_trade_no,created_at) VALUES (?,?,?)", (token, number, int(time.time())))
                    return token
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError("short_link_unavailable")

    def order_for_short_link(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12}", token): return None
        with self.connect() as db:
            row = db.execute("SELECT o.* FROM short_links s JOIN orders o USING(out_trade_no) WHERE s.token=?", (token,)).fetchone()
            return dict(row) if row else None

    def capture_payer_ip(self, number, address):
        import ipaddress
        address=str(ipaddress.ip_address(address))
        with self.connect() as db:
            db.execute("UPDATE orders SET client_ip=? WHERE out_trade_no=? AND client_ip=''",(address,number))

    def create_order(self, user_id, payload, pay_url, cfg, return_token=None, payment_payload=None, request_hash=None, client_ip=""):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO orders(out_trade_no,user_id,subject,amount,product,channel,pay_url,app_id,seller_id,created_at,notify_url,channel_version,client_ip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (payload["out_trade_no"], user_id, payload["subject"], payload["amount"], payload["product"], payload.get("channel", "alipay"), pay_url, cfg["app_id"], cfg["seller_id"], int(time.time()), payload.get("notify_url", ""), cfg.get("channel_version"), client_ip))
            db.execute("UPDATE orders SET expires_at=?,payment_payload=?,request_hash=? WHERE out_trade_no=?", (cfg.get("expires_at", int(time.time()) + 900), json.dumps(payment_payload or {}), request_hash, payload["out_trade_no"]))
            db.execute("UPDATE orders SET provider_notify_url=?,provider_return_url=? WHERE out_trade_no=?", (cfg.get("notify_url", ""), cfg.get("return_url", ""), payload["out_trade_no"]))
            db.execute("UPDATE orders SET payment_config_id=? WHERE out_trade_no=?", (cfg.get("payment_config_id"), payload["out_trade_no"]))
            db.execute("UPDATE orders SET merchant_server_ip=? WHERE out_trade_no=?", (cfg.get("merchant_server_ip", ""), payload["out_trade_no"]))
            db.execute("INSERT INTO payment_checks(out_trade_no,next_check) VALUES (?,?)", (payload["out_trade_no"], int(time.time()) + self.setting("poll_interval_seconds", 10)))
            if hasattr(self,'operations'):self.operations.snapshot(db,user_id,payload['out_trade_no'],payload['amount'],cfg.get('merchant_fee_rate'))
            db.execute('DELETE FROM merchant_quota_reservations WHERE number=?',(payload['out_trade_no'],))
            if return_token:
                db.execute("INSERT INTO payment_returns VALUES (?,?,?)", (digest(return_token), payload["out_trade_no"], int(time.time()) + 86400))
        return self.order(payload["out_trade_no"])

    def create_crypto_order(self,user_id,payload,cfg,payment,request_hash=None,client_ip=""):
        number=payload["out_trade_no"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            from .crypto import CryptoError
            from .payment_terms import address_key,overlaps
            active=db.execute("SELECT crypto_address,crypto_amount,match_mode FROM orders WHERE channel='crypto' AND crypto_network=? AND crypto_asset=? AND status IN ('CREATED','CONFIRMING') AND (status='CONFIRMING' OR expires_at+json_extract(payment_payload,'$.lookback_hours')*3600>?)",(payment['network'],payment['asset'],int(time.time()))).fetchall()
            if any(address_key(payment['network'],row['crypto_address'])==address_key(payment['network'],payment['address']) and overlaps(payment['crypto_amount'],payment['match_mode'],row['crypto_amount'],row['match_mode']) for row in active):raise CryptoError('crypto_wallet_amount_conflict')
            db.execute("INSERT INTO orders(out_trade_no,user_id,subject,amount,product,channel,pay_url,app_id,seller_id,created_at,notify_url,channel_version,client_ip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (number,user_id,payload["subject"],payload["amount"],"page","crypto",payment["address"],payment["asset_code"],payment["network"],int(time.time()),payload.get("notify_url",""),cfg.get("channel_version"),client_ip))
            db.execute("UPDATE orders SET expires_at=?,payment_payload=?,request_hash=?,provider_notify_url=?,payment_config_id=?,cashier_qr=?,crypto_network=?,crypto_asset=?,crypto_amount=?,crypto_address=?,crypto_rate=?,crypto_rate_updated_at=?,crypto_rate_source=?,crypto_quoted_at=? WHERE out_trade_no=?",
                       (payment["expires_at"],json.dumps(payment),request_hash,payload.get("notify_url",""),cfg.get("payment_config_id"),payment["address"],payment["network"],payment["asset"],payment["crypto_amount"],payment["address"],payment["rate_cny"],payment["rate_updated_at"],payment["rate_source"],payment["rate_observed_at"],number))
            db.execute('UPDATE orders SET fixed_rate_snapshot=?,market_rate_snapshot=?,rate_snapshot_at=?,match_mode=? WHERE out_trade_no=?',(payment['fixed_rate_snapshot'],payment['market_rate_snapshot'],payment['rate_snapshot_at'],payment['match_mode'],number))
            db.execute("UPDATE orders SET merchant_server_ip=? WHERE out_trade_no=?", (cfg.get("merchant_server_ip", ""), number))
            db.execute("INSERT INTO payment_checks(out_trade_no,next_check) VALUES (?,?)",(number,int(time.time())+self.setting("poll_interval_seconds",10)))
            if hasattr(self,"operations"):
                self.operations.snapshot(db,user_id,number,payload["amount"],cfg.get("merchant_fee_rate"))
                db.execute("DELETE FROM merchant_quota_reservations WHERE number=?",(number,))
        return self.order(number)

    def apply_crypto_observation(self, observation, minimum_confirmations):
        """Record an exactly matched transfer; confirmation is handled by the scanner."""
        now=int(time.time());number=observation["out_trade_no"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order=db.execute("SELECT * FROM orders WHERE out_trade_no=?",(number,)).fetchone()
            if not order or order["channel"]!="crypto" or order["status"] not in {"CREATED","CONFIRMING"} or order["paid_at"]:return False
            if order['status']=='CONFIRMING' and order['crypto_txid'] and order['crypto_txid']!=observation['txid']:return False
            log_index=str(observation.get("log_index",0))
            prior=db.execute("SELECT * FROM crypto_observations WHERE network=? AND txid=? AND log_index=?",(observation["network"],observation["txid"],log_index)).fetchone()
            if prior and (prior["out_trade_no"]!=number or prior["block_hash"]==observation["block_hash"]):return False
            if order["crypto_network"]!=observation["network"] or order["crypto_asset"]!=observation["asset"]:return False
            expected_address=order["crypto_address"]
            actual_address=observation["address"]
            if order["crypto_network"] in {"ETH","BSC","ARBITRUM","POLYGON","PLASMA","AVAXC"}:
                expected_address,actual_address=expected_address.lower(),actual_address.lower()
            if expected_address!=actual_address:return False
            if int(observation["timestamp"])<order["created_at"] or int(observation["timestamp"])>order["expires_at"]:return False
            if not matches(observation["amount"],order["crypto_amount"],order["match_mode"]):return False
            if prior:
                db.execute("UPDATE crypto_observations SET out_trade_no=?,network=?,asset=?,address=?,amount=?,block_height=?,block_hash=?,seen_at=? WHERE network=? AND txid=? AND log_index=?",(number,observation["network"],observation["asset"],observation["address"],observation["amount"],int(observation["block_height"]),observation["block_hash"],now,observation["network"],observation["txid"],log_index))
            else:
                db.execute("INSERT INTO crypto_observations(txid,log_index,out_trade_no,network,asset,address,amount,block_height,block_hash,seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(observation["txid"],log_index,number,observation["network"],observation["asset"],observation["address"],observation["amount"],int(observation["block_height"]),observation["block_hash"],now))
            db.execute("UPDATE orders SET status='CONFIRMING',crypto_seen_at=CAST(strftime('%s','now') AS INTEGER),crypto_txid=?,crypto_block=?,from_address=?,crypto_confirmations=1,provider_status=? WHERE out_trade_no=? AND status IN ('CREATED','CONFIRMING')",(observation["txid"],int(observation["block_height"]),observation.get("from_address",""),f"1/{minimum_confirmations} confirmations",number))
            db.execute("INSERT INTO events(out_trade_no,accepted,reason,created_at,stage,details,user_id) VALUES (?,1,'crypto_transfer_seen',?,'crypto.confirm',?,?)",(number,now,json.dumps({"txid":observation["txid"],"block":observation["block_height"],"amount":observation["amount"]}),order["user_id"]))
        return True

    def confirm_crypto_payment(self,number,txid,block_height,confirmations):
        now=int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM orders WHERE out_trade_no=?",(number,)).fetchone()
            if not row or row["channel"]!="crypto" or row["status"]!="CONFIRMING" or row["paid_at"]:return False
            if row["crypto_txid"]!=txid or row["crypto_block"]!=block_height:return False
            if confirmations<int(json.loads(row["payment_payload"]).get("confirmations",1)):return False
            observation=db.execute("SELECT log_index,block_hash FROM crypto_observations WHERE network=? AND txid=? AND out_trade_no=?",(row["crypto_network"],txid,number)).fetchone()
            if not observation:return False
            trade_number=f"{row['crypto_network']}:{txid}:{observation['log_index'] if observation else '0'}"
            db.execute("UPDATE orders SET status='PAID',trade_no=?,paid_at=?,crypto_confirmations=?,provider_status=? WHERE out_trade_no=? AND status='CONFIRMING'",(trade_number,now,int(confirmations),f"{confirmations} confirmations",number))
            self.operations.paid(db,dict(row))
            if row["notify_url"]:
                event_id=str(uuid.uuid4())
                payload=json.dumps({"id":event_id,"event":"payment.paid","out_trade_no":number,"trade_no":txid,"amount":row["amount"],"currency":"CNY","channel":"crypto","crypto_network":row["crypto_network"],"crypto_asset":row["crypto_asset"],"crypto_amount":row["crypto_amount"],"status":"PAID","paid_at":now},separators=(",",":"))
                db.execute("INSERT OR IGNORE INTO outbox(id,out_trade_no,user_id,url,payload,next_attempt) VALUES (?,?,?,?,?,?)",(event_id,number,row["user_id"],row["notify_url"],payload,now))
            db.execute("INSERT INTO events(out_trade_no,accepted,reason,created_at,stage,details,user_id) VALUES (?,1,'crypto_confirmed',?,'crypto.confirm',?,?)",(number,now,json.dumps({"txid":txid,"block":block_height,"confirmations":confirmations}),row["user_id"]))
        return True

    def rollback_crypto_observation(self,number,txid,log_index=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order=db.execute("SELECT status,crypto_txid,crypto_network FROM orders WHERE out_trade_no=?",(number,)).fetchone()
            if order and order["status"]=="CONFIRMING" and order["crypto_txid"]==txid:
                db.execute("UPDATE orders SET status='CREATED',crypto_txid='',crypto_block=0,from_address='',crypto_confirmations=0,crypto_seen_at=0,provider_status='reorg_detected' WHERE out_trade_no=?",(number,))
                if log_index is not None:db.execute("DELETE FROM crypto_observations WHERE network=? AND txid=? AND log_index=? AND out_trade_no=?",(order["crypto_network"],txid,str(log_index),number))
                else:db.execute("DELETE FROM crypto_observations WHERE network=? AND txid=? AND out_trade_no=?",(order["crypto_network"],txid,number))
                db.execute("INSERT INTO events(out_trade_no,accepted,reason,created_at,stage,details,user_id) SELECT ?,0,'crypto_reorg',?,'crypto.confirm',?,user_id FROM orders WHERE out_trade_no=?",(number,int(time.time()),json.dumps({"txid":txid}),number))

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

    def claim_qr(self, number, seconds=25):
        """One caller at a time asks Alipay for an order's website-checkout QR; the others wait for that answer.

        It uses the checkout job row of the order: an Alipay website order never needs that row for anything else.
        """
        now, lease = int(time.time()), secrets.token_hex(16)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO checkout_jobs(out_trade_no) VALUES (?)", (number,))
            if db.execute("SELECT locked_until FROM checkout_jobs WHERE out_trade_no=?", (number,)).fetchone()[0] > now: return None
            db.execute("UPDATE checkout_jobs SET locked_until=?,lease=? WHERE out_trade_no=?", (now + seconds, lease, number))
        return lease

    def latest_page_order(self, channel="alipay"):
        """When the last website order of a channel was made (0 if never): what decides whether connections are kept warm."""
        with self.connect() as db:
            row = db.execute("SELECT created_at FROM orders WHERE channel=? AND product='page' ORDER BY rowid DESC LIMIT 1", (channel,)).fetchone()
        return row[0] if row else 0

    def qr_busy(self, number):
        with self.connect() as db:
            row = db.execute("SELECT locked_until FROM checkout_jobs WHERE out_trade_no=?", (number,)).fetchone()
        return bool(row) and row[0] > int(time.time())

    def release_qr(self, number, lease):
        with self.connect() as db:
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
            row = db.execute(f"SELECT c.* FROM payment_checks c JOIN orders o USING(out_trade_no) WHERE o.status='CREATED' AND o.channel IN ('alipay','wechat') AND c.locked_until<=? AND {where} ORDER BY c.next_check LIMIT 1", [now, *args]).fetchone()
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

    def set_provider_status(self, number, text):
        """What the provider last said, for display; an order that is already paid keeps showing that, so a late answer cannot make it look unpaid."""
        with self.connect() as db:
            db.execute("UPDATE orders SET provider_status=? WHERE out_trade_no=? AND (paid_at IS NULL OR ? IN ('SUCCESS','REFUND','TRADE_SUCCESS','TRADE_FINISHED'))", (text, number, text))

    def close_unpaid(self, number, provider_state):
        """The provider closed an order that was never paid; a paid order is never downgraded."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order = db.execute("SELECT status,paid_at,user_id FROM orders WHERE out_trade_no=?", (number,)).fetchone()
            if not order or order["paid_at"]: return
            db.execute("UPDATE orders SET provider_status=?,status=CASE WHEN status='CREATED' THEN 'CLOSED' ELSE status END WHERE out_trade_no=?", (provider_state, number))
            db.execute("INSERT INTO events(out_trade_no,accepted,reason,created_at,stage,user_id) VALUES (?,1,?,?,'order.updated',?)", (number, "TRADE_CLOSED", int(time.time()), order["user_id"]))

    def apply_notification(self, params, app_id, seller_id, provider_state=None):
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
            db.execute("UPDATE orders SET provider_status=? WHERE out_trade_no=?", (provider_state or status, number))
            if status in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
                new_status = order["status"] if order["status"] in {"REFUNDING", "PARTIALLY_REFUNDED", "REFUNDED"} else "PAID"
                db.execute("UPDATE orders SET status=?,trade_no=?,paid_at=COALESCE(paid_at,?) WHERE out_trade_no=?", (new_status,params["trade_no"], int(time.time()), number))
                if not order['paid_at'] and hasattr(self,'operations'):self.operations.paid(db,order)
                if not order["paid_at"] and order["notify_url"]:
                    event_id = str(uuid.uuid4())
                    payload = json.dumps({"id": event_id, "event": "payment.paid", "out_trade_no": number,
                                          "trade_no": params["trade_no"], "amount": order["amount"], "currency": "CNY",
                                          "channel": order["channel"], "status": "PAID", "paid_at": int(time.time())}, separators=(",", ":"))
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
        # 固定间隔重试，最多 8 次，间隔来自“失败重试间隔”设置
        delays = [self.policy()["callback_retry_interval_seconds"]] * 8
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
