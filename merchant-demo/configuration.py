"""Live merchant settings, persisted atomically without exposing API credentials."""
import fcntl
import hashlib
import os
import re
import secrets
import socket
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dotenv import dotenv_values, set_key
from pay_http import request_json


class ConfigurationError(ValueError):
    def __init__(self, code, http_status=400, upstream_status=None, key_valid=False):
        super().__init__(code)
        self.code, self.http_status = code, http_status
        self.upstream_status, self.key_valid = upstream_status, key_valid


def local_hosts():
    hosts = {'localhost', '127.0.0.1', '::1'}
    try:
        hostname = socket.gethostname()
        hosts.add(hostname.lower())
        hosts.update(row[4][0].split('%')[0] for row in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('8.8.8.8', 80))
            hosts.add(sock.getsockname()[0])
    except OSError:
        pass
    return hosts


def origin(value, *, callback=False):
    if not isinstance(value, str):
        raise ConfigurationError('invalid_callback_origin' if callback else 'invalid_pay_origin')
    value = value.strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        valid = (parsed.hostname and not parsed.username and not parsed.password and not parsed.path
                 and not parsed.query and not parsed.fragment and not any(ord(c) <= 32 for c in value)
                 and not any(c in value for c in '\\#?') and (parsed.port is None or 1 <= parsed.port <= 65535))
        valid = valid and (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in local_hosts()))
        if not valid:
            raise ValueError()
    except ValueError:
        raise ConfigurationError('invalid_callback_origin' if callback else 'invalid_pay_origin') from None
    return f'{parsed.scheme}://{parsed.netloc.lower()}'


def valid_public_key(pem):
    try:
        return isinstance(serialization.load_pem_public_key(pem.encode()), Ed25519PublicKey)
    except (ValueError, TypeError, AttributeError):
        return False


def fetch_public_key(pay_origin):
    response, status = request_json('GET', pay_origin + '/api/webhooks/public-key')
    pem = response.get('public_key')
    if status != 200 or response.get('algorithm') != 'Ed25519' or not valid_public_key(pem):
        raise ConfigurationError('public_key_unavailable', 502, status, key_valid=True)
    return pem


def verify_key(pay_origin, api_key):
    if not api_key:
        raise ConfigurationError('pay_key_missing')
    if not re.fullmatch(r'byp_[A-Za-z0-9_-]{30,200}', api_key):
        raise ConfigurationError('invalid_api_key_format')
    # This existing Pay endpoint authenticates BEFORE looking up the order.
    # A random absent order proves authentication without creating an order,
    # listing the merchant's transactions, or contacting a payment provider.
    probe = 'DEMO_AUTH_' + secrets.token_hex(20)
    response, status = request_json('GET', pay_origin + '/api/v1/payments/' + probe, api_key=api_key)
    if status == 404 and response.get('error') == 'order_not_found':
        return
    if status == 401 and response.get('error') == 'unauthorized':
        raise ConfigurationError('unauthorized', 422, status)
    code = response.get('error')
    if status == 403:
        code = 'pay_access_denied'
    elif status == 429:
        code = 'rate_limited'
    elif code not in {'pay_unreachable', 'pay_redirect_rejected', 'pay_response_too_large'}:
        code = 'pay_validation_unavailable'
    raise ConfigurationError(code, 502, status)


def fingerprint(values):
    return hashlib.sha256((values['PAY_ORIGIN'] + '\n' + values.get('PAY_API_KEY', '')).encode()).hexdigest()


