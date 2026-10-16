import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime
from decimal import Decimal
from functools import wraps
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, has_request_context, jsonify, redirect, request, send_from_directory
from werkzeug.local import LocalProxy
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from bypay.alipay_client import AlipayClient, AlipayError
from bypay.wechat_client import WechatClient, WechatError, ready as wechat_ready
from bypay.constants import GATEWAY_BY_ENV
from bypay.payment_validation import money
from .store import Store, digest, present_order
from .trace import create_trace
from .webhooks import WebhookSigner, validate_callback, local_callback_origins
from .console import install_console
from .channels import ChannelError, ChannelStore, FIELDS, INTEGRATED, REQUIRED as REQUIRED_FIELDS
from .reconcile import Reconciler, yuan
from .settings import SiteSettings, SettingsError
from .refunds import Refunds, RefundError

ROOT = Path(__file__).resolve().parent.parent
EMPTY_PASSWORD_HASH = generate_password_hash("unavailable-account-placeholder")


class ApiError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def payment_config(config):
    def key(name):
        path = config.get(name + "_FILE", "")
        return Path(path).read_text().strip() if path else config.get(name, "").replace("\\n", "\n")
    env = config.get("ALIPAY_ENV", "sandbox")
    if env not in GATEWAY_BY_ENV:
        raise ValueError("ALIPAY_ENV must be sandbox or production")
    origin = config["PUBLIC_BASE_URL"].rstrip("/")
    parsed = urlsplit(origin)
    if parsed.path or parsed.query or parsed.fragment or parsed.username or not parsed.hostname:
        raise ValueError("PUBLIC_BASE_URL must be an origin without a path")
    local = parsed.hostname in {"localhost", "127.0.0.1", "[::1]", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and (local or config.get("BYPAY_ALLOW_HTTP") == "1")):
        raise ValueError("PUBLIC_BASE_URL requires HTTPS outside localhost")
    notify_url = config.get("ALIPAY_NOTIFY_URL")
    if notify_url is None:
        notify_url = "" if local or parsed.scheme != "https" else origin + "/alipay/notify"
    if notify_url:
        notification = urlsplit(notify_url)
        if notification.scheme != "https" or not notification.hostname or notification.hostname in {"localhost", "127.0.0.1", "::1"} or notification.username or notification.password or notification.query or notification.fragment or notification.path != "/alipay/notify":
            raise ValueError("ALIPAY_NOTIFY_URL 必须是指向本服务的公网 HTTPS 地址，路径为 /alipay/notify")
    return {"env": env, "app_id": config.get("ALIPAY_APP_ID", ""),
            "seller_id": config.get("ALIPAY_SELLER_ID", ""),
            "app_private_key": key("ALIPAY_PRIVATE_KEY"),
            "alipay_public_key": key("ALIPAY_PUBLIC_KEY"),
            "gateway": GATEWAY_BY_ENV[env], "sign_type": "RSA2", "charset": "utf-8",
            "notify_url": notify_url, "return_url": origin + "/alipay/return"}


