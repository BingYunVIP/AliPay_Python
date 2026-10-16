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

from dotenv import dotenv_values, set_key
from pay_http import request_json
from connection import check, ConnectionError
from epay_protocol import public_pem
from cryptography.fernet import Fernet
import json
import sqlite3
from contextlib import closing


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
        valid = (parsed.hostname and parsed.username is None and parsed.password is None and not parsed.path
                 and not parsed.query and not parsed.fragment and not any(ord(c) <= 32 for c in value)
                 and not any(c in value for c in '\\#?') and (parsed.port is None or 1 <= parsed.port <= 65535))
        valid = valid and (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in local_hosts()))
        if not valid:
            raise ValueError()
    except ValueError:
        raise ConfigurationError('invalid_callback_origin' if callback else 'invalid_pay_origin') from None
    return f'{parsed.scheme}://{parsed.netloc.lower()}'


def fingerprint(values):
    fields = ('PAY_ORIGIN','PAY_MERCHANT_ID','PAY_API_VERSION','PAY_MD5_KEY','PAY_MERCHANT_PRIVATE_KEY','PAY_PLATFORM_PUBLIC_KEY')
    return hashlib.sha256('\n'.join(str(values.get(k,'')) for k in fields).encode()).hexdigest()


class Configuration:
    def __init__(self, source, defaults, lock_file):
        self.source, self.defaults, self.lock_file = Path(source), dict(defaults), Path(lock_file)

    def read(self):
        values = dict(self.defaults)
        if self.source.exists():
            values.update({k:v for k,v in dotenv_values(self.source, interpolate=False).items() if v is not None})
        values['PAY_ORIGIN'] = origin(values['PAY_ORIGIN'])
        values['DEMO_ORIGIN'] = origin(values['DEMO_ORIGIN'], callback=True)
        for key,default in {'PAY_MERCHANT_ID':'','PAY_API_VERSION':'v1','PAY_MD5_KEY':'','PAY_MERCHANT_PRIVATE_KEY':'','PAY_PLATFORM_PUBLIC_KEY':''}.items():
            values.setdefault(key,default)
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
        configured = bool(values['PAY_MERCHANT_ID']) and bool(values['PAY_MD5_KEY'] if values['PAY_API_VERSION']=='v1' else values['PAY_MERCHANT_PRIVATE_KEY'] and values['PAY_PLATFORM_PUBLIC_KEY'])
        state = 'unverified' if configured else 'missing'
        checked_at = 0
        if values.get('PAY_KEY_FINGERPRINT') == fingerprint(values):
            state = values.get('PAY_KEY_STATUS',state)
            try: checked_at = int(values.get('PAY_KEY_CHECKED_AT',0))
            except ValueError: pass
        return {'pay_origin':values['PAY_ORIGIN'],'demo_origin':values['DEMO_ORIGIN'],'callback_url':values['DEMO_ORIGIN']+'/notify',
                'merchant_id':values['PAY_MERCHANT_ID'],'api_version':values['PAY_API_VERSION'],
                'platform_public_key':values['PAY_PLATFORM_PUBLIC_KEY'],'md5_configured':bool(values['PAY_MD5_KEY']),
                'private_configured':bool(values['PAY_MERCHANT_PRIVATE_KEY']), 'key_configured':configured,'key_status':state,
                'key_checked_at':checked_at,'can_manage':editable,'verification_key_configured':configured}

    def candidate(self,payload,current):
        if not isinstance(payload,dict): raise ConfigurationError('invalid_request')
        result=dict(current)
        result['PAY_ORIGIN']=origin(payload.get('pay_origin',current['PAY_ORIGIN']))
        result['DEMO_ORIGIN']=origin(payload.get('demo_origin',current['DEMO_ORIGIN']),callback=True)
        mapping={'merchant_id':'PAY_MERCHANT_ID','api_version':'PAY_API_VERSION','md5_key':'PAY_MD5_KEY',
                 'merchant_private_key':'PAY_MERCHANT_PRIVATE_KEY','platform_public_key':'PAY_PLATFORM_PUBLIC_KEY'}
        for field,key in mapping.items():
            value=payload.get(field)
            if value is None: continue
            if not isinstance(value,str): raise ConfigurationError('invalid_request')
            value=value.strip()
            if value or field not in {'md5_key','merchant_private_key'}:result[key]=value
        if result['PAY_API_VERSION'] not in {'v1','v2'}: raise ConfigurationError('invalid_api_version')
        return result

    def mark_key(self,used,state):
        with self.locked():
            if fingerprint(used)==fingerprint(self.read()):
                self.write({'PAY_KEY_STATUS':state,'PAY_KEY_CHECKED_AT':int(time.time()),'PAY_KEY_FINGERPRINT':fingerprint(used)})

    def apply(self,payload,*,save):
        with self.locked():
            current=self.read(); candidate=self.candidate(payload,current)
            try:check(candidate)
            except ConnectionError as exc:
                if fingerprint(candidate)==fingerprint(current):
                    self.write({'PAY_KEY_STATUS':'invalid','PAY_KEY_CHECKED_AT':int(time.time()),'PAY_KEY_FINGERPRINT':fingerprint(current)})
                raise ConfigurationError(exc.code,422) from None
            status={'PAY_KEY_STATUS':'valid','PAY_KEY_CHECKED_AT':int(time.time()),'PAY_KEY_FINGERPRINT':fingerprint(candidate)}
            if save:
                keys=('PAY_ORIGIN','DEMO_ORIGIN','PAY_MERCHANT_ID','PAY_API_VERSION','PAY_MD5_KEY','PAY_MERCHANT_PRIVATE_KEY','PAY_PLATFORM_PUBLIC_KEY')
                self.write({**{k:candidate[k] for k in keys},**status})
            elif fingerprint(candidate)==fingerprint(current):self.write(status)
        return {**self.public(),'checked':{'pay_origin':candidate['PAY_ORIGIN'],'merchant_id':candidate['PAY_MERCHANT_ID'],'api_version':candidate['PAY_API_VERSION']}}

    def cipher(self,create=False):
        path=self.lock_file.parent/'connection-master.key'
        if not path.exists():
            if not create: raise ValueError('connection_key_missing')
            database=Path(self.defaults['DATABASE'])
            if database.exists():
                with closing(sqlite3.connect(database)) as db:
                    columns={row[1] for row in db.execute('PRAGMA table_info(orders)')}
                    if 'connection_enc' in columns and db.execute("SELECT 1 FROM orders WHERE connection_enc!='' LIMIT 1").fetchone():
                        raise ValueError('connection_key_missing_restore_backup')
            temporary=path.with_name('.connection-key-'+secrets.token_hex(8))
            fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            try:
                with os.fdopen(fd,'wb') as stream:stream.write(Fernet.generate_key());stream.flush();os.fsync(stream.fileno())
                try:os.link(temporary,path)
                except FileExistsError:pass
            finally:temporary.unlink(missing_ok=True)
        return Fernet(path.read_bytes())

    def seal(self,values):
        keys=('PAY_ORIGIN','DEMO_ORIGIN','PAY_MERCHANT_ID','PAY_API_VERSION','PAY_MD5_KEY','PAY_MERCHANT_PRIVATE_KEY','PAY_PLATFORM_PUBLIC_KEY')
        return self.cipher(True).encrypt(json.dumps({k:values.get(k,'') for k in keys}).encode()).decode()

    def open_snapshot(self,value):
        return json.loads(self.cipher().decrypt(value.encode()))