class Configuration:
    def __init__(self, source, defaults, lock_file):
        self.source, self.defaults, self.lock_file = Path(source), dict(defaults), Path(lock_file)

    def read(self):
        values = dict(self.defaults)
        if self.source.exists():
            values.update({k:v for k,v in dotenv_values(self.source, interpolate=False).items() if v is not None})
        values['PAY_ORIGIN'] = origin(values['PAY_ORIGIN'])
        values['DEMO_ORIGIN'] = origin(values['DEMO_ORIGIN'], callback=True)
        if not values.get('PAY_PUBLIC_KEY_PEM'):
            try:
                values['PAY_PUBLIC_KEY_PEM'] = Path(values['PAY_PUBLIC_KEY_FILE']).read_text()
            except OSError:
                values['PAY_PUBLIC_KEY_PEM'] = ''
        return values

    @contextmanager
    def locked(self):
        self.lock_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.lock_file, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def write(self, updates):
        """Caller holds locked(); replace a complete .env, preserving other settings."""
        self.source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix='.env-', dir=self.source.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(self.source.read_text() if self.source.exists() else '')
            for key, value in updates.items():
                set_key(temporary, key, str(value))
            temporary.chmod(0o600)
            with temporary.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, self.source)
        finally:
            temporary.unlink(missing_ok=True)

    def public(self, *, editable=False):
        values = self.read()
        state = 'missing' if not values.get('PAY_API_KEY') else 'unverified'
        checked_at = 0
        if values.get('PAY_KEY_FINGERPRINT') == fingerprint(values):
            state = values.get('PAY_KEY_STATUS', state)
            try:
                checked_at = int(values.get('PAY_KEY_CHECKED_AT', 0))
            except ValueError:
                pass
        return {
            'pay_origin': values['PAY_ORIGIN'], 'demo_origin': values['DEMO_ORIGIN'],
            'callback_url': values['DEMO_ORIGIN'] + '/notify',
            'alipay_notify_url': values['PAY_ORIGIN'] + '/alipay/notify',
            'key_configured': bool(values.get('PAY_API_KEY')), 'key_status': state,
            'key_checked_at': checked_at,
            'verification_key_configured': valid_public_key(values.get('PAY_PUBLIC_KEY_PEM')),
            'can_manage': editable,
            'key_hint': '•••• ' + values['PAY_API_KEY'][-4:] if editable and values.get('PAY_API_KEY') else '',
        }

    def candidate(self, payload, current):
        if not isinstance(payload, dict):
            raise ConfigurationError('invalid_request')
        result = dict(current)
        result['PAY_ORIGIN'] = origin(payload.get('pay_origin', current['PAY_ORIGIN']))
        result['DEMO_ORIGIN'] = origin(payload.get('demo_origin', current['DEMO_ORIGIN']), callback=True)
        key = payload.get('api_key', '')
        if not isinstance(key, str):
            raise ConfigurationError('invalid_api_key_format')
        key = key.strip()
        if result['PAY_ORIGIN'] != current['PAY_ORIGIN'] and not key and result['PAY_ORIGIN'].startswith('https://'):
            raise ConfigurationError('key_required_for_new_origin')
        result['PAY_API_KEY'] = key or current.get('PAY_API_KEY', '')
        return result

    def mark_key(self, used, state):
        with self.locked():
            if fingerprint(used) == fingerprint(self.read()):
                self.write({'PAY_KEY_STATUS':state, 'PAY_KEY_CHECKED_AT':int(time.time()), 'PAY_KEY_FINGERPRINT':fingerprint(used)})

    def apply(self, payload, *, save):
        with self.locked():
            current = self.read()
            candidate = self.candidate(payload, current)
            try:
                verify_key(candidate['PAY_ORIGIN'], candidate['PAY_API_KEY'])
                pem = fetch_public_key(candidate['PAY_ORIGIN'])
            except ConfigurationError as error:
                if fingerprint(candidate) == fingerprint(current):
                    state = 'valid' if error.key_valid else 'invalid' if error.code == 'unauthorized' else 'unverified'
                    self.write({'PAY_KEY_STATUS':state, 'PAY_KEY_CHECKED_AT':int(time.time()), 'PAY_KEY_FINGERPRINT':fingerprint(current)})
                raise
            status = {'PAY_KEY_STATUS':'valid', 'PAY_KEY_CHECKED_AT':int(time.time()), 'PAY_KEY_FINGERPRINT':fingerprint(candidate)}
            if save:
                self.write({**{k:candidate[k] for k in ('PAY_ORIGIN','DEMO_ORIGIN','PAY_API_KEY')}, 'PAY_PUBLIC_KEY_PEM':pem, **status})
            elif fingerprint(candidate) == fingerprint(current):
                self.write({'PAY_PUBLIC_KEY_PEM':pem, **status})
        return self.public()
