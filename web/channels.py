"""Versioned, encrypted channel configuration shared by all native web processes."""
import base64
import json
import os
import re
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bypay.constants import GATEWAY_BY_ENV
from bypay.key_utils import KeyProblem, load_private_key, load_public_key

FIELDS = {
    "alipay": {"env", "app_id", "seller_id", "app_private_key", "alipay_public_key"},
    "wechat": {"app_id", "mch_id", "certificate_serial", "api_v3_key", "merchant_private_key", "platform_public_key", "platform_public_key_id"},
    "paypal": {"env", "client_id", "client_secret", "webhook_id"},
    "stripe": {"publishable_key", "secret_key", "webhook_secret"},
}
INTEGRATED = {"alipay", "wechat"}
SECRETS = {"app_private_key", "alipay_public_key", "api_v3_key", "merchant_private_key", "platform_public_key", "client_secret", "secret_key", "webhook_secret"}
REQUIRED = {
    "alipay": {"app_id", "seller_id", "app_private_key", "alipay_public_key"},
    "wechat": {"app_id", "mch_id", "certificate_serial", "api_v3_key", "merchant_private_key", "platform_public_key", "platform_public_key_id"},
    "paypal": {"client_id", "client_secret"},
    "stripe": {"secret_key", "webhook_secret"},
}


class ChannelError(ValueError):
    def __init__(self, code, field=None, reason=None):
        super().__init__(code)
        self.field, self.reason = field, reason


