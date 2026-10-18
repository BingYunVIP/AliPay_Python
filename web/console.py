"""Authenticated operational views. Every metric is derived from the order ledger."""
import csv
import io
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import Response, g, jsonify, request

from bypay.payment_validation import money
from .platform_rules import amount, cents
from .store import present_order

# 网站域名：允许带 http/https、端口和路径，最终只保留主机名
SITE_HOST = re.compile(r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,63}")


def site_host(value):
    if not isinstance(value, str): raise ValueError("invalid_domain")
    text = value.strip()
    if not text: return ""
    parsed = urlsplit(text if "://" in text else "http://" + text)
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host or len(host) > 253 or not SITE_HOST.fullmatch(host): raise ValueError("invalid_domain")
    return host

ZONE = ZoneInfo("Asia/Shanghai")
# The first part of an event's stage names its category in the event log
EVENT_CATEGORIES = {"notify", "merchant", "create", "query", "console", "sdk", "refund", "order", "return", "checkout", "settings", "settlement", "mail", "task", "domain", "risk", "request"}


def install_console(app, store, auth, trace, cfg, ready, ApiError):
    def page_args():
        try:
            page = max(1, int(request.args.get("page", 1)))
            size = int(request.args.get("page_size", 20))
            if size not in {10, 20, 50, 100} or page > 100000:
                raise ValueError()
            return page, size
        except ValueError:
            raise ApiError("invalid_pagination")

    def scope(column="user_id"):
        return ("1=1", []) if g.user["role"] == "admin" else (f"{column}=?", [g.user["id"]])

    def period_start(period):
        now = datetime.now(ZONE)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "all": return 0
        if period not in {"today", "7d", "30d"}: raise ApiError("invalid_period")
        return int((start - timedelta(days={"today": 0, "7d": 6, "30d": 29}[period])).timestamp())

    def order_filter(selection=False):
        where, args = scope("o.user_id")
        where += " AND o.deleted_at IS " + ("NOT NULL" if request.args.get("deleted") == "1" else "NULL")
        # An explicit selection only belongs to its owner and list scope; later searches or filters never narrow it.
        if selection: return where, args
        channel=request.args.get("channel","all")
        if channel != "all":
            if channel not in {"alipay","wechat","paypal","stripe"}: raise ApiError("invalid_channel_config")
            where += " AND o.channel=?"; args.append(channel)
        product=request.args.get('product','all')
        if product!='all':
            if product not in {'page','wap','app','mini'}:raise ApiError('unsupported_product')
            where+=' AND o.product=?';args.append(product)
        config_id=request.args.get("config_id","")
        if config_id:
            if not config_id.isdigit(): raise ApiError("invalid_channel_config")
            where += " AND o.payment_config_id=?"; args.append(int(config_id))
        merchant = request.args.get("merchant", "").strip()[:254]
        if merchant:
            # 用户表点“累计收款/成功率”跳过来时按商户 ID 或邮箱筛选
            if merchant.isdigit():
                where += " AND m.pid=?"; args.append(int(merchant))
            else:
                where += " AND lower(u.email)=?"; args.append(merchant.lower())
        search = request.args.get("search", "").strip()[:128]
        if search:
            term = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            # 订单列表里看得到的列都能搜：订单号、商品、渠道交易号、商户 ID/邮箱、渠道配置、状态、用户 IP
            where += (" AND (o.out_trade_no LIKE ? ESCAPE '\\' OR o.subject LIKE ? ESCAPE '\\'"
                      " OR COALESCE(o.trade_no,'') LIKE ? ESCAPE '\\' OR COALESCE(o.client_ip,'') LIKE ? ESCAPE '\\'"
                      " OR o.status LIKE ? ESCAPE '\\' OR COALESCE(o.provider_status,'') LIKE ? ESCAPE '\\'"
                      " OR COALESCE(u.email,'') LIKE ? ESCAPE '\\' OR CAST(COALESCE(m.pid,'') AS TEXT) LIKE ? ESCAPE '\\'"
                      " OR COALESCE(c.name,'') LIKE ? ESCAPE '\\')")
            args += [term] * 9
        status = request.args.get("status", "all")
        if status != "all":
            if status not in {"CREATED", "PAID", "CLOSED", "EXPIRED", "REFUNDING", "PARTIALLY_REFUNDED", "REFUNDED"}: raise ApiError("invalid_status")
            where += " AND (CASE WHEN o.status='CREATED' AND o.expires_at<=CAST(strftime('%s','now') AS INTEGER) THEN 'EXPIRED' ELSE o.status END)=?"; args.append(status)
        period = request.args.get("period", "all")
        where += " AND o.created_at>=?"; args.append(period_start(period))
        return where, args

    def cents_sum(db, where, args):
        return db.execute(f"SELECT COALESCE(SUM(CAST(ROUND(CAST(amount AS NUMERIC)*100) AS INTEGER)),0) FROM orders WHERE {where}", args).fetchone()[0]

    @app.get("/api/console/overview")
    @auth(session_only=True)
    def overview():
        period = request.args.get("period", "7d")
        if period == "all": raise ApiError("invalid_period")
        since = period_start(period)
        where, args = scope()
        with store.connect() as db:
            counts = {row["status"]: row["total"] for row in db.execute(f"SELECT status,COUNT(*) total FROM orders WHERE {where} AND created_at>=? GROUP BY status", [*args, since])}
            revenue = cents_sum(db, f"{where} AND paid_at>=?", [*args, since])
            paid_count = db.execute(f"SELECT COUNT(*) FROM orders WHERE {where} AND paid_at>=?", [*args, since]).fetchone()[0]
            totals = db.execute(f"SELECT status,COUNT(*) total FROM outbox WHERE {where} GROUP BY status", args).fetchall()
            callbacks = {row["status"]: row["total"] for row in totals}
            all_revenue = cents_sum(db, f"{where} AND paid_at IS NOT NULL", args)
            paid_rows = db.execute(f"SELECT amount,paid_at FROM orders WHERE {where} AND paid_at>=? ORDER BY paid_at", [*args, since]).fetchall()
            distribution = {row['channel']:row['total'] for row in db.execute(f"SELECT channel,SUM(CAST(ROUND(CAST(amount AS NUMERIC)*100) AS INTEGER)) total FROM orders WHERE {where} AND paid_at>=? GROUP BY channel", [*args,since])}
            recent = db.execute(f"SELECT * FROM orders WHERE {where} AND deleted_at IS NULL ORDER BY created_at DESC,out_trade_no DESC LIMIT 5", args).fetchall()
            users = db.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0] if g.user["role"] == "admin" else None
            heartbeat = db.execute("SELECT last_seen FROM service_health WHERE name='callbacks'").fetchone()
        start = datetime.fromtimestamp(since, ZONE)
        step = timedelta(hours=4) if period == "today" else timedelta(days=1)
        size = 6 if period == "today" else 7 if period == "7d" else 30
        bars = [{"timestamp": int((start + step * i).timestamp()), "amount_cents": 0, "count": 0} for i in range(size)]
        for row in paid_rows:
            index = int((row["paid_at"] - since) // step.total_seconds())
            if 0 <= index < size:
                bars[index]["amount_cents"] += int(Decimal(row["amount"]) * 100)
                bars[index]["count"] += 1
        total = sum(counts.values())
        return jsonify(period=period, timezone="Asia/Shanghai", updated_at=int(time.time()), revenue_cents=revenue,
                       lifetime_revenue_cents=all_revenue, paid_count=paid_count, order_count=total,
                       paid_ratio=round(sum(counts.get(k,0) for k in ("PAID","REFUNDING","PARTIALLY_REFUNDED","REFUNDED")) / total * 100, 1) if total else None,
                       pending_count=counts.get("CREATED", 0), callbacks=callbacks, users=users, trend=bars,
                       recent_orders=[present_order(row) for row in recent],
                       channel_enabled=store.setting("alipay_enabled", True), channel_ready=ready(),
                       distribution=distribution, config_count=len(app.extensions["channels"].all()),
                       enabled_config_count=sum(ch["enabled"] and ch["configured"] and ch["integrated"] for ch in app.extensions["channels"].all()),
                       worker_last_seen=heartbeat["last_seen"] if heartbeat else None)

    @app.get("/api/console/orders")
    @auth(session_only=True)
    def orders():
        where, args = order_filter()
        page, size = page_args()
        with store.connect() as db:
            total = db.execute(f"""SELECT COUNT(*) FROM orders o LEFT JOIN users u ON u.id=o.user_id
                LEFT JOIN merchant_accounts m ON m.user_id=o.user_id LEFT JOIN channel_configs c ON c.id=o.payment_config_id
                WHERE {where}""", args).fetchone()[0]
            rows = db.execute(f"""SELECT o.*,c.name config_name,u.email merchant_email,m.pid merchant_id,
                COALESCE(f.fee,0) fee_cents FROM orders o
                LEFT JOIN channel_configs c ON c.id=o.payment_config_id
                LEFT JOIN users u ON u.id=o.user_id
                LEFT JOIN merchant_accounts m ON m.user_id=o.user_id
                LEFT JOIN financial_orders f ON f.number=o.out_trade_no
                WHERE {where} ORDER BY o.created_at DESC,o.out_trade_no DESC LIMIT ? OFFSET ?""", [*args, size, (page - 1) * size]).fetchall()
        return jsonify(orders=[present_order(row) for row in rows], total=total, page=page, page_size=size)

    @app.get("/api/console/orders/ids")
    @auth(session_only=True)
    def order_ids():
        where, args = order_filter()
        with store.connect() as db:
            rows = db.execute(f"""SELECT o.out_trade_no FROM orders o LEFT JOIN users u ON u.id=o.user_id
                LEFT JOIN merchant_accounts m ON m.user_id=o.user_id LEFT JOIN channel_configs c ON c.id=o.payment_config_id
                WHERE {where} ORDER BY o.created_at DESC,o.out_trade_no DESC LIMIT 1001""", args).fetchall()
        return jsonify(ids=[row[0] for row in rows[:1000]], limited=len(rows) > 1000)

    def selected_filter(payload):
        if not isinstance(payload,dict):raise ApiError('invalid_json')
        ids=payload.get('ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(x,str) or len(x)>64 for x in ids):raise ApiError('invalid_selection')
        where,args=order_filter(selection=True)
        ids=list(dict.fromkeys(ids))
        return where+' AND o.out_trade_no IN ('+','.join('?'*len(ids))+')',args+ids

    @app.post('/api/console/orders/batch')
    @auth(session_only=True)
    def batch_orders():
        payload=request.get_json(silent=True);where,args=selected_filter(payload)
        action=payload.get('action')
        if action not in {'delete','restore'}:raise ApiError('invalid_action')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute(f'SELECT o.out_trade_no,o.status FROM orders o WHERE {where} LIMIT 1001',args).fetchall()
            if len(rows)>1000:raise ApiError('batch_limit_exceeded',422)
            blocked=[row['out_trade_no'] for row in rows if row['status']=='REFUNDING']
            changed=[row['out_trade_no'] for row in rows if row['status']!='REFUNDING']
            for number in changed:db.execute('UPDATE orders SET deleted_at=? WHERE out_trade_no=?',(int(time.time()) if action=='delete' else None,number))
        trace('order.batch','orders_'+action,{'changed':changed,'skipped':blocked},user_id=g.user['id'])
        return jsonify(changed=len(changed),skipped=blocked)

    @app.post('/api/console/orders/export')
    @app.get("/api/console/orders/export")
    @auth(session_only=True)
    def export_orders():
        where, args = selected_filter(request.get_json(silent=True)) if request.method=='POST' else order_filter()
        with store.connect() as db:
            if db.execute(f"""SELECT COUNT(*) FROM orders o LEFT JOIN users u ON u.id=o.user_id
                    LEFT JOIN merchant_accounts m ON m.user_id=o.user_id LEFT JOIN channel_configs c ON c.id=o.payment_config_id
                    WHERE {where}""", args).fetchone()[0] > 10000:
                raise ApiError("export_limit_exceeded", 422)
            rows = db.execute(f"""SELECT o.* FROM orders o LEFT JOIN users u ON u.id=o.user_id
                LEFT JOIN merchant_accounts m ON m.user_id=o.user_id LEFT JOIN channel_configs c ON c.id=o.payment_config_id
                WHERE {where} ORDER BY o.created_at DESC""", args).fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        columns = ["out_trade_no", "subject", "amount", "channel", "product", "status", "trade_no", "created_at", "paid_at"]
        writer.writerow(columns)
        for row in rows:
            values = [str(row[k] if row[k] is not None else "") for k in columns]
            # Excel 会把 12 位以上的纯数字变成科学计数法，前缀一个单引号让它按文本显示
            writer.writerow(["'" + v if v.lstrip().startswith(("=", "+", "-", "@")) or v.startswith(("\t", "\r", "\n")) or (v.isdigit() and len(v) >= 12) else v for v in values])
        trace("console.export", "orders_exported", {"count": len(rows)}, user_id=g.user["id"])
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": 'attachment; filename="pay-orders.csv"'})

    @app.get("/api/console/events")
    @auth(session_only=True)
    def console_events():
        where, args = scope()
        search = request.args.get("search", "").strip()[:128]
        if search:
            where += " AND instr(COALESCE(out_trade_no,'') || ' ' || stage || ' ' || reason,?)>0"; args.append(search)
        category = request.args.get("category", "all")
        if category != "all":
            if category not in EVENT_CATEGORIES: raise ApiError("invalid_category")
            where += " AND stage LIKE ?"; args.append(category + ".%")
        if request.args.get("status") == "rejected": where += " AND accepted=0"
        page, size = page_args()
        with store.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM events WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM events WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?", [*args, size, (page - 1) * size]).fetchall()
        return jsonify(events=[{**{k: v for k, v in dict(row).items() if k != "user_id"}, "details": json.loads(row["details"])} for row in rows], total=total, page=page, page_size=size)

    @app.get("/api/console/callbacks")
    @auth(session_only=True)
    def callbacks():
        where, args = scope()
        status = request.args.get("status", "all")
        if status != "all":
            if status not in {"pending", "failed", "delivered"}: raise ApiError("invalid_status")
            where += " AND status=?"; args.append(status)
        search = request.args.get("search", "").strip()[:128]
        if search: where += " AND instr(out_trade_no,?)>0"; args.append(search)
        page, size = page_args()
        with store.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM outbox WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT id,out_trade_no,url,payload,attempts,next_attempt,locked_until,status,last_error FROM outbox WHERE {where} ORDER BY next_attempt DESC LIMIT ? OFFSET ?", [*args, size, (page - 1) * size]).fetchall()
        return jsonify(callbacks=[{**dict(row), "payload": json.loads(row["payload"])} for row in rows], total=total, page=page, page_size=size)

    @app.post("/api/console/callbacks/<event_id>/retry")
    @auth(session_only=True)
    def retry_callback(event_id):
        now = int(time.time())
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM outbox WHERE id=?", (event_id,)).fetchone()
            if not row or (g.user["role"] != "admin" and row["user_id"] != g.user["id"]): raise ApiError("not_found", 404)
            if row["status"] == "delivered" or row["locked_until"] > now: raise ApiError("callback_not_retryable", 409)
            db.execute("UPDATE outbox SET status='pending',attempts=0,next_attempt=?,last_error='' WHERE id=?", (now, event_id))
        trace("console.callback", "callback_retry_scheduled", {"event_id": event_id}, row["out_trade_no"], g.user["id"])
        return jsonify(ok=True)

    @app.get("/api/admin/users")
    @auth(session_only=True, admin=True)
    def users():
        page, size = page_args()
        search = request.args.get("search", "").strip()[:128]
        where, args = ("instr(u.email,?)>0", [search]) if search else ("1=1", [])
        with store.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM users u WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"""SELECT u.id,u.email,u.role,u.disabled,u.created_at,k.suffix,m.pid merchant_id,m.mode signature_mode,a.review,a.fee_due,a.fee_paid,a.risk_hold,a.risk_reason,a.pay_enabled,
                (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id) order_count,
                (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id AND o.paid_at IS NOT NULL) paid_count,
                (SELECT COALESCE(SUM(CAST(ROUND(CAST(amount AS NUMERIC)*100) AS INTEGER)),0) FROM orders o WHERE o.user_id=u.id AND o.paid_at IS NOT NULL) revenue_cents,
                (SELECT COALESCE(-SUM(l.cents),0) FROM ledger_lines l WHERE l.user_id=u.id AND l.account='deposit') deposit_cents,
                (SELECT GROUP_CONCAT(d.host,'、') FROM merchant_domains d WHERE d.user_id=u.id) domains
                FROM users u LEFT JOIN api_keys k ON k.user_id=u.id LEFT JOIN merchant_accounts m ON m.user_id=u.id LEFT JOIN merchant_access a ON a.user_id=u.id WHERE {where} ORDER BY u.id LIMIT ? OFFSET ?""", [*args, size, (page - 1) * size]).fetchall()
        return jsonify(users=[dict(row) for row in rows], total=total, page=page, page_size=size)

    @app.post("/api/admin/users")
    @auth(session_only=True, admin=True)
    def add_user():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict): raise ApiError("invalid_json")
        email, password = payload.get("email", ""), payload.get("password", "")
        if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email): raise ApiError("invalid_email")
        if not isinstance(password, str) or not 12 <= len(password) <= 128: raise ApiError("password_too_short")
        operations = app.extensions["operations"]; rules = operations.rules.read()
        values = payload.get("domains", [])
        if not isinstance(values, list) or len(values) > 10 or any(not isinstance(v, str) for v in values): raise ApiError("invalid_domain")
        try: hosts = list(dict.fromkeys(host for host in (site_host(value) for value in values) if host))
        except ValueError: raise ApiError("invalid_domain")
        if len(hosts) > rules["merchant_domain_limit"]: raise ApiError("domain_limit_exceeded")
        whitelist, pay_enabled, disabled = payload.get("callback_whitelist", False), payload.get("pay_enabled", True), payload.get("disabled")
        if type(whitelist) is not bool or type(pay_enabled) is not bool or (disabled is not None and type(disabled) is not bool): raise ApiError("invalid_json")
        try: deposit = cents(str(payload.get("deposit", "0.00")).strip() or "0.00")
        except ValueError: raise ApiError("invalid_amount")
        # 保证金低于门槛时默认停用商户状态，管理员仍可显式开启
        if disabled is None: disabled = deposit < cents(rules["deposit_minimum"])
        try: user = store.register(email.strip().lower(), password)
        except sqlite3.IntegrityError: raise ApiError("email_unavailable", 409)
        app.extensions["merchants"].ensure(user["id"])
        operations.ensure(user["id"])
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE users SET disabled=? WHERE id=?", (int(disabled), user["id"]))
            db.execute("UPDATE merchant_access SET pay_enabled=? WHERE user_id=?", (int(pay_enabled), user["id"]))
            for host in hosts:
                db.execute("INSERT OR IGNORE INTO merchant_domains(user_id,host,status,created_at) VALUES (?,?,?,?)",
                           (user["id"], host, "approved" if whitelist else "pending", int(time.time())))
        if deposit: operations.credit_deposit(user["id"], deposit, "user:" + str(user["id"]))
        if whitelist and hosts:
            # 商户回调域名白名单是平台级的，勾选后立即同步到系统设置
            settings = app.extensions["site_settings"]
            settings.save({"callback_hosts": sorted(set(settings.read()["callback_hosts"]) | set(hosts))})
        trace("console.user", "user_created", {"target_user_id": user["id"], "domains": hosts, "callback_whitelist": whitelist,
              "disabled": disabled, "pay_enabled": pay_enabled, "deposit": amount(deposit)}, user_id=g.user["id"])
        return jsonify(user={**user, "disabled": disabled, "pay_enabled": pay_enabled, "domains": hosts, "deposit": amount(deposit)}), 201

    @app.post("/api/admin/users/<int:user_id>/access")
    @auth(session_only=True, admin=True)
    def user_access(user_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or type(payload.get("disabled")) is not bool: raise ApiError("invalid_json")
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
            if not user: raise ApiError("not_found", 404)
            if user["role"] == "admin": raise ApiError("admin_protected", 403)
            db.execute("UPDATE users SET disabled=? WHERE id=?", (int(payload["disabled"]), user_id))
            if payload["disabled"]:
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                db.execute("DELETE FROM api_keys WHERE user_id=?", (user_id,))
        trace("console.user", "user_disabled" if payload["disabled"] else "user_enabled", {"target_user_id": user_id}, user_id=g.user["id"])
        return jsonify(ok=True)

    @app.post("/api/admin/users/<int:user_id>/pay-access")
    @auth(session_only=True, admin=True)
    def user_pay_access(user_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or type(payload.get("pay_enabled")) is not bool: raise ApiError("invalid_json")
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
            if not user: raise ApiError("not_found", 404)
            if user["role"] == "admin": raise ApiError("admin_protected", 403)
            db.execute("UPDATE merchant_access SET pay_enabled=? WHERE user_id=?", (int(payload["pay_enabled"]), user_id))
        trace("console.user", "user_pay_enabled" if payload["pay_enabled"] else "user_pay_disabled", {"target_user_id": user_id}, user_id=g.user["id"])
        return jsonify(ok=True)

    @app.get("/api/admin/users/<int:user_id>/deposit")
    @auth(session_only=True, admin=True)
    def user_deposit(user_id):
        with store.connect() as db:
            user = db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
            if not user: raise ApiError("not_found", 404)
            balance = db.execute("SELECT COALESCE(-SUM(cents),0) FROM ledger_lines WHERE user_id=? AND account='deposit'", (user_id,)).fetchone()[0]
        rules = app.extensions["operations"].rules.read()
        return jsonify(deposit_cents=balance, deposit=amount(balance), deposit_minimum=rules["deposit_minimum"])

    @app.get("/api/admin/settings")
    @auth(session_only=True, admin=True)
    def settings():
        return jsonify(**app.extensions["site_settings"].read(),
                       amount_ceiling=money(app.config["TEST_MAX_AMOUNT"]), active_origin=app.config["PUBLIC_BASE_URL"],
                       app_id=cfg["app_id"], seller_id=cfg["seller_id"],
                       environment=cfg["env"], private_key_configured=bool(cfg["app_private_key"]), public_key_configured=bool(cfg["alipay_public_key"]))

    @app.put("/api/admin/settings")
    @auth(session_only=True, admin=True)
    def save_settings():
        payload = request.get_json(silent=True)
        result = app.extensions["site_settings"].save(payload)
        trace("console.settings", "settings_saved", payload, user_id=g.user["id"])
        return jsonify(ok=True, version=result["version"])
