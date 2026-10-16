"""Validated, persistent operational settings shared by web and worker processes."""
import json
import re
from decimal import Decimal
from urllib.parse import urlsplit

from bypay.payment_validation import money

TIMING_DEFAULTS = {
    "order_timeout_minutes": 15, "poll_interval_seconds": 10,
    "query_window_minutes": 1440, "callback_timeout_seconds": 10,
    "callback_first_delay_seconds": 0, "callback_retry_delays": [30, 120, 600, 1800, 3600],
}


class SettingsError(ValueError):
    def __init__(self, code="invalid_settings", field=None):
        super().__init__(code)
        self.field = field


def validate_origin(value, allow_http=False):
    if not isinstance(value, str) or len(value) > 240 or any(ord(c) < 33 for c in value):
        raise SettingsError(field="origin")
    try:
        parsed = urlsplit(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or (parsed.scheme == "http" and not (local or allow_http)) or (parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError()
    except ValueError:
        raise SettingsError(field="origin") from None
    return value


class SiteSettings:
    def __init__(self, store, config, payment):
        self.store = store
        self.defaults = {**TIMING_DEFAULTS, "origin": config["PUBLIC_BASE_URL"].rstrip("/"),
            "notify_url": payment["notify_url"], "return_url": payment["return_url"],
            "callback_hosts": sorted({h.strip() for h in config.get("BYPAY_CALLBACK_HOSTS", "test.bingyun.vip").split(",") if h.strip()}),
            "registration": config.get("ALLOW_REGISTRATION", "true") == "true", "alipay_enabled": True,
            "max_amount": money(config.get("TEST_MAX_AMOUNT", "3000.00")), "version": 0}
        self.ceiling = self.defaults["max_amount"]
        self.allow_http = config.get("BYPAY_ALLOW_HTTP") == "1"

    def read(self, db=None):
        if db is None:
            with self.store.connect() as connection:
                return self.read(connection)
        saved = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM console_settings")}
        return {**self.defaults, **{k: v for k, v in saved.items() if k in self.defaults}}

    def urls(self):
        cfg = self.read()
        return {k: cfg[k] for k in ("notify_url", "return_url")}

    def save(self, payload):
        if not isinstance(payload, dict) or not payload or set(payload) - self.defaults.keys():
            raise SettingsError()
        updates = {k: v for k, v in payload.items() if k != "version"}
        if not updates:
            raise SettingsError()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self.read(db)
            if "version" in payload and (type(payload["version"]) is not int or payload["version"] != old["version"]):
                raise SettingsError("settings_conflict")
            cfg = {**old, **updates}
            for key in ("registration", "alipay_enabled"):
                if type(cfg[key]) is not bool: raise SettingsError(field=key)
            try:
                cfg["max_amount"] = money(cfg["max_amount"])
                if Decimal(cfg["max_amount"]) > Decimal(self.ceiling): raise ValueError()
            except ValueError:
                raise SettingsError(field="max_amount") from None
            if isinstance(cfg["origin"], str): cfg["origin"] = cfg["origin"].rstrip("/")
            validate_origin(cfg["origin"], self.allow_http)
            if self.defaults["origin"].startswith("https://") and not cfg["origin"].startswith("https://"):
                raise SettingsError(field="origin")
            for key, path in (("notify_url", "/alipay/notify"), ("return_url", "/alipay/return")):
                value = cfg[key]
                if key == "notify_url" and value == "": continue
                if not isinstance(value, str) or len(value) > 256: raise SettingsError(field=key)
                try:
                    parsed = urlsplit(value)
                    validate_origin(parsed.scheme + "://" + parsed.netloc, self.allow_http and key == "return_url")
                except ValueError:
                    raise SettingsError(field=key) from None
                if parsed.path != path or parsed.query or parsed.fragment or any(ord(c) < 33 for c in value):
                    raise SettingsError(field=key)
                if key == "notify_url" and (parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
                    raise SettingsError(field=key)
            hosts = cfg["callback_hosts"]
            if not isinstance(hosts, list) or len(hosts) > 32: raise SettingsError(field="callback_hosts")
            normalized = []
            for host in hosts:
                if not isinstance(host, str) or len(host) > 253: raise SettingsError(field="callback_hosts")
                host = host.strip().lower()
                if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host) or host.endswith((".localhost", ".local")):
                    raise SettingsError(field="callback_hosts")
                normalized.append(host)
            cfg["callback_hosts"] = sorted(set(normalized))
            for key, bounds in {"order_timeout_minutes": (1, 1440), "poll_interval_seconds": (3, 300),
                                "query_window_minutes": (1, 4320), "callback_timeout_seconds": (2, 30),
                                "callback_first_delay_seconds": (0, 3600)}.items():
                if type(cfg[key]) is not int or not bounds[0] <= cfg[key] <= bounds[1]: raise SettingsError(field=key)
            if cfg["query_window_minutes"] < cfg["order_timeout_minutes"]:
                raise SettingsError(field="query_window_minutes")
            delays = cfg["callback_retry_delays"]
            if not isinstance(delays, list) or len(delays) > 8 or any(type(v) is not int or not 5 <= v <= 86400 for v in delays):
                raise SettingsError(field="callback_retry_delays")
            if delays != sorted(delays): raise SettingsError(field="callback_retry_delays")
            cfg["version"] = old["version"] + 1
            for key in {*updates, "version"}:
                db.execute("INSERT INTO console_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(cfg[key])))
        return cfg