class ChannelStore:
    def __init__(self, store, base, url_provider=None):
        self.store = store
        self.base = {key: base.get(key, "") for key in FIELDS["alipay"]}
        self.urls = {key: base[key] for key in ("notify_url", "return_url")}
        self.url_provider = url_provider
        self.key_path = store.path.parent / "channel-master.key"
        # Ported from Epay/includes/lib/Channel.php: payment type and concrete
        # channel ID are separate; each order keeps the selected channel.
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for channel in FIELDS:
                if not db.execute("SELECT 1 FROM channel_configs WHERE channel=? AND is_default=1", (channel,)).fetchone():
                    row = db.execute("SELECT version FROM channel_current WHERE channel=?", (channel,)).fetchone()
                    db.execute("INSERT INTO channel_configs(channel,name,is_default,version) VALUES (?,?,1,?)", (channel, {"alipay":"支付宝","wechat":"微信支付","paypal":"PayPal","stripe":"Stripe"}[channel], row["version"] if row else None))
            db.execute("UPDATE orders SET payment_config_id=(SELECT id FROM channel_configs WHERE channel=orders.channel AND is_default=1) WHERE payment_config_id IS NULL")

    def config(self, config_id=None, channel="alipay", db=None):
        if db is None:
            with self.store.connect() as conn: return self.config(config_id, channel, conn)
        row = db.execute("SELECT * FROM channel_configs WHERE id=?" if config_id else "SELECT * FROM channel_configs WHERE channel=? AND is_default=1", (config_id or channel,)).fetchone()
        if not row or (config_id and row["channel"] != channel): raise ChannelError("unknown_channel")
        return dict(row)

    def all(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT id,channel FROM channel_configs ORDER BY id").fetchall()
        return [self.describe(row["channel"], row["id"]) for row in rows]

    def _cipher(self, create=False):
        if not self.key_path.exists():
            if not create:
                raise ChannelError("channel_config_locked")
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM channel_versions LIMIT 1").fetchone():
                    raise ChannelError("channel_config_locked")
            temp = self.key_path.with_name(self.key_path.name + "." + secrets.token_hex(8))
            fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "wb") as fp:
                    fp.write(Fernet.generate_key()); fp.flush(); os.fsync(fp.fileno())
                try: os.link(temp, self.key_path)
                except FileExistsError: pass
            finally:
                temp.unlink(missing_ok=True)
        try:
            return Fernet(self.key_path.read_bytes())
        except (OSError, ValueError):
            raise ChannelError("channel_config_locked")

    def _decode(self, row):
        try:
            return json.loads(self._cipher().decrypt(row["payload"]))
        except (InvalidToken, ValueError):
            raise ChannelError("channel_config_locked")

    def _row(self, channel, version=None, db=None):
        if db is None:
            with self.store.connect() as db:
                return self._row(channel, version, db)
        if version:
            return db.execute("SELECT * FROM channel_versions WHERE channel=? AND id=?", (channel, version)).fetchone()
        return db.execute("SELECT v.* FROM channel_current c JOIN channel_versions v ON v.id=c.version WHERE c.channel=?", (channel,)).fetchone()

    def _fallback(self, channel):
        return dict(self.base) if channel == "alipay" else ({"env": "sandbox"} if channel == "paypal" else {})

    def get(self, channel, config_id=None):
        if channel not in FIELDS: raise ChannelError("unknown_channel")
        record = self.config(config_id, channel)
        row = self._row(channel, record["version"]) if record["version"] else None
        return (self._decode(row), row["id"], row["created_at"]) if row else (self._fallback(channel), 0, None)

    def describe(self, channel, config_id=None):
        record = self.config(config_id, channel)
        cfg, version, updated = self.get(channel, record["id"])
        return {"id": channel, "config_id": record["id"], "name": record["name"], "enabled": bool(record["enabled"]), "is_default": bool(record["is_default"]), "revision": record["revision"], "version": version, "updated_at": updated, "integrated": channel in INTEGRATED,
                "configured": all(cfg.get(key) for key in REQUIRED[channel]),
                "fields": {key: cfg.get(key, "") for key in sorted(FIELDS[channel] - SECRETS)},
                "secrets": {key: bool(cfg.get(key)) for key in sorted(FIELDS[channel] & SECRETS)},
                "source": "console" if version else "server" if channel == "alipay" else "empty"}

    @staticmethod
    def _validate(channel, cfg):
        env = cfg.get("env", "sandbox")
        if channel in {"alipay", "paypal"} and env not in {"sandbox", "production"}:
            raise ChannelError("invalid_channel_field", "env")
        if channel == "wechat" and cfg.get("certificate_serial"):
            # `openssl x509 -serial` prints "serial=4C3B…"; people also paste colon-separated hex
            cfg["certificate_serial"] = re.sub(r"[\s:]", "", re.sub(r"^\s*serial\s*=\s*", "", cfg["certificate_serial"], flags=re.I)).upper()
        patterns = {
            "alipay": {"app_id": r"[0-9]{16,32}", "seller_id": r"2088[0-9]{12}"},
            "wechat": {"app_id": r"wx[A-Za-z0-9]{16}", "mch_id": r"[0-9]{6,20}", "certificate_serial": r"[A-F0-9]{20,64}", "api_v3_key": r"[\x21-\x7e]{32}", "platform_public_key_id": r"PUB_KEY_ID_[0-9A-Za-z]{8,64}"},
            "paypal": {"client_id": r"[A-Za-z0-9_-]{10,256}", "webhook_id": r"[A-Za-z0-9_-]{5,128}"},
            "stripe": {"publishable_key": r"pk_(?:test|live)_[A-Za-z0-9]{10,256}", "secret_key": r"(?:sk|rk)_(?:test|live)_[A-Za-z0-9]{10,256}", "webhook_secret": r"whsec_[A-Za-z0-9]{10,256}"},
        }
        for field, pattern in patterns[channel].items():
            if cfg.get(field) and not re.fullmatch(pattern, cfg[field]):
                raise ChannelError("invalid_channel_field", field)
        for field in {"app_private_key", "merchant_private_key"} & FIELDS[channel]:
            if cfg.get(field):
                try:
                    key = load_private_key(cfg[field])
                    if key.key_size < 2048: raise KeyProblem("too_short", key.key_size)
                    cfg[field] = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
                except KeyProblem as problem: raise ChannelError("invalid_channel_key", field, problem.reason) from None
        for field in {"alipay_public_key", "platform_public_key"} & FIELDS[channel]:
            if cfg.get(field):
                try:
                    key = load_public_key(cfg[field])
                    if key.key_size < 2048: raise KeyProblem("too_short", key.key_size)
                    cfg[field] = base64.b64encode(key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
                except KeyProblem as problem: raise ChannelError("invalid_channel_key", field, problem.reason) from None
        # The provider's public key must never be your own: that is the classic mix-up for both channels
        pairs = {"alipay": ("app_private_key", "alipay_public_key", "alipay_public_key_is_app_key"), "wechat": ("merchant_private_key", "platform_public_key", "wechat_public_key_is_merchant_key")}
        if channel in pairs and cfg.get(pairs[channel][0]) and cfg.get(pairs[channel][1]):
            private = serialization.load_pem_private_key(cfg[pairs[channel][0]].encode(), password=None)
            own = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            if own == base64.b64decode(cfg[pairs[channel][1]]):
                raise ChannelError(pairs[channel][2], pairs[channel][1])

    def save(self, channel, fields, expected_version, actor, config_id=None, name=None, enabled=None, revision=None):
        if channel not in FIELDS: raise ChannelError("unknown_channel")
        if not isinstance(fields, dict) or set(fields) - FIELDS[channel] or type(expected_version) is not int:
            raise ChannelError("invalid_channel_config")
        for key, value in fields.items():
            if not isinstance(value, str) or len(value) > (12000 if key in SECRETS else 256):
                raise ChannelError("invalid_channel_field", key)
            if key not in SECRETS and any(ord(c) < 32 for c in value): raise ChannelError("invalid_channel_field", key)
        cipher = self._cipher(create=True)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self.config(config_id, channel, db)
            if revision is not None and revision != record["revision"]: raise ChannelError("channel_config_conflict")
            if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 64 or any(ord(c)<32 for c in name)): raise ChannelError("invalid_channel_field", "name")
            if enabled is not None and type(enabled) is not bool: raise ChannelError("invalid_channel_field", "enabled")
            current = self._row(channel, record["version"], db) if record["version"] else None
            if expected_version != (current["id"] if current else 0): raise ChannelError("channel_config_conflict")
            old = self._decode(current) if current else self._fallback(channel)
            cfg = dict(old)
            changed = []
            for key, value in fields.items():
                value = value.strip()
                if key in SECRETS and not value: continue
                if cfg.get(key, "") != value: changed.append(key)
                cfg[key] = value
            self._validate(channel, cfg)
            if channel == "alipay" and not current and record["is_default"]:
                legacy = db.execute("INSERT INTO channel_versions(channel,payload,created_at) VALUES (?,?,?)", (channel, cipher.encrypt(json.dumps(old).encode()), int(time.time()))).lastrowid
                db.execute("UPDATE orders SET channel_version=? WHERE channel='alipay' AND channel_version IS NULL AND app_id=? AND seller_id=?", (legacy, old.get("app_id", ""), old.get("seller_id", "")))
            version = current["id"] if current and not changed else db.execute("INSERT INTO channel_versions(channel,payload,created_at) VALUES (?,?,?)", (channel, cipher.encrypt(json.dumps(cfg).encode()), int(time.time()))).lastrowid
            db.execute("UPDATE channel_configs SET version=?,name=?,enabled=?,revision=revision+1 WHERE id=?", (version, name.strip() if name is not None else record["name"], int(enabled) if enabled is not None else record["enabled"], record["id"]))
            if record["is_default"]:
                db.execute("INSERT INTO channel_current VALUES (?,?) ON CONFLICT(channel) DO UPDATE SET version=excluded.version", (channel, version))
            db.execute("INSERT INTO events(accepted,reason,created_at,stage,details,user_id) VALUES (1,'channel_config_saved',?,'console.channel',?,?)",
                       (int(time.time()), json.dumps({"channel": channel, "config_id": record["id"], "changed_fields": sorted(changed), "version": version}), actor))
        return self.describe(channel, record["id"])

    def create(self, channel, name, fields, actor, enabled=True):
        if channel not in FIELDS or not isinstance(fields, dict) or set(fields)-FIELDS[channel]: raise ChannelError("invalid_channel_config")
        if not isinstance(name, str) or not name.strip() or len(name)>64 or any(ord(c)<32 for c in name): raise ChannelError("invalid_channel_field", "name")
        if type(enabled) is not bool: raise ChannelError("invalid_channel_field", "enabled")
        for key,value in fields.items():
            if not isinstance(value,str) or len(value)>(12000 if key in SECRETS else 256): raise ChannelError("invalid_channel_field", key)
        cfg = {k:v.strip() for k,v in fields.items()}
        self._validate(channel,cfg)
        cipher = self._cipher(create=True)
        with self.store.connect() as db:
            version = db.execute("INSERT INTO channel_versions(channel,payload,created_at) VALUES (?,?,?)", (channel,cipher.encrypt(json.dumps(cfg).encode()),int(time.time()))).lastrowid
            config_id = db.execute("INSERT INTO channel_configs(channel,name,version,enabled) VALUES (?,?,?,?)",(channel,name.strip(),version,int(enabled))).lastrowid
            db.execute("INSERT INTO events(accepted,reason,created_at,stage,details,user_id) VALUES (1,'channel_config_added',?,'console.channel',?,?)", (int(time.time()),json.dumps({"channel":channel,"config_id":config_id}),actor))
        return self.describe(channel, config_id)

    def payment_config(self, order=None, config_id=None, channel=None):
        """The credentials an order must use: its own channel and pinned version, else the (default) configuration."""
        channel = (order or {}).get("channel") or channel or "alipay"
        if channel not in INTEGRATED: raise ChannelError("channel_not_integrated")
        record = self.config(order.get("payment_config_id") if order else config_id, channel)
        row = self._row(channel, order.get("channel_version")) if order and order.get("channel_version") else None
        if order and order.get("channel_version") and not row: raise ChannelError("channel_config_locked")
        if row:
            cfg, version = self._decode(row), row["id"]
        else:
            cfg, version, _ = self.get(channel, record["id"])
        common = {"channel_version": version or None, "payment_config_id": record["id"], "config_enabled": bool(record["enabled"])}
        if channel == "wechat":
            # orders.app_id / orders.seller_id hold the WeChat AppID and merchant number so every notice is matched to both
            return {**cfg, **common, "channel": "wechat", "seller_id": cfg.get("mch_id", "")}
        env = cfg.get("env", "sandbox")
        urls = dict(self.url_provider() if self.url_provider else self.urls)
        if order:
            for key in ("notify_url", "return_url"):
                if order.get("provider_" + key) is not None: urls[key] = order["provider_" + key]
        return {**cfg, **urls, **common, "channel": "alipay", "env": env, "gateway": GATEWAY_BY_ENV[env], "sign_type": "RSA2", "charset": "utf-8"}

    def pin(self, cfg):
        """Bind new orders to the exact credentials used, including during concurrent edits."""
        if cfg.get("channel_version"): return dict(cfg)
        channel = cfg.get("channel", "alipay")
        snapshot = {key: cfg.get(key, "") for key in FIELDS[channel]}
        cipher = self._cipher(create=True)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM channel_versions WHERE channel=? ORDER BY id DESC", (channel,)):
                if self._decode(row) == snapshot:
                    return {**cfg, "channel_version": row["id"]}
            version = db.execute("INSERT INTO channel_versions(channel,payload,created_at) VALUES (?,?,?)",
                                 (channel, cipher.encrypt(json.dumps(snapshot).encode()), int(time.time()))).lastrowid
        return {**cfg, "channel_version": version}