def create_app(settings=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(PUBLIC_BASE_URL="https://pay.bingyun.vip", DATABASE=str(ROOT / "data/web.sqlite3"),
                      ALLOW_REGISTRATION="true", TEST_MAX_AMOUNT="3000.00", MAX_CONTENT_LENGTH=32768,
                      MAX_FORM_MEMORY_SIZE=32768, MAX_FORM_PARTS=64, FRONTEND_DIST=str(ROOT / "frontend/dist"))
    app.config.update({k: v for k, v in os.environ.items() if k.startswith(("ALIPAY_", "BYPAY_")) or k in {"PUBLIC_BASE_URL", "ALLOW_REGISTRATION", "TEST_MAX_AMOUNT", "DATABASE"}})
    app.config.update(settings or {})
    cfg = payment_config(app.config)
    store = Store(app.config["DATABASE"])
    base_cfg = cfg
    site = SiteSettings(store, app.config, base_cfg)
    channels = ChannelStore(store, base_cfg, site.urls)
    def current_config():
        if not has_request_context(): return channels.payment_config()
        if not hasattr(g, "payment_config"):
            g.payment_config = channels.payment_config()
        return g.payment_config
    cfg = LocalProxy(current_config)
    trace = create_trace(store, app.config.get("BYPAY_LOG_FILE", str(Path(app.config["DATABASE"]).parent / "logs/payment.jsonl")))
    signer = WebhookSigner(Path(app.config["DATABASE"]).parent / "webhook-signing.pem")
    callback_hosts = LocalProxy(lambda: set(site.read()["callback_hosts"]) | (app.extensions["operations"].callback_hosts() if "operations" in app.extensions else set()))
    callback_local = LocalProxy(lambda: local_callback_origins(site.read()["origin"], app.config.get("BYPAY_LOCAL_CALLBACK_PORT", 8100)))
    store.bootstrap(app.config.get("BYPAY_ADMIN_EMAIL"), app.config.get("BYPAY_ADMIN_PASSWORD"))
    app.extensions.update(store=store, alipay_config=base_cfg, channels=channels, site_settings=site, webhook_signer=signer, trace=trace, callback_hosts=callback_hosts, callback_local_origins=callback_local)
    from .merchants import Merchants
    merchants = Merchants(store, site)
    app.extensions["merchants"] = merchants
    from .mailing import Mailer,email_address
    from .operations import Operations
    from .antibot import AntiBot
    mailer=Mailer(store,merchants.cipher,trace)
    operations=Operations(store,mailer,trace)
    antibot=AntiBot(store,merchants.cipher)
    app.extensions.update(mailer=mailer,operations=operations,antibot=antibot)
    secure = app.config["PUBLIC_BASE_URL"].startswith("https://")
    cookie_name = "__Host-bypay_session" if secure else "bypay_session"
    origin = app.config["PUBLIC_BASE_URL"].rstrip("/")
    browser_origins = {origin}
    # Native local previews can be opened with either loopback hostname.
    # Keep production domains strict; never trust arbitrary Host/Origin values.
    parsed_origin = urlsplit(origin)
    if parsed_origin.scheme == "http" and parsed_origin.hostname in {"localhost", "127.0.0.1", "::1"}:
        port = f":{parsed_origin.port}" if parsed_origin.port else ""
        browser_origins.update(f"http://{host}{port}" for host in ("localhost", "127.0.0.1", "[::1]"))
    if app.config.get("BYPAY_RUN_MODE") == "local" and parsed_origin.scheme == "http" and parsed_origin.hostname in {"localhost", "127.0.0.1", "::1"}:
        browser_origins.update(value for value in app.config.get("BYPAY_BROWSER_ORIGINS", "").split(",") if value)
    if app.config.get("BYPAY_RUN_MODE") == "managed" and app.config.get("BYPAY_ALLOW_HTTP") == "1":
        browser_origins.update(value for value in app.config.get("BYPAY_BROWSER_ORIGINS", "").split(",") if value)
    if app.config.get("BYPAY_TRUST_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    def ready(config=None):
        active = config if config is not None else cfg
        return all(active.get(k) for k in ("app_id", "seller_id", "app_private_key", "alipay_public_key"))

    def client(config=None):
        active = config if config is not None else dict(cfg)
        if not ready(active):
            raise ApiError("payment_not_configured", 503)
        return app.config.get("ALIPAY_CLIENT_FACTORY", AlipayClient)(active)

    def wechat_client(config):
        if not wechat_ready(config):
            raise ApiError("payment_not_configured", 503)
        try:
            return app.config.get("WECHAT_CLIENT_FACTORY", WechatClient)(config)
        except WechatError as exc:
            raise ApiError(exc.code, 503) from None

    alipay_factory = lambda active: app.config.get("ALIPAY_CLIENT_FACTORY", AlipayClient)(active)
    wechat_factory = lambda active: app.config.get("WECHAT_CLIENT_FACTORY", WechatClient)(active)
    reconciler = Reconciler(store, channels, trace, alipay_factory, wechat_factory)
    app.extensions["reconciler"] = reconciler
    refunds = Refunds(store, channels, trace, alipay_factory, wechat_factory)
    app.extensions["refunds"] = refunds

    def limit(key, count=30, window=60):
        if not store.rate_limit(key, count, window):
            raise ApiError("rate_limited", 429)

    def json_body():
        if not request.is_json:
            raise ApiError("json_required", 415)
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ApiError("invalid_json")
        return value

    def same_origin():
        if request.headers.get("Origin") and request.headers["Origin"] not in browser_origins | {site.read()["origin"]}:
            raise ApiError("invalid_origin", 403)

    def auth(session_only=False, admin=False):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                bearer = request.headers.get("Authorization", "")
                is_api = bearer.startswith("Bearer ")
                token = bearer[7:] if is_api else request.cookies.get(cookie_name, "")
                g.user = store.authenticate(token, api=is_api) if token else None
                if not g.user or (session_only and is_api):
                    raise ApiError("unauthorized", 401)
                if admin and g.user["role"] != "admin":
                    raise ApiError("forbidden", 403)
                if request.method != "GET":
                    if not is_api:
                        same_origin()
                        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", "").encode(), g.user["csrf"].encode()):
                            raise ApiError("invalid_csrf", 403)
                    limit("user:" + str(g.user["id"]))
                return fn(*args, **kwargs)
            return wrapped
        return decorator

    def user_info(user):
        return {k: user[k] for k in ("id", "email", "role")}

    def public_order(order):
        return present_order(order)

    def owned_order(number, include_deleted=False):
        order = store.order(number)
        if not order or (order.get("deleted_at") and not include_deleted) or (order["user_id"] != g.user["id"] and g.user["role"] != "admin"):
            raise ApiError("order_not_found", 404)
        g.order_number = number
        return order

    @app.before_request
    def limit_body_size():
        maximum=262144 if request.path in {'/api/admin/mail/template','/api/admin/mail/preview','/api/admin/mail/test'} else app.config['MAX_CONTENT_LENGTH']
        request.max_content_length=maximum
        if request.content_length and request.content_length > maximum:
            raise RequestEntityTooLarge()

    @app.after_request
    def headers(response):
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                                 "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                                 "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src https://openapi.alipay.com https://unitradeprod.alipay.com https://excashier.alipay.com https://openapi-sandbox.dl.alipaydev.com https://excashier-sandbox.dl.alipaydev.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        # A SPA may navigate to login after settings change without loading a new document
        csp=response.headers['Content-Security-Policy']
        csp=csp.replace("script-src 'self'", "script-src 'self' https://challenges.cloudflare.com").replace("frame-src ","frame-src https://challenges.cloudflare.com ").replace("connect-src 'self'", "connect-src 'self' https://challenges.cloudflare.com")
        response.headers['Content-Security-Policy']=csp
        if getattr(g, 'cashier_parent', None):
            response.headers.pop('X-Frame-Options', None)
            response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace("frame-ancestors 'none'", "frame-ancestors 'self' " + g.cashier_parent)
            response.headers['Cache-Control'] = 'no-store'
        if request.path.startswith(("/api/", "/alipay/", "/wechat/", "/m/")) or request.path.endswith('.php'):
            response.headers["Cache-Control"] = "no-store"
        if secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(ApiError)
    def api_error(exc):
        if request.path.startswith("/api/v1/payments") and request.method == "POST" and getattr(g, "user", None):
            trace("request.rejected", exc.code, number=getattr(g, "order_number", None), user_id=g.user["id"], accepted=False)
        response = jsonify(error=exc.code, message=operations.rules.read()['blocked_message'] if exc.code=='product_blocked' else None)
        if exc.status == 429:
            response.headers["Retry-After"] = "60"
        return response, exc.status

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error="request_rejected"), exc.code

    @app.errorhandler(ChannelError)
    def channel_error(exc):
        status = 409 if str(exc) == "channel_config_conflict" else 503 if str(exc) == "channel_config_locked" else 400
        return jsonify(error=str(exc), field=exc.field, reason=exc.reason), status

    @app.errorhandler(SettingsError)
    def settings_error(exc):
        return jsonify(error=str(exc), field=exc.field), 409 if str(exc) == "settings_conflict" else 400

    @app.errorhandler(RefundError)
    def refund_error(exc):
        return jsonify(error=str(exc)), 409

    @app.errorhandler(WechatError)
    def wechat_error(exc):
        app.logger.error("WeChat Pay operation failed (%s)", exc.code)
        trace("sdk.error", exc.code, {"exception_type": type(exc).__name__, "provider_code": exc.provider_code}, number=getattr(g, "order_number", None), user_id=getattr(g, "user", {}).get("id"), accepted=False)
        return jsonify(error=exc.code), 409 if exc.code == "wechat_order_number_used" else 502

    @app.errorhandler(AlipayError)
    def alipay_error(exc):
        app.logger.error("Alipay operation failed (%s)", type(exc).__name__)
        trace("sdk.error", "payment_provider_error", {"exception_type": type(exc).__name__}, number=getattr(g, "order_number", None), user_id=getattr(g, "user", {}).get("id"), accepted=False)
        return jsonify(error="payment_provider_error"), 502

    @app.get("/api/health")
    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.get("/api/capabilities")
    def capabilities():
        available = channels.all()
        return jsonify(payment_ready=any(ch["enabled"] and ch["configured"] and ch["integrated"] for ch in available), registration=store.setting("registration", app.config["ALLOW_REGISTRATION"] == "true"),
                       products=["page", "wap", "app", "mini"], channels=sorted(INTEGRATED), environment=cfg["env"],
                       channel_enabled=store.setting("alipay_enabled", True), max_amount=store.setting("max_amount", money(app.config["TEST_MAX_AMOUNT"])),
                       automatic_verification=True, notification_configured=bool(cfg["notify_url"]),
                       payment_configs=[{k: item[k] for k in ("id", "config_id", "name", "enabled", "configured", "integrated", "is_default")} | {"environment": item["fields"].get("env")} for item in available],
                       email_verification=mailer.config()['registration_code'],turnstile={k:antibot.public()[k] for k in ('enabled','site_key')},
                       registration_review=operations.rules.read()['registration_review'],registration_paid=operations.rules.read()['registration_paid'],registration_fee=operations.rules.read()['registration_fee'],
                       merchant_refund=operations.rules.read()['merchant_refund'],preview=app.config.get("BYPAY_UI_PREVIEW") == "1")

    @app.get("/api/webhooks/public-key")
    def webhook_public_key():
        return jsonify(algorithm="Ed25519", public_key=signer.public_pem())

    @app.post("/api/auth/<action>")
    def authenticate(action):
        if action not in {"login", "register"}:
            raise ApiError("not_found", 404)
        same_origin()
        ip=operations.rules.client_ip(request)
        limit("auth-ip:" + ip, 20, 600)
        payload = json_body()
        try:antibot.verify(payload.get('turnstile_token'),ip,action)
        except ValueError as exc:raise ApiError(str(exc),403) from None
        email, password = payload.get("email", ""), payload.get("password", "")
        if not isinstance(email, str) or not isinstance(password, str) or len(email) > 254 or not 1 <= len(password) <= 128:
            raise ApiError("invalid_credentials")
        email = email.strip().lower()
        if not email or re.search(r"\s|[\x00-\x1f\x7f]", email):
            raise ApiError("invalid_credentials", 401)
        if action == "register" and not email_address(email):
            raise ApiError("invalid_email")
        limit("auth-email:" + email, 10, 600)
        if action == "register":
            if not store.setting("registration", app.config["ALLOW_REGISTRATION"] == "true"):
                raise ApiError("registration_closed", 403)
            if len(password) < 12:
                raise ApiError("password_too_short")
            try:
                inviter=operations.inviter(payload.get('invite_code',''))
                if mailer.config()['registration_code']:mailer.consume_code(email,payload.get('email_code',''))
            except ValueError as exc:raise ApiError(str(exc)) from None
            try:
                user = store.register(email, password)
                merchants.ensure(user["id"])
                operations.ensure(user['id'],new=True,inviter=inviter)
                if mailer.config()['notify_registration']:mailer.enqueue('registration',email,{'message':'账户已创建，请登录控制台查看商户接入状态'},'registration:'+str(user['id']))
            except sqlite3.IntegrityError:
                raise ApiError("email_unavailable", 409)
        else:
            user = store.user_by_email(email)
            valid = check_password_hash(user["password"] if user else EMPTY_PASSWORD_HASH, password)
            if not user or not valid:
                raise ApiError("invalid_credentials", 401)
            if user.get("disabled"):
                raise ApiError("user_disabled", 403)
        old_token = request.cookies.get(cookie_name)
        if old_token:
            store.logout(old_token)
        token, csrf = store.session(user["id"])
        response = jsonify(user=user_info(user), csrf_token=csrf)
        response.set_cookie(cookie_name, token, max_age=43200, httponly=True, secure=secure, samesite="Lax", path="/")
        return response

    @app.get("/api/auth/me")
    @auth(session_only=True)
    def me():
        return jsonify(user=user_info(g.user), csrf_token=g.user["csrf"], api_key=store.key_info(g.user["id"]), access=operations.access(g.user["id"]))

    @app.post("/api/auth/logout")
    @auth(session_only=True)
    def logout():
        store.logout(request.cookies.get(cookie_name, ""))
        response = jsonify(ok=True)
        response.delete_cookie(cookie_name, path="/", secure=secure, httponly=True, samesite="Lax")
        return response

    @app.post("/api/keys")
    @auth(session_only=True)
    def rotate_key():
        return jsonify(api_key=store.rotate_key(g.user["id"]))

    @app.get("/api/v1/payments")
    @auth()
    def list_payments():
        return jsonify(orders=[public_order(o) for o in store.orders(g.user["id"], admin=g.user["role"] == "admin")])

    @app.get("/api/v1/payments/<number>")
    @auth()
    def get_payment(number):
        owned_order(number, include_deleted=request.args.get("deleted") == "1")
        result = reconciler.snapshot(number)
        return jsonify(order=public_order(result["order"]), verification=result["verification"])

    @app.delete("/api/v1/payments/<number>")
    @auth(session_only=True)
    def delete_payment(number):
        order = owned_order(number)
        if order["status"] == "REFUNDING": raise ApiError("refund_in_progress", 409)
        with store.connect() as db: db.execute("UPDATE orders SET deleted_at=? WHERE out_trade_no=?", (int(time.time()),number))
        trace("order.deleted", "order_deleted", number=number, user_id=g.user["id"])
        return jsonify(ok=True)

    @app.post("/api/v1/payments/<number>/restore")
    @auth(session_only=True)
    def restore_payment(number):
        owned_order(number, include_deleted=True)
        with store.connect() as db: db.execute("UPDATE orders SET deleted_at=NULL WHERE out_trade_no=?", (number,))
        trace("order.restored", "order_restored", number=number, user_id=g.user["id"])
        return jsonify(ok=True)

    @app.post("/api/v1/payments/<number>/refund")
    @auth(session_only=True)
    def refund_payment(number):
        owned_order(number)
        if g.user['role']!='admin' and not operations.rules.read()['merchant_refund']:raise ApiError('merchant_refund_disabled',403)
        payload = json_body()
        refund_no, reason = payload.get("refund_no"), payload.get("reason", "订单退款")
        if not isinstance(refund_no, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", refund_no): raise ApiError("invalid_refund_number")
        if not isinstance(reason,str) or not reason.strip() or len(reason)>128 or any(ord(c)<32 for c in reason): raise ApiError("invalid_refund_reason")
        try: amount=money(payload.get("amount"))
        except ValueError: raise ApiError("invalid_amount")
        record=refunds.start(number,refund_no,amount,reason.strip(),g.user["id"])
        return jsonify(refund=record,order=public_order(store.order(number))), 202 if record["status"] in {"PENDING","UNKNOWN"} else 200

    @app.post("/api/v1/payments/<number>/refund-status")
    @auth(session_only=True)
    def refund_status(number):
        if g.user['role']!='admin' and not operations.rules.read()['merchant_refund']:raise ApiError('merchant_refund_disabled',403)
        order=owned_order(number)
        limit("refund-query:"+number,6)
        record=refunds.sync(order["active_refund_no"]) if order["active_refund_no"] else None
        return jsonify(refund=record,order=public_order(store.order(number)))

    @app.get("/api/v1/payments/<number>/qr.svg")
    @auth()
    def payment_qr(number):
        from .qrcode import payment_qr_svg
        order = owned_order(number)
        limit("qr:" + str(g.user["id"]), 60)
        if not (order["pay_url"] or order["cashier_qr"]): raise ApiError("qr_not_available", 422)
        if order["status"] != "CREATED" or order["expires_at"] <= int(time.time()): raise ApiError("payment_order_expired", 409)
        if order["product"] == "wap":
            # The signed URL is ~1000 characters: its QR is a 160-module grid that cannot hold a logo or be
            # scanned at card size. Encode a short redirect on the configured site address instead (a phone
            # must be able to reach it, so it never comes from the Host header).
            value = site.read()["origin"] + "/m/" + store.short_link(number)
        else:
            value = order["cashier_qr"] or order["pay_url"]
        try:
            svg = payment_qr_svg(value, order["channel"])
        except ValueError:
            raise ApiError("qr_not_available", 422)
        return Response(svg, mimetype="image/svg+xml")

    @app.get("/m/<token>")
    def mobile_checkout(token):
        from .cashier import allowed_url
        limit("short:" + str(request.remote_addr), 120)
        text = lambda message, status: (message, status, {"Content-Type": "text/plain; charset=utf-8"})
        order = store.order_for_short_link(token)
        if not order or order["deleted_at"] or order["product"] != "wap": return text("订单不存在", 404)
        if order["status"] != "CREATED" or order["expires_at"] <= int(time.time()): return text("订单已结束或已过期，请返回商户重新下单", 410)
        if order["channel"] == "wechat":
            target = wechat_h5_target(order)
            if not target: return text("手机收银台链接已过期，请返回商户重新下单", 410)
        else:
            try:
                target = allowed_url(order["pay_url"])
            except ValueError:
                return text("订单暂不可用", 404)
        trace("checkout.clicked", "mobile_checkout_opened", {"note": "手机访问短链接，不作为付款凭证。"}, order["out_trade_no"], order["user_id"])
        return redirect(target, 302)

    @app.post("/api/v1/payments/<number>/prepare-qr")
    @auth()
    def prepare_qr(number):
        return prepare_qr_request(number, json_body() if request.content_length else {})

    def prepare_qr_request(number, payload):
        from .cashier import fetch_cashier_qr
        order = owned_order(number)
        if set(payload) - {"refresh", "native"} or any(type(payload[k]) is not bool for k in payload): raise ApiError("invalid_json")
        if order["cashier_qr"] and not payload.get("refresh") and not payload.get("native"): return jsonify(order=public_order(order))
        if order["product"] != "page" or order["status"] != "CREATED" or order["expires_at"] <= int(time.time()): raise ApiError("qr_not_available", 422)
        limit("cashier:" + number, 3, 60)
        if payload.get("refresh"):
            order = reconciler.sync(number, "qr_refresh")["order"]
            if order["status"] != "CREATED": return jsonify(order=public_order(order))
        if order["channel"] == "wechat":
            return jsonify(order=public_order(store.order(number)))  # a WeChat Native code stays valid for the whole payment window
        trace("checkout.qr", "cashier_qr_requested", number=number, user_id=g.user["id"])
        try:
            qr_cfg = channels.payment_config(order)
            old_return = urlsplit(qr_cfg["return_url"])
            browser_origin = request.headers.get("Origin", origin)
            if app.config.get("BYPAY_RUN_MODE") == "local" and old_return.hostname in {"localhost", "127.0.0.1", "::1"} and browser_origin in browser_origins:
                qr_cfg["return_url"] = browser_origin + old_return.path + ("?" + old_return.query if old_return.query else "")
            qr_cfg["time_expire"] = datetime.fromtimestamp(order["expires_at"], ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
            # Epay::qrcodepc requests the embedded QR mode (4), not the default
            # browser-session QR; the order number, amount and deadline stay fixed.
            checkout_url = client(qr_cfg).create_page_pay_url(number, order["amount"], order["subject"])
            embedded = client({**qr_cfg, "qr_pay_mode":"4", "qrcode_width":230}).create_page_pay_url(number, order["amount"], order["subject"])
            if payload.get("native"):
                with store.connect() as db: db.execute("UPDATE orders SET pay_url=?,provider_return_url=? WHERE out_trade_no=?", (checkout_url,qr_cfg["return_url"],number))
                trace("checkout.qr", "cashier_frame_ready", {"mode":"4","width":230}, number,g.user["id"])
                return jsonify(order=public_order(store.order(number)),cashier_url=embedded)
            value = fetch_cashier_qr(embedded)
        except Exception as exc:
            trace("checkout.qr", "cashier_qr_unavailable", {"exception_type": type(exc).__name__, "check": str(exc) if isinstance(exc,ValueError) and str(exc).startswith("cashier_") else "provider_request_failed"}, number, g.user["id"], False)
            raise ApiError("cashier_qr_unavailable", 502) from None
        with store.connect() as db: db.execute("UPDATE orders SET cashier_qr=?,pay_url=?,provider_return_url=? WHERE out_trade_no=?", (value, checkout_url, qr_cfg["return_url"],number))
        trace("checkout.qr", "cashier_qr_ready", {"source": "alipay_website_checkout"}, number, g.user["id"])
        return jsonify(order=public_order(store.order(number)))

    @app.post("/api/v1/payments/<number>/checkout-opened")
    @auth(session_only=True)
    def checkout_opened(number):
        order = owned_order(number)
        trace("checkout.clicked", "checkout_open_requested", {"note": "浏览器点击事件，不作为付款凭证。"}, number, order["user_id"])
        return jsonify(ok=True)

    @app.post("/api/v1/payments")
    @auth()
    def create_payment():
        return create_payment_request(json_body())

    def create_payment_request(payload,internal=False):
        number, subject = payload.get("out_trade_no"), payload.get("subject")
        if not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", number):
            raise ApiError("invalid_order_number")
        g.order_number = number
        config_id = payload.get("config_id")
        if config_id is not None and (type(config_id) is not int or config_id < 1): raise ApiError("invalid_channel_config")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 128 or any(ord(c) < 32 for c in subject):
            raise ApiError("invalid_subject")
        channel = payload.get("channel", "alipay")
        if channel not in INTEGRATED or payload.get("product", "page") not in {"page", "wap", "app", "mini"}:
            raise ApiError("unsupported_product", 422)
        if channel == "wechat" and len(number) > 32: raise ApiError("wechat_order_number_too_long")  # WeChat Pay accepts 6 to 32 characters
        buyer = {k: payload[k] for k in ("buyer_id", "buyer_open_id") if k in payload}
        if payload.get("product") == "mini":
            if len(buyer) != 1: raise ApiError("buyer_identity_required")
            for key, value in buyer.items():
                pattern = r"2088[0-9]{12}" if key == "buyer_id" else r"[A-Za-z0-9_-]{8,128}"
                if (channel == "wechat" and key != "buyer_open_id") or not isinstance(value, str) or not re.fullmatch(pattern, value): raise ApiError("invalid_buyer_identity")
        elif buyer:
            raise ApiError("invalid_buyer_identity")
        if any(k in payload for k in ("return_url", "seller_id", "app_id")):
            raise ApiError("server_managed_field")
        notify_url = payload.get("notify_url", "")
        if not isinstance(notify_url, str):
            raise ApiError("invalid_merchant_callback")
        if notify_url:
            try:
                validate_callback(notify_url, callback_hosts, callback_local)
            except ValueError:
                raise ApiError("invalid_merchant_callback")
        try:
            amount = money(payload.get("amount"))
        except ValueError:
            raise ApiError("invalid_amount")
        if Decimal(amount) > Decimal(store.setting("max_amount", money(app.config["TEST_MAX_AMOUNT"]))):
            raise ApiError("test_amount_exceeded")
        payload = {"out_trade_no": number, "subject": subject.strip(), "amount": amount, "product": payload.get("product", "page"), "notify_url": notify_url}
        if channel != "alipay": payload["channel"] = channel  # Alipay requests keep their original hash, so old orders still replay
        request_hash = digest(json.dumps({**payload, **buyer}, sort_keys=True, separators=(",", ":")))
        existing = store.order(number)
        was_existing = existing is not None
        def matching(order):
            if order["user_id"] != g.user["id"] or any(order[k] != payload[k] for k in payload) or (order.get("request_hash") and order["request_hash"] != request_hash) or (config_id and config_id != order["payment_config_id"]):
                raise ApiError("order_conflict", 409)
        def replay(order):
            matching(order)
            trace("create.replayed", "same_order_returned", number=number, user_id=g.user["id"])
            return jsonify(order=public_order(order)), 200
        if existing:
            matching(existing)
            if existing["pay_url"] or json.loads(existing["payment_payload"]) or existing["status"] != "CREATED": return replay(existing)
        if not existing and not internal:
            try:operations.precheck(g.user['id'],payload['subject'],payload['amount'],notify_url,number,operations.rules.client_ip(request),admin=g.user['role']=='admin')
            except ValueError as exc:raise ApiError(str(exc),403) from None
        if not existing and channel == "alipay" and not store.setting("alipay_enabled", True):
            raise ApiError("channel_disabled", 403)
        policy = site.read()
        order_cfg = channels.payment_config(existing) if existing else channels.pin(channels.payment_config(config_id=config_id, channel=channel))
        if not existing and not order_cfg["config_enabled"]: raise ApiError("channel_disabled", 403)
        expires_at = existing["expires_at"] if existing else int(time.time()) + policy["order_timeout_minutes"] * 60
        if expires_at <= int(time.time()): raise ApiError("payment_order_expired", 409)
        if channel == "wechat":
            return create_wechat(payload, buyer, order_cfg, existing, request_hash, expires_at, replay, matching, was_existing)
        order_cfg.update(expires_at=expires_at, timeout_express=f"{policy['order_timeout_minutes']}m",
                         time_expire=datetime.fromtimestamp(expires_at, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"))
        return_token = secrets.token_urlsafe(32) if payload["product"] in {"page", "wap"} else None
        # Keep the browser on the same allowed hostname so its login cookie survives.
        # A forwarded or arbitrary Host header never controls a payment URL.
        browser_origin = request.headers.get("Origin", origin)
        if app.config.get("BYPAY_RUN_MODE") == "local" and browser_origin in browser_origins and order_cfg["return_url"] == base_cfg["return_url"]:
            order_cfg["return_url"] = browser_origin + "/alipay/return"
        return_base = order_cfg["return_url"]
        if return_token: order_cfg["return_url"] += "?" + urlencode({"state": return_token})
        pay_client = client(order_cfg)
        trace("create.received", "request_validated", payload, number, g.user["id"])
        if payload["product"] == "mini":
            if not existing:
                try:
                    existing = store.create_order(g.user["id"], payload, "", order_cfg, request_hash=request_hash)
                except sqlite3.IntegrityError:
                    existing = store.order(number)
                    matching(existing)
                trace("create.saved", "order_saved", {"status": existing["status"], "amount": amount}, number, g.user["id"])
            lease = store.claim_checkout(number)
            if not lease:
                current = store.order(number)
                if json.loads(current["payment_payload"]): return replay(current)
                raise ApiError("payment_creation_pending", 409)
            try:
                method = "alipay.trade.create"
                trace("sdk.create", "provider_creation_requested", {"method": method}, number, g.user["id"])
                result = pay_client.create_mini_trade(number, amount, payload["subject"], buyer=buyer)
                trace("sdk.response", "sdk_response_received", result.get("sdk_response", {}), number, g.user["id"], accepted=result.get("is_success", False))
                if not result.get("is_success"):
                    raise ApiError("payment_product_unavailable" if result.get("sub_code") in {"isv.insufficient-isv-permissions", "ACQ.ACCESS_FORBIDDEN", "ACQ.PRODUCT_NOT_SUPPORT"} else "payment_provider_error", 502)
                if result.get("out_trade_no") != number: raise ApiError("payment_mismatch", 502)
                if any(result.get(key) and result[key] != order_cfg[key] for key in ("app_id", "seller_id")):
                    raise ApiError("payment_mismatch", 502)
                if result.get("total_amount"):
                    try:
                        if money(result["total_amount"]) != amount: raise ValueError()
                    except ValueError:
                        raise ApiError("payment_mismatch", 502) from None
                trade_no = result.get("trade_no")
                if not isinstance(trade_no, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", trade_no): raise ApiError("payment_mismatch", 502)
                payment = {"kind": "mini", "trade_no": trade_no}
                store.finish_checkout(number, lease, payment, trade_no)
                trace("sdk.ready", "checkout_ready", {"kind": payment["kind"]}, number, g.user["id"])
            finally:
                store.finish_checkout(number, lease)
            return jsonify(order=public_order(store.order(number))), 200 if was_existing else 201
        if payload["product"] == "app":
            payment = {"kind": "app", "order_string": pay_client.create_app_pay_string(number, amount, payload["subject"])}
            try:
                order = store.create_order(g.user["id"], payload, "", order_cfg, payment_payload=payment, request_hash=request_hash)
            except sqlite3.IntegrityError:
                return replay(store.order(number))
            trace("sdk.link", "app_order_signed", {"method": "alipay.trade.app.pay", "expires_at": expires_at}, number, g.user["id"])
            trace("create.saved", "order_saved", {"status": order["status"], "amount": amount}, number, g.user["id"])
            return jsonify(order=public_order(order)), 201
        fn = pay_client.create_wap_pay_url if payload["product"] == "wap" else pay_client.create_page_pay_url
        pay_url = fn(number, amount, payload["subject"])
        parsed_url = urlsplit(pay_url)
        query = parse_qs(parsed_url.query)
        trace("sdk.link", "checkout_url_signed", {"method": "alipay.trade." + payload["product"] + ".pay",
              "response_type": "URL string", "gateway": parsed_url.scheme + "://" + parsed_url.netloc + parsed_url.path,
              "parameter_names": sorted(query), "notify_url": order_cfg["notify_url"], "return_url": return_base,
              "note": "仅本地签名，尚未向支付宝请求。用户打开收银台后，支付宝才可能创建交易。"}, number, g.user["id"])
        try:
            order = store.create_order(g.user["id"], payload, pay_url, order_cfg, return_token, {"kind": "url", "url": pay_url}, request_hash)
        except sqlite3.IntegrityError:
            return replay(store.order(number))
        trace("create.saved", "order_saved", {"status": order["status"], "amount": amount}, number, g.user["id"])
        return jsonify(order=public_order(order)), 201

    @app.post("/api/v1/payments/<number>/sync")
    @auth()
    def sync_payment(number):
        owned_order(number)
        limit("sync:" + number, 6)
        result = reconciler.sync(number)
        if result["verification"]["error"]:
            raise ApiError(result["verification"]["error"], 502)
        return jsonify(order=public_order(result["order"]), verification=result["verification"], message=result["verification"]["state"])

    @app.post("/api/payment-result")
    def payment_result():
        same_origin()
        limit("result-ip:" + str(request.remote_addr), 120)
        order = store.return_order(json_body().get("state"))
        if not order:
            raise ApiError("payment_result_expired", 404)
        limit("result:" + order["out_trade_no"], 30)
        result = reconciler.sync(order["out_trade_no"], "return")
        # A result token grants only this receipt, never a session or checkout access.
        return jsonify(order={k: result["order"][k] for k in ("out_trade_no", "subject", "amount", "status", "paid_at", "channel")}, verification=result["verification"])

    def rfc3339(timestamp):
        return datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")

    def create_wechat(payload, buyer, order_cfg, existing, request_hash, expires_at, replay, matching, was_existing):
        """WeChat Pay order creation, ported from Epay plugins/wxpayn (qrcode / h5 / jspay / wxminipay / apppay).

        The order row exists before any provider I/O and a lease serialises creators, so a repeated request
        never creates two WeChat orders; a stored payload is replayed instead of calling WeChat again.
        """
        number, product, amount = payload["out_trade_no"], payload["product"], payload["amount"]
        base = site.read()["origin"]
        if not base.startswith("https://"):
            raise ApiError("wechat_https_required", 409)  # WeChat requires a public HTTPS notify_url on every order
        order_cfg.update(notify_url=f"{base}/wechat/notify/{number}", return_url=f"{base}/wechat/return" if product == "wap" else "", expires_at=expires_at)
        wx = wechat_client(order_cfg)
        trace("create.received", "request_validated", payload, number, g.user["id"])
        if not existing:
            try:
                existing = store.create_order(g.user["id"], payload, "", order_cfg, request_hash=request_hash)
            except sqlite3.IntegrityError:
                existing = store.order(number)
                matching(existing)
            trace("create.saved", "order_saved", {"status": existing["status"], "amount": amount}, number, g.user["id"])
        lease = store.claim_checkout(number)
        if not lease:
            current = store.order(number)
            if json.loads(current["payment_payload"]): return replay(current)
            raise ApiError("payment_creation_pending", 409)
        try:
            cents = int(Decimal(amount) * 100)
            description, deadline, ip = payload["subject"], rfc3339(expires_at), operations.rules.client_ip(request)
            trace("sdk.create", "provider_creation_requested", {"method": "wechat." + product}, number, g.user["id"])
            cashier_qr = ""
            if product == "page":
                code = wx.native(number, cents, description, order_cfg["notify_url"], deadline, ip)["code_url"]
                payment, cashier_qr = {"kind": "wechat_native", "code_url": code}, code
            elif product == "wap":
                link = wx.h5(number, cents, description, order_cfg["notify_url"], deadline, ip, "冰云支付", base)["h5_url"]
                payment = {"kind": "wechat_h5", "url": base + "/m/" + store.short_link(number), "h5_url": link, "h5_at": int(time.time()), "client_ip": ip, "return_token": store.issue_return_token(number)}
            elif product == "app":
                payment = {"kind": "wechat_app", "params": wx.app(number, cents, description, order_cfg["notify_url"], deadline, ip)}
            else:
                payment = {"kind": "wechat_jsapi", "params": wx.jsapi(number, cents, description, order_cfg["notify_url"], deadline, buyer["buyer_open_id"], ip)}
            store.finish_checkout(number, lease, payment)
            if cashier_qr:
                with store.connect() as db: db.execute("UPDATE orders SET cashier_qr=? WHERE out_trade_no=?", (cashier_qr, number))
            trace("sdk.ready", "checkout_ready", {"kind": payment["kind"]}, number, g.user["id"])
        finally:
            store.finish_checkout(number, lease)
        return jsonify(order=public_order(store.order(number))), 200 if was_existing else 201

    def wechat_h5_target(order):
        """A WeChat H5 link only lives 5 minutes, so a stale one is renewed with the order's original parameters."""
        payload = json.loads(order["payment_payload"] or "{}")
        if payload.get("kind") != "wechat_h5": return None
        link, now = payload["h5_url"], int(time.time())
        if now - payload.get("h5_at", 0) >= 240:
            if now - payload.get("h5_failed_at", 0) < 20: return None  # a link that cannot be renewed does not call WeChat Pay again on every visit
            try:
                cfg = channels.payment_config(order)
                link = wechat_client(cfg).h5(order["out_trade_no"], int(Decimal(order["amount"]) * 100), order["subject"], order["provider_notify_url"], rfc3339(order["expires_at"]),
                                             payload["client_ip"], "冰云支付", site.read()["origin"])["h5_url"]
                payload.update(h5_url=link, h5_at=now)
            except (WechatError, ApiError, ChannelError):
                payload["h5_failed_at"] = now
                link = None
            with store.connect() as db: db.execute("UPDATE orders SET payment_payload=? WHERE out_trade_no=?", (json.dumps(payload), order["out_trade_no"]))
            if link is None: return None
        redirect = f"{site.read()['origin']}/wechat/return?state={payload['return_token']}"
        return f"{link}&redirect_url={quote(redirect, safe='')}"

    @app.post("/wechat/notify/<number>")
    def wechat_notify(number):
        """WeChat Pay payment notice: verify with the public key, decrypt with the APIv3 key, then apply only a matching, paid order."""
        known = re.fullmatch(r"[A-Za-z0-9_|*-]{6,32}", number) is not None
        # anyone can post here, so the work per request is capped before anything is read, and a flood of refusals leaves a few log lines, not thousands
        if not store.rate_limit("wechat-notify:" + (number if known else "invalid"), 30, 60) or not store.rate_limit("wechat-notify-ip:" + (request.remote_addr or "-"), 300, 60):
            return jsonify(code="FAIL", message="too many requests"), 429

        def refuse(status, reason, message):
            if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", reason): reason = "notification_invalid"
            if store.rate_limit(f"wechat-notify-log:{number if known else 'invalid'}:{reason}", 3, 300):
                trace("notify.rejected", reason, {"channel": "wechat"}, number if known else None, accepted=False)
            return jsonify(code="FAIL", message=message), status
        order = store.order(number) if known else None
        if not order or order["channel"] != "wechat":
            return refuse(400, "unknown_order", "rejected")  # the same answer as a bad signature, so this cannot be used to look orders up
        try:
            cfg = channels.payment_config(order)
            if not wechat_ready(cfg): raise WechatError("not configured", "payment_not_configured")
            notice = wechat_factory(cfg).read_notification(request.headers, request.get_data())
            trace("notify.signature", "signature_valid", {"out_trade_no": number, "channel": "wechat"}, number, order["user_id"])
            if notice["event_type"] != "TRANSACTION.SUCCESS":
                return "", 204  # verified, and nothing for us to change
            paid = notice["transaction"]
            amount = paid.get("amount") or {}
            if paid.get("trade_state") != "SUCCESS" or paid.get("out_trade_no") != number or paid.get("mchid") != cfg["mch_id"] or paid.get("appid") != cfg["app_id"] or amount.get("currency", "CNY") != "CNY":
                raise ValueError("merchant_mismatch" if paid.get("mchid") != cfg["mch_id"] or paid.get("appid") != cfg["app_id"] else "unknown_order")
            store.apply_notification({"out_trade_no": number, "app_id": cfg["app_id"], "seller_id": cfg["mch_id"], "total_amount": yuan(amount.get("total")),
                                      "trade_no": paid.get("transaction_id", ""), "trade_status": "TRADE_SUCCESS"}, cfg["app_id"], cfg["mch_id"], provider_state="SUCCESS")
        except WechatError as exc:
            return refuse(400, exc.code, "rejected")
        except (ValueError, sqlite3.IntegrityError, ChannelError) as exc:
            return refuse(400, str(exc) if isinstance(exc, ValueError) and not isinstance(exc, ChannelError) else "payment_mismatch", "rejected")
        order = store.order(number)
        trace("notify.accepted", "notification_verified_and_saved", {"app_id": cfg["app_id"], "seller_id": cfg["mch_id"], "total_amount": yuan(amount.get("total")), "trade_state": "SUCCESS",
              "trade_no": paid.get("transaction_id", ""), "reply": "204"}, number, order["user_id"])
        return "", 204

    @app.post("/alipay/notify")
    def notify():
        if request.mimetype != "application/x-www-form-urlencoded" or request.args:
            return "failure", 400, {"Content-Type": "text/plain; charset=utf-8"}
        if any(len(request.form.getlist(k)) != 1 for k in request.form):
            return "failure", 400, {"Content-Type": "text/plain; charset=utf-8"}
        params = request.form.to_dict()
        trace("notify.received", "notification_received", {"parameters": params}, accepted=False)
        try:
            existing_order = store.order(params.get("out_trade_no", ""))
            notify_cfg = channels.payment_config(existing_order)
            if not ready(notify_cfg) or params.get("sign_type") != "RSA2" or not AlipayClient.verify_notify(params, notify_cfg["alipay_public_key"]):
                raise ValueError("invalid_signature")
            trace("notify.signature", "signature_valid", {"out_trade_no": params.get("out_trade_no")})
            store.apply_notification(params, notify_cfg["app_id"], notify_cfg["seller_id"])
        except (ValueError, sqlite3.IntegrityError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "trade_conflict"
            trace("notify.rejected", reason, accepted=False)
            return "failure", 200, {"Content-Type": "text/plain; charset=utf-8"}
        order = store.order(params["out_trade_no"])
        trace("notify.accepted", "notification_verified_and_saved", {"app_id": params["app_id"], "seller_id": params["seller_id"],
              "total_amount": params["total_amount"], "trade_status": params["trade_status"], "trade_no": params["trade_no"],
              "reply": "success"}, order["out_trade_no"], order["user_id"])
        return "success", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.get("/alipay/return")
    @app.get("/wechat/return")
    def payment_return():
        # Return parameters can locate a receipt but NEVER confirm payment.
        token = request.args.get("state", "")
        order = None if any(len(request.args.getlist(k)) != 1 for k in request.args) else store.return_order(token)
        if not order and not token and all(len(request.args.getlist(k)) == 1 for k in request.args):
            # Older checkout links had no state token. Only an authentic Alipay
            # return may recover their receipt without a login session.
            legacy = store.order(request.args.get("out_trade_no", ""))
            if legacy and legacy["channel"] == "alipay":
                legacy_cfg = channels.payment_config(legacy)
                if request.args.get("app_id") == legacy["app_id"] and AlipayClient.verify_notify(request.args.to_dict(), legacy_cfg["alipay_public_key"]):
                    order, token = legacy, store.issue_return_token(legacy["out_trade_no"])
        trace("return.received", "browser_return_is_not_payment_confirmation", {"parameter_names": sorted(request.args)},
              order["out_trade_no"] if order else None, order["user_id"] if order else None)
        return redirect("/payment-return" + ("#state=" + token if order else ""), code=303)

    @app.get("/api/admin/events")
    @auth(session_only=True, admin=True)
    def events():
        return jsonify(events=store.events())

    @app.get("/api/events")
    @auth(session_only=True)
    def my_events():
        return jsonify(events=store.events(None if g.user["role"] == "admin" else g.user["id"]))

    @app.get("/api/admin/diagnostics")
    @auth(session_only=True, admin=True)
    def diagnostics():
        checks = [{"name": name, "ok": bool(cfg.get(name))} for name in ("app_id", "seller_id", "app_private_key", "alipay_public_key")]
        if ready():
            try:
                client()
                checks.append({"name": "sdk_keys", "ok": True})
            except (AlipayError, ValueError):
                checks.append({"name": "sdk_keys", "ok": False})
        # every saved configuration, per channel: which required parameters are present
        configurations = []
        for item in channels.all():
            if not item["integrated"]: continue
            fields = {**item["fields"], **{key: present for key, present in item["secrets"].items()}}
            required = REQUIRED_FIELDS[item["id"]]
            configurations.append({"id": item["id"], "config_id": item["config_id"], "name": item["name"], "enabled": item["enabled"], "configured": item["configured"],
                                   "checks": [{"name": key, "ok": bool(fields.get(key))} for key in sorted(required)]})
        return jsonify(checks=checks, configurations=configurations, origin=site.read()["origin"], notify_url=cfg["notify_url"], return_url=cfg["return_url"],
                       wechat_notify_ready=site.read()["origin"].startswith("https://"),
                       environment=cfg["env"], automatic_verification=True, notification_configured=bool(cfg["notify_url"]),
                       note="订单会自动向支付宝查询并验签；异步通知需配置指向当前服务的公网 HTTPS 地址" if not cfg["notify_url"] else "订单通过支付宝通知和自动查询核验，均保留验签和金额校验")

    @app.post("/api/admin/channel-configs/<int:config_id>/check")
    @auth(session_only=True, admin=True)
    def check_channel_config(config_id):
        """Online check that creates nothing: WeChat Pay looks up an order number that cannot exist."""
        with store.connect() as db: row = db.execute("SELECT channel FROM channel_configs WHERE id=?", (config_id,)).fetchone()
        if not row: raise ApiError("not_found", 404)
        if row["channel"] != "wechat": raise ApiError("channel_check_unavailable", 422)
        limit("channel-check:" + str(config_id), 6)
        result = wechat_client(channels.payment_config(config_id=config_id, channel="wechat")).check()
        trace("console.channel", "channel_checked", {"config_id": config_id, "authenticated": result["authenticated"], "verified": result["verified"], "provider_code": result["provider_code"]}, user_id=g.user["id"], accepted=result["authenticated"])
        return jsonify(**result)

    @app.get("/api/admin/channels")
    @auth(session_only=True, admin=True)
    def list_channels():
        return jsonify(channels=channels.all())

    @app.post("/api/admin/channels")
    @auth(session_only=True, admin=True)
    def add_channel():
        payload = json_body()
        return jsonify(channel=channels.create(payload.get("channel"), payload.get("name"), payload.get("fields", {}), g.user["id"], payload.get("enabled",True))), 201

    @app.put("/api/admin/channel-configs/<int:config_id>")
    @auth(session_only=True, admin=True)
    def update_channel_config(config_id):
        payload = json_body()
        with store.connect() as db: row = db.execute("SELECT channel FROM channel_configs WHERE id=?", (config_id,)).fetchone()
        if not row: raise ApiError("not_found", 404)
        return jsonify(channel=channels.save(row["channel"], payload.get("fields", {}), payload.get("version"), g.user["id"], config_id, payload.get("name"), payload.get("enabled"), payload.get("revision")))

    @app.put("/api/admin/channels/<name>")
    @auth(session_only=True, admin=True)
    def update_channel(name):
        payload = json_body()
        if set(payload) - {"fields", "version"}: raise ApiError("invalid_channel_config")
        result = channels.save(name, payload.get("fields"), payload.get("version"), g.user["id"])
        return jsonify(channel=result)

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def frontend(path):
        if path.startswith(("api/", "alipay/", "wechat/")) or path.startswith("."):
            raise ApiError("not_found", 404)
        dist = Path(app.config["FRONTEND_DIST"]).resolve()
        candidate = (dist / path).resolve()
        if path and candidate.is_relative_to(dist) and candidate.is_file():
            return send_from_directory(dist, path)
        if path.startswith("cashier/"):
            value = merchants.cashier(path.removeprefix("cashier/"))
            if not value: raise ApiError("cashier_not_found",404)
            from .epay import frame_origins
            g.cashier_parent = " ".join(frame_origins(value[1]["notify_url"]))
        elif path not in {"", "login", "register", "dashboard", "payment-return"}:
            raise ApiError("not_found", 404)
        if not (dist / "index.html").exists():
            raise ApiError("frontend_not_built", 503)
        return send_from_directory(dist, "index.html")

    from .epay import install_epay
    install_epay(app, merchants, auth, trace, create_payment_request, prepare_qr_request, ApiError)
    from .platform_api import install_platform
    install_platform(app,auth,create_payment_request,ApiError,same_origin)
    install_console(app, store, auth, trace, cfg, ready, ApiError)
    return app
