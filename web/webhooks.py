import base64
import http.client
import ipaddress
import os
import secrets
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit, urlencode

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def validate_callback(url, allowed_hosts):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise ValueError("invalid_merchant_callback")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.username or
            parsed.password or parsed.fragment or parsed.query or parsed.port not in {None, 443}):
        raise ValueError("invalid_merchant_callback")
    return parsed


class WebhookSigner:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            key = Ed25519PrivateKey.generate()
            value = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            temp = path.with_name(path.name + "." + secrets.token_hex(8))
            fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "wb") as fp:
                    fp.write(value)
                    fp.flush()
                    os.fsync(fp.fileno())
                try:
                    os.link(temp, path)
                except FileExistsError:
                    pass
            finally:
                temp.unlink(missing_ok=True)
        self.key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(self.key, Ed25519PrivateKey):
            raise ValueError("Invalid webhook signing key")

    def public_pem(self):
        return self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def headers(self, body, event_id):
        stamp = str(int(time.time()))
        content = stamp.encode() + b"." + body
        return {"Content-Type": "application/json", "X-ByPay-Timestamp": stamp,
                "X-ByPay-Signature": base64.b64encode(self.key.sign(content)).decode(),
                "X-ByPay-Event-Id": event_id}


def send_to_merchant(url, body, headers, allowed_hosts, timeout=10, method="POST"):
    parsed = validate_callback(url, allowed_hosts)
    target = (parsed.path or "/") + ("?" + body.decode("ascii") if method == "GET" else "")
    content = None if method == "GET" else body
    if method not in {"POST", "GET"}: raise ValueError("invalid_callback_method")
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("callback_address_blocked")
    # Pin this validated IP while retaining TLS hostname verification (no DNS rebinding or redirects).
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=timeout, context=ssl.create_default_context())
    raw_socket = socket.create_connection((addresses[0], 443), timeout=timeout)
    try:
        connection.sock = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parsed.hostname)
        connection.request(method, target, body=content, headers=headers)
        response = connection.getresponse()
        response_body = response.read(1024)
        return response.status == 200 and response_body.strip() == b"success", response.status
    finally:
        raw_socket.close()
        connection.close()


def deliver_one(store, signer, trace, allowed_hosts):
    job = store.claim_delivery()
    if not job:
        return False
    error, http_status = "", None
    try:
        body = job["payload"].encode()
        headers = signer.headers(body, job["id"])
        method = "POST"
        merchants = getattr(store, "merchants", None)
        if merchants and merchants.order(job["out_trade_no"]):
            body = urlencode(merchants.notification(job["out_trade_no"])).encode('ascii')
            headers = {"Accept":"text/plain", "X-ByPay-Event-Id":job["id"]}
            method = "GET"
        success, http_status = send_to_merchant(job["url"], body, headers, allowed_hosts, timeout=store.policy()["callback_timeout_seconds"], method=method)
        if not success:
            error = "merchant_did_not_acknowledge"
    except Exception as exc:
        success, error = False, type(exc).__name__
    status = store.finish_delivery(job, success, error)
    trace("merchant." + status, "merchant_callback_delivered" if success else "merchant_callback_failed" if status == "failed" else "merchant_callback_retry",
          {"event_id": job["id"], "attempt": job["attempts"], "http_status": http_status,
           "error": error, "notify_url": job["url"]}, job["out_trade_no"], job["user_id"], accepted=success)
    return True
