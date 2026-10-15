import hmac
import os
import re
import sqlite3
from decimal import Decimal
from functools import wraps
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from flask import Flask, g, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from bypay.alipay_client import AlipayClient, AlipayError
from bypay.constants import GATEWAY_BY_ENV
from bypay.payment_validation import money
from .store import Store
from .trace import create_trace
from .webhooks import WebhookSigner, validate_callback

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
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("PUBLIC_BASE_URL requires HTTPS outside localhost")
    return {"env": env, "app_id": config.get("ALIPAY_APP_ID", ""),
            "seller_id": config.get("ALIPAY_SELLER_ID", ""),
            "app_private_key": key("ALIPAY_PRIVATE_KEY"),
            "alipay_public_key": key("ALIPAY_PUBLIC_KEY"),
            "gateway": GATEWAY_BY_ENV[env], "sign_type": "RSA2", "charset": "utf-8",
            "notify_url": origin + "/alipay/notify", "return_url": origin + "/alipay/return"}


def create_app(settings=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(PUBLIC_BASE_URL="https://pay.bingyun.vip", DATABASE=str(ROOT / "data/web.sqlite3"),
                      ALLOW_REGISTRATION="true", TEST_MAX_AMOUNT="10.00", MAX_CONTENT_LENGTH=32768,
                      MAX_FORM_MEMORY_SIZE=32768, MAX_FORM_PARTS=64, FRONTEND_DIST=str(ROOT / "frontend/dist"))
    app.config.update({k: v for k, v in os.environ.items() if k.startswith(("ALIPAY_", "BYPAY_")) or k in {"PUBLIC_BASE_URL", "ALLOW_REGISTRATION", "TEST_MAX_AMOUNT", "DATABASE"}})
    app.config.update(settings or {})
    cfg = payment_config(app.config)
    store = Store(app.config["DATABASE"])
    trace = create_trace(store, app.config.get("BYPAY_LOG_FILE", str(Path(app.config["DATABASE"]).parent / "logs/payment.jsonl")))
    signer = WebhookSigner(Path(app.config["DATABASE"]).parent / "webhook-signing.pem")
    callback_hosts = set(app.config.get("BYPAY_CALLBACK_HOSTS", "test.bingyun.vip").split(","))
    store.bootstrap(app.config.get("BYPAY_ADMIN_EMAIL"), app.config.get("BYPAY_ADMIN_PASSWORD"))
    app.extensions.update(store=store, alipay_config=cfg, webhook_signer=signer, trace=trace, callback_hosts=callback_hosts)
    secure = app.config["PUBLIC_BASE_URL"].startswith("https://")
    cookie_name = "__Host-bypay_session" if secure else "bypay_session"
    origin = app.config["PUBLIC_BASE_URL"].rstrip("/")
    if app.config.get("BYPAY_TRUST_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    def ready():
        return all(cfg.get(k) for k in ("app_id", "seller_id", "app_private_key", "alipay_public_key"))

    def client():
        if not ready():
            raise ApiError("payment_not_configured", 503)
        return app.config.get("ALIPAY_CLIENT_FACTORY", AlipayClient)(cfg)

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
        if request.headers.get("Origin") and request.headers["Origin"] != origin:
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
        return {k: v for k, v in order.items() if k not in {"app_id", "seller_id", "user_id"}}

    def owned_order(number):
        order = store.order(number)
        if not order or (order["user_id"] != g.user["id"] and g.user["role"] != "admin"):
            raise ApiError("order_not_found", 404)
        g.order_number = number
        return order

    @app.before_request
    def limit_body_size():
        if request.content_length and request.content_length > app.config["MAX_CONTENT_LENGTH"]:
            raise RequestEntityTooLarge()

    @app.after_request
    def headers(response):
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                                 "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                                 "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        if request.path.startswith(("/api/", "/alipay/")):
            response.headers["Cache-Control"] = "no-store"
        if secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(ApiError)
    def api_error(exc):
        if request.path.startswith("/api/v1/payments") and request.method == "POST" and getattr(g, "user", None):
            trace("request.rejected", exc.code, number=getattr(g, "order_number", None), user_id=g.user["id"], accepted=False)
        response = jsonify(error=exc.code)
        if exc.status == 429:
            response.headers["Retry-After"] = "60"
        return response, exc.status

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error="request_rejected"), exc.code

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
        return jsonify(payment_ready=ready(), registration=app.config["ALLOW_REGISTRATION"] == "true",
                       products=["page", "wap"], channels=["alipay"], environment=cfg["env"], max_amount=money(app.config["TEST_MAX_AMOUNT"]))

    @app.get("/api/webhooks/public-key")
    def webhook_public_key():
        return jsonify(algorithm="Ed25519", public_key=signer.public_pem())

    @app.post("/api/auth/<action>")
    def authenticate(action):
        if action not in {"login", "register"}:
            raise ApiError("not_found", 404)
        same_origin()
        limit("auth-ip:" + str(request.remote_addr), 20, 600)
        payload = json_body()
        email, password = payload.get("email", ""), payload.get("password", "")
        if not isinstance(email, str) or not isinstance(password, str) or len(email) > 254 or not 1 <= len(password) <= 128:
            raise ApiError("invalid_credentials")
        email = email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ApiError("invalid_email")
        limit("auth-email:" + email, 10, 600)
        if action == "register":
            if app.config["ALLOW_REGISTRATION"] != "true":
                raise ApiError("registration_closed", 403)
            if len(password) < 12:
                raise ApiError("password_too_short")
            try:
                user = store.register(email, password)
            except sqlite3.IntegrityError:
                raise ApiError("email_unavailable", 409)
        else:
            user = store.user_by_email(email)
            valid = check_password_hash(user["password"] if user else EMPTY_PASSWORD_HASH, password)
            if not user or not valid:
                raise ApiError("invalid_credentials", 401)
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
        return jsonify(user=user_info(g.user), csrf_token=g.user["csrf"], api_key=store.key_info(g.user["id"]))

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
        return jsonify(order=public_order(owned_order(number)))

    @app.post("/api/v1/payments/<number>/checkout-opened")
    @auth(session_only=True)
    def checkout_opened(number):
        order = owned_order(number)
        trace("checkout.clicked", "checkout_open_requested", {"note": "浏览器点击事件，不作为付款凭证。"}, number, order["user_id"])
        return jsonify(ok=True)

    @app.post("/api/v1/payments")
    @auth()
    def create_payment():
        payload = json_body()
        number, subject = payload.get("out_trade_no"), payload.get("subject")
        if not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", number):
            raise ApiError("invalid_order_number")
        g.order_number = number
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 128 or any(ord(c) < 32 for c in subject):
            raise ApiError("invalid_subject")
        if payload.get("channel", "alipay") != "alipay" or payload.get("product", "page") not in {"page", "wap"}:
            raise ApiError("unsupported_product", 422)
        if any(k in payload for k in ("return_url", "seller_id", "app_id")):
            raise ApiError("server_managed_field")
        notify_url = payload.get("notify_url", "")
        if not isinstance(notify_url, str):
            raise ApiError("invalid_merchant_callback")
        if notify_url:
            try:
                validate_callback(notify_url, callback_hosts)
            except ValueError:
                raise ApiError("invalid_merchant_callback")
        try:
            amount = money(payload.get("amount"))
        except ValueError:
            raise ApiError("invalid_amount")
        if Decimal(amount) > Decimal(money(app.config["TEST_MAX_AMOUNT"])):
            raise ApiError("test_amount_exceeded")
        payload = {"out_trade_no": number, "subject": subject.strip(), "amount": amount, "product": payload.get("product", "page"), "notify_url": notify_url}
        existing = store.order(number)
        def replay(order):
            if order["user_id"] != g.user["id"] or any(order[k] != payload[k] for k in payload):
                raise ApiError("order_conflict", 409)
            trace("create.replayed", "same_order_returned", number=number, user_id=g.user["id"])
            return jsonify(order=public_order(order)), 200
        if existing:
            return replay(existing)
        pay_client = client()
        trace("create.received", "request_validated", payload, number, g.user["id"])
        fn = pay_client.create_wap_pay_url if payload["product"] == "wap" else pay_client.create_page_pay_url
        pay_url = fn(number, amount, payload["subject"])
        parsed_url = urlsplit(pay_url)
        query = parse_qs(parsed_url.query)
        trace("sdk.link", "checkout_url_signed", {"method": "alipay.trade." + payload["product"] + ".pay",
              "response_type": "URL string", "gateway": parsed_url.scheme + "://" + parsed_url.netloc + parsed_url.path,
              "parameter_names": sorted(query), "notify_url": cfg["notify_url"], "return_url": cfg["return_url"],
              "note": "仅本地签名，尚未向支付宝请求。用户打开收银台后，支付宝才可能创建交易。"}, number, g.user["id"])
        try:
            order = store.create_order(g.user["id"], payload, pay_url, cfg)
        except sqlite3.IntegrityError:
            return replay(store.order(number))
        trace("create.saved", "order_saved", {"status": order["status"], "amount": amount}, number, g.user["id"])
        return jsonify(order=public_order(order)), 201

    @app.post("/api/v1/payments/<number>/sync")
    @auth()
    def sync_payment(number):
        order = owned_order(number)
        limit("sync:" + number, 6)
        trace("query.started", "query_requested", {"method": "alipay.trade.query"}, number, order["user_id"])
        result = client().query(out_trade_no=number)
        trace("query.response", "sdk_response_received", result.get("sdk_response", result), number, order["user_id"], accepted=result.get("is_success", False))
        if not result.get("is_success"):
            if result.get("sub_code") == "ACQ.TRADE_NOT_EXIST":
                return jsonify(order=public_order(order), message="awaiting_checkout")
            raise ApiError("payment_provider_error", 502)
        # The official SDK verifies the query response signature. Bind it to this order too.
        params = {**result, "app_id": cfg["app_id"], "seller_id": cfg["seller_id"]}
        try:
            store.apply_notification(params, cfg["app_id"], cfg["seller_id"])
        except (ValueError, sqlite3.IntegrityError):
            raise ApiError("payment_mismatch", 502)
        return jsonify(order=public_order(store.order(number)))

    @app.post("/alipay/notify")
    def notify():
        if request.mimetype != "application/x-www-form-urlencoded" or request.args:
            return "failure", 400, {"Content-Type": "text/plain; charset=utf-8"}
        if any(len(request.form.getlist(k)) != 1 for k in request.form):
            return "failure", 400, {"Content-Type": "text/plain; charset=utf-8"}
        params = request.form.to_dict()
        trace("notify.received", "notification_received", {"parameters": params}, accepted=False)
        try:
            if not ready() or params.get("sign_type") != "RSA2" or not AlipayClient.verify_notify(params, cfg["alipay_public_key"]):
                raise ValueError("invalid_signature")
            trace("notify.signature", "signature_valid", {"out_trade_no": params.get("out_trade_no")})
            store.apply_notification(params, cfg["app_id"], cfg["seller_id"])
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
    def payment_return():
        # Browser return fields never update payment state or become HTML.
        trace("return.received", "browser_return_is_not_payment_confirmation", {"parameter_names": sorted(request.args)})
        return redirect("/payment-return", code=303)

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
        return jsonify(checks=checks, origin=origin, notify_url=cfg["notify_url"], return_url=cfg["return_url"],
                       environment=cfg["env"], note="仅检查本机配置与密钥格式；公网可达性和支付结果需在 VPS 部署后验证。")

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def frontend(path):
        if path.startswith(("api/", "alipay/")) or path.startswith("."):
            raise ApiError("not_found", 404)
        dist = Path(app.config["FRONTEND_DIST"]).resolve()
        candidate = (dist / path).resolve()
        if path and candidate.is_relative_to(dist) and candidate.is_file():
            return send_from_directory(dist, path)
        if path not in {"", "login", "register", "dashboard", "payment-return"}:
            raise ApiError("not_found", 404)
        if not (dist / "index.html").exists():
            raise ApiError("frontend_not_built", 503)
        return send_from_directory(dist, "index.html")

    return app
