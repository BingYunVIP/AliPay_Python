"""Merchant identities and Epay credential/order snapshots; no merchant private keys."""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bypay.epay_protocol import private_key, public_pem, signed


def persist_once(path, data):
    path = Path(path)
    temp = path.with_name(path.name + '.' + secrets.token_hex(8))
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        try: os.link(temp, path)
        except FileExistsError: pass
    finally: temp.unlink(missing_ok=True)


class Merchants:
    def __init__(self, store, site):
        self.store, self.site = store, site
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS merchant_accounts (
                  pid INTEGER PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
                  md5_key BLOB NOT NULL, public_key TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'both', created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS merchant_orders (
                  platform_no TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), pid INTEGER NOT NULL REFERENCES merchant_accounts(pid),
                  merchant_no TEXT NOT NULL, version TEXT NOT NULL, request_hash TEXT NOT NULL,
                  md5_key BLOB NOT NULL, return_url TEXT NOT NULL, param TEXT NOT NULL DEFAULT '',
                  token_hash TEXT UNIQUE NOT NULL, token BLOB NOT NULL, created_at INTEGER NOT NULL,
                  UNIQUE(user_id,merchant_no));
                CREATE TABLE IF NOT EXISTS merchant_key_info (name TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
            ''')
        vault = store.path.parent/'merchant-master.key'
        platform = store.path.parent/'epay-platform-private.pem'
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for name, path in (('vault',vault),('platform',platform)):
                previous = db.execute('SELECT fingerprint FROM merchant_key_info WHERE name=?',(name,)).fetchone()
                if not path.exists():
                    if previous: raise ValueError('merchant_key_file_missing_restore_backup')
                    data = Fernet.generate_key() if name == 'vault' else rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
                    persist_once(path,data)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if previous and previous['fingerprint'] != digest: raise ValueError('merchant_key_file_mismatch')
                db.execute('INSERT OR IGNORE INTO merchant_key_info VALUES (?,?)',(name,digest))
        self.cipher = Fernet(vault.read_bytes())
        self.platform = private_key(platform.read_text())
        with store.connect() as db: users = [r['id'] for r in db.execute('SELECT id FROM users ORDER BY id')]
        for user_id in users: self.ensure(user_id)
        store.merchants = self

    def ensure(self, user_id):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM merchant_accounts WHERE user_id=?',(user_id,)).fetchone()
            if not row:
                pid = db.execute('SELECT COALESCE(MAX(pid),999)+1 FROM merchant_accounts').fetchone()[0]
                db.execute('INSERT INTO merchant_accounts(pid,user_id,md5_key,created_at) VALUES (?,?,?,?)',(pid,user_id,self.cipher.encrypt(secrets.token_hex(16).encode()),int(time.time())))
                row = db.execute('SELECT * FROM merchant_accounts WHERE user_id=?',(user_id,)).fetchone()
        return dict(row)

    def account(self, pid):
        with self.store.connect() as db:
            row = db.execute('SELECT m.*,u.disabled,u.email FROM merchant_accounts m JOIN users u ON u.id=m.user_id WHERE m.pid=?',(pid,)).fetchone()
        return dict(row) if row else None

    def secret(self, record): return self.cipher.decrypt(record['md5_key']).decode()

    def describe(self, user_id):
        row = self.ensure(user_id)
        return {'merchant_id':row['pid'],'interface_url':self.site.read()['origin'], 'md5_key':self.secret(row),
                'platform_public_key':public_pem(self.platform),'merchant_public_key':row['public_key'],'signature_mode':row['mode']}

    def update(self, user_id, payload):
        if not isinstance(payload,dict) or set(payload)-{'rotate_md5','merchant_public_key','signature_mode'}: raise ValueError('invalid_merchant_settings')
        self.ensure(user_id)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = dict(db.execute('SELECT * FROM merchant_accounts WHERE user_id=?',(user_id,)).fetchone())
            key = public_pem(payload['merchant_public_key']) if 'merchant_public_key' in payload else row['public_key']
            mode = payload.get('signature_mode',row['mode'])
            if mode not in {'both','rsa'} or (mode=='rsa' and not key): raise ValueError('merchant_public_key_required')
            if 'rotate_md5' in payload and type(payload['rotate_md5']) is not bool: raise ValueError('invalid_merchant_settings')
            secret = self.cipher.encrypt(secrets.token_hex(16).encode()) if payload.get('rotate_md5') else row['md5_key']
            db.execute('UPDATE merchant_accounts SET public_key=?,mode=?,md5_key=? WHERE user_id=?',(key,mode,secret,user_id))
        return self.describe(user_id)

    def order(self, number):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM merchant_orders WHERE platform_no=?',(number,)).fetchone()
        return dict(row) if row else None

    def resolve(self, account, parameters):
        with self.store.connect() as db:
            if parameters.get('trade_no'):
                row = db.execute('SELECT platform_no FROM merchant_orders WHERE user_id=? AND platform_no=?',(account['user_id'],parameters['trade_no'])).fetchone()
            else:
                row = db.execute('SELECT platform_no FROM merchant_orders WHERE user_id=? AND merchant_no=?',(account['user_id'],parameters.get('out_trade_no',''))).fetchone()
        return self.store.order(row['platform_no']) if row else None

    def reserve(self, account, parameters, version):
        content = {k:parameters.get(k,'') for k in ('out_trade_no','name','money','type','notify_url','return_url','param')}
        content['version'] = version
        digest = hashlib.sha256(json.dumps(content,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM merchant_orders WHERE user_id=? AND merchant_no=?',(account['user_id'],parameters['out_trade_no'])).fetchone()
            if row:
                if row['request_hash']!=digest: raise ValueError('order_conflict')
                return dict(row)
            number = str(int(time.time()*1000)) + str(secrets.randbelow(10**9)).zfill(9)
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO merchant_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(number,account['user_id'],account['pid'],parameters['out_trade_no'],version,digest,account['md5_key'],parameters.get('return_url',''),parameters.get('param',''),hashlib.sha256(token.encode()).hexdigest(),self.cipher.encrypt(token.encode()),int(time.time())))
            return dict(db.execute('SELECT * FROM merchant_orders WHERE platform_no=?',(number,)).fetchone())

    def checkout_url(self, record, origin=None):
        return (origin or self.site.read()['origin'])+'/cashier/'+self.cipher.decrypt(record['token']).decode()

    def cashier(self, token):
        if not isinstance(token,str) or not 40<=len(token)<=128: return None
        with self.store.connect() as db:
            row = db.execute('SELECT m.* FROM merchant_orders m JOIN users u ON u.id=m.user_id WHERE m.token_hash=? AND u.disabled=0',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row: return None
        order = self.store.order(row['platform_no'])
        if not order or order.get('deleted_at') or int(time.time())>max(order['expires_at'],order['paid_at'] or 0)+86400: return None
        return dict(row),order

    def notification(self, number):
        record, order = self.order(number), self.store.order(number)
        if not record or not order or not order['paid_at']: raise ValueError('order_not_paid')
        fields = {'pid':record['pid'],'trade_no':number,'out_trade_no':record['merchant_no'],'type':order['channel'],
                  'name':order['subject'],'money':order['amount'],'trade_status':'TRADE_SUCCESS'}
        if record['param']: fields['param']=record['param']
        if record['version']=='v2':
            fields['api_trade_no']=order['trade_no'] or ''
            return signed(fields,self.platform,'RSA')
        return signed(fields,self.secret(record),'MD5')

    def query(self, order):
        record = self.order(order['out_trade_no'])
        return {'trade_no':record['platform_no'],'out_trade_no':record['merchant_no'],'pid':record['pid'],
                'api_trade_no':order['trade_no'] or '', 'type':order['channel'],'name':order['subject'],'money':order['amount'],
                'param':record['param'],'status':1 if order['paid_at'] else 0,'state':'EXPIRED' if order['status']=='CREATED' and order['expires_at']<=int(time.time()) else order['status'],
                'addtime':time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(order['created_at'])),
                'endtime':time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(order['paid_at'])) if order['paid_at'] else ''}
