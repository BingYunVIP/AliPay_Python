"""Merchant identities and Epay credential/order snapshots; no merchant private keys."""
import hashlib
import json
import os
import secrets
import time
from decimal import Decimal
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


EPAY_CHANNELS = {'alipay':'alipay','wxpay':'wechat','crypto':'crypto'}  # the Epay protocol calls WeChat Pay "wxpay"
EPAY_TYPES = {channel:name for name,channel in EPAY_CHANNELS.items()}
QUOTA_RESERVATION_TTL = 120


class Merchants:
    def __init__(self, store, site):
        self.store, self.site = store, site
        vault = store.path.parent/'merchant-master.key'
        platform = store.path.parent/'epay-platform-private.pem'
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for name, path in (('vault',vault),('platform',platform)):
                previous = db.execute('SELECT fingerprint FROM merchant_key_info WHERE name=?',(name,)).fetchone()
                if not path.exists():
                    if previous: raise ValueError('merchant_key_file_missing')
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

    @staticmethod
    def default_payment_settings(fee_rate='2.50'):
        return {'minimum_amount':'0.01','maximum_amount':'3000.00','daily_limit':'0.00','fee_rate':fee_rate,'timeout_minutes':15,'max_pending_orders':0,'load_balance':'first','cancel_limit':0,'cancel_window_minutes':60}

    def payment_settings(self, user_id, db=None):
        if db is None:
            with self.store.connect() as conn: return self.payment_settings(user_id, conn)
        group=self.store.groups.for_user(user_id,db) if hasattr(self.store,'groups') else {'service_rate':'1.50','cost_rate':'1.00'}
        group_rate=format(Decimal(group['service_rate'])+Decimal(group['cost_rate']),'.2f')
        row=db.execute('SELECT version,payload FROM merchant_payment_settings WHERE user_id=?',(user_id,)).fetchone()
        if row:
            settings=json.loads(row['payload']);settings['fee_rate']=group_rate
            return {'version':row['version'],'settings':settings}
        return {'version':0,'settings':self.default_payment_settings(group_rate)}

    def save_payment_settings(self, user_id, payload, maximum_amount, platform_timeout=1440):
        from decimal import Decimal, InvalidOperation
        fields=set(self.default_payment_settings())
        if not isinstance(payload,dict) or set(payload)!=fields|{'version'} or type(payload.get('version')) is not int: raise ValueError('invalid_merchant_payment_settings')
        value={key:payload[key] for key in fields}
        try:
            for key in ('minimum_amount','maximum_amount','daily_limit'):
                amount=Decimal(str(value[key]))
                if not amount.is_finite() or amount<0 or amount.as_tuple().exponent < -2: raise ValueError()
                value[key]=format(amount.quantize(Decimal('.01')),'f')
            fee=Decimal(str(value['fee_rate']))
            if not fee.is_finite() or fee<0 or fee>100: raise ValueError()
            value['fee_rate']=format(fee,'f')
        except (InvalidOperation,ValueError,TypeError): raise ValueError('invalid_merchant_payment_settings') from None
        if Decimal(value['minimum_amount'])<=0 or Decimal(value['maximum_amount'])<Decimal(value['minimum_amount']) or Decimal(value['maximum_amount'])>Decimal(maximum_amount): raise ValueError('invalid_merchant_payment_settings')
        if type(platform_timeout) is not int or value['timeout_minutes']>platform_timeout: raise ValueError('invalid_merchant_payment_settings')
        for key,low,high in (('timeout_minutes',1,1440),('max_pending_orders',0,100000),('cancel_limit',0,100000),('cancel_window_minutes',1,10080)):
            if type(value[key]) is not int or not low<=value[key]<=high: raise ValueError('invalid_merchant_payment_settings')
        if value['load_balance'] not in {'first','round_robin','minimum_amount'}: raise ValueError('invalid_merchant_payment_settings')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.payment_settings(user_id,db)
            if payload['version']!=current['version']: raise ValueError('merchant_payment_settings_conflict')
            group=self.store.groups.for_user(user_id,db)
            value['fee_rate']=format(Decimal(group['service_rate'])+Decimal(group['cost_rate']),'.2f')
            version=current['version']+1
            db.execute('INSERT INTO merchant_payment_settings VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET version=excluded.version,payload=excluded.payload,updated_at=excluded.updated_at',(user_id,version,json.dumps(value,separators=(',',':')),int(time.time())))
        return {'version':version,'settings':value}

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
        content = {k:parameters.get(k,'') for k in ('out_trade_no','name','money','type','notify_url','return_url','param','crypto_asset')}
        content['version'] = version
        digest = hashlib.sha256(json.dumps(content,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        from decimal import Decimal
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM merchant_orders WHERE user_id=? AND merchant_no=?',(account['user_id'],parameters['out_trade_no'])).fetchone()
            if row:
                if row['request_hash']!=digest: raise ValueError('order_conflict')
                # Replaying an Epay request after an upstream failure must reserve quota again.
                # A persisted platform order already accounts for pending/paid limits itself.
                platform_order=db.execute('SELECT 1 FROM orders WHERE out_trade_no=?',(row['platform_no'],)).fetchone()
                reservation=db.execute('SELECT expires_at FROM merchant_quota_reservations WHERE number=?',(row['platform_no'],)).fetchone()
                now=int(time.time())
                if not platform_order and (not reservation or reservation['expires_at']<=now):
                    if reservation: db.execute('DELETE FROM merchant_quota_reservations WHERE number=?',(row['platform_no'],))
                    settings=self.payment_settings(account['user_id'],db)['settings']
                    amount=Decimal(parameters['money'])
                    if amount<Decimal(settings['minimum_amount']): raise ValueError('merchant_amount_below_minimum')
                    if amount>Decimal(settings['maximum_amount']): raise ValueError('merchant_amount_above_maximum')
                    self.check_quota(db,account['user_id'],amount,settings,now)
                    db.execute('INSERT INTO merchant_quota_reservations(number,user_id,amount,created_at,expires_at) VALUES (?,?,?,?,?)',(row['platform_no'],account['user_id'],format(amount,'.2f'),now,now+QUOTA_RESERVATION_TTL))
                    db.execute("UPDATE merchant_orders SET reservation_state='active' WHERE platform_no=?",(row['platform_no'],))
                return dict(db.execute('SELECT * FROM merchant_orders WHERE platform_no=?',(row['platform_no'],)).fetchone())
            settings=self.payment_settings(account['user_id'],db)['settings']
            amount=Decimal(parameters['money'])
            if amount<Decimal(settings['minimum_amount']): raise ValueError('merchant_amount_below_minimum')
            if amount>Decimal(settings['maximum_amount']): raise ValueError('merchant_amount_above_maximum')
            now=int(time.time())
            self.check_quota(db,account['user_id'],amount,settings,now)
            number = str(int(time.time()*1000)) + str(secrets.randbelow(10**9)).zfill(9)
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO merchant_quota_reservations(number,user_id,amount,created_at,expires_at) VALUES (?,?,?,?,?)',(number,account['user_id'],format(amount,'.2f'),now,now+QUOTA_RESERVATION_TTL))
            db.execute('INSERT INTO merchant_orders(platform_no,user_id,pid,merchant_no,version,request_hash,md5_key,return_url,param,token_hash,token,created_at,amount) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(number,account['user_id'],account['pid'],parameters['out_trade_no'],version,digest,account['md5_key'],parameters.get('return_url',''),parameters.get('param',''),hashlib.sha256(token.encode()).hexdigest(),self.cipher.encrypt(token.encode()),int(time.time()),format(amount,'.2f')))
            return dict(db.execute('SELECT * FROM merchant_orders WHERE platform_no=?',(number,)).fetchone())

    def check_quota(self, db, user_id, value, settings, now=None):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from decimal import Decimal
        now=now or int(time.time())
        day_start=int(datetime.fromtimestamp(now,ZoneInfo('Asia/Shanghai')).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
        db.execute('DELETE FROM merchant_quota_reservations WHERE expires_at<=?',(now,))
        total=db.execute('''SELECT COALESCE(SUM(CAST(ROUND(CAST(amount AS NUMERIC)*100) AS INTEGER)),0)
            FROM orders WHERE user_id=? AND ((status='CREATED' AND created_at>=? AND expires_at>?) OR paid_at>=?)''',
            (user_id,day_start,now,day_start)).fetchone()[0]
        total+=db.execute('SELECT COALESCE(SUM(CAST(ROUND(CAST(amount AS NUMERIC)*100) AS INTEGER)),0) FROM merchant_quota_reservations WHERE user_id=? AND created_at>=? AND expires_at>?',(user_id,day_start,now)).fetchone()[0]
        if Decimal(settings['daily_limit'])>0 and total+int(Decimal(value)*100)>int(Decimal(settings['daily_limit'])*100): raise ValueError('merchant_daily_limit_exceeded')
        if settings['max_pending_orders']:
            pending=db.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='CREATED' AND expires_at>?",(user_id,now)).fetchone()[0]
            pending+=db.execute('SELECT COUNT(*) FROM merchant_quota_reservations WHERE user_id=? AND expires_at>?',(user_id,now)).fetchone()[0]
            if pending>=settings['max_pending_orders']: raise ValueError('merchant_pending_limit_exceeded')

    def reserve_quota(self, user_id, number, value, settings):
        from decimal import Decimal
        amount=Decimal(value)
        if amount<Decimal(settings['minimum_amount']): raise ValueError('merchant_amount_below_minimum')
        if amount>Decimal(settings['maximum_amount']): raise ValueError('merchant_amount_above_maximum')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now=int(time.time())
            row=db.execute('SELECT user_id,amount,expires_at FROM merchant_quota_reservations WHERE number=?',(number,)).fetchone()
            if row:
                if row['user_id']!=user_id or Decimal(row['amount'])!=amount: raise ValueError('order_conflict')
                if row['expires_at']>now: return
                db.execute('DELETE FROM merchant_quota_reservations WHERE number=?',(number,))
            self.check_quota(db,user_id,amount,settings,now)
            db.execute('INSERT INTO merchant_quota_reservations(number,user_id,amount,created_at,expires_at) VALUES (?,?,?,?,?)',(number,user_id,format(amount,'.2f'),now,now+QUOTA_RESERVATION_TTL))

    def reservation_state(self, number, state):
        if state not in {'active','failed'}: raise ValueError('invalid_reservation_state')
        with self.store.connect() as db: db.execute('UPDATE merchant_orders SET reservation_state=? WHERE platform_no=?',(state,number))

    def release_quota(self, number, user_id, value):
        from decimal import Decimal, InvalidOperation
        try:
            amount=Decimal(str(value))
            if not amount.is_finite() or amount<0: return False
            stored=format(amount.quantize(Decimal('.01')),'f')
        except (InvalidOperation,ValueError,TypeError):
            return False
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT user_id,amount FROM merchant_quota_reservations WHERE number=?',(number,)).fetchone()
            if not row or row['user_id']!=user_id or Decimal(row['amount'])!=amount: return False
            return db.execute('DELETE FROM merchant_quota_reservations WHERE number=? AND user_id=? AND amount=?',(number,user_id,stored)).rowcount==1

    def consume_quota(self, number, user_id=None):
        with self.store.connect() as db:
            if user_id is None: db.execute('DELETE FROM merchant_quota_reservations WHERE number=?',(number,))
            else: db.execute('DELETE FROM merchant_quota_reservations WHERE number=? AND user_id=?',(number,user_id))

    def begin_cancel(self, user_id, number, settings, now=None):
        now=int(time.time()) if now is None else int(now)
        window=int(settings['cancel_window_minutes'])*60
        limit=int(settings['cancel_limit'])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE merchant_cancel_requests SET state='failed' WHERE state='pending' AND created_at<?",(now-120,))
            row=db.execute('''SELECT o.status,o.paid_at,o.expires_at FROM merchant_orders m JOIN orders o ON o.out_trade_no=m.platform_no
                WHERE m.platform_no=? AND m.user_id=?''',(number,user_id)).fetchone()
            if not row: raise ValueError('order_not_found')
            previous=db.execute('SELECT state FROM merchant_cancel_requests WHERE platform_no=? AND user_id=?',(number,user_id)).fetchone()
            if previous and previous['state']=='confirmed' and not row['paid_at'] and row['status']=='CLOSED':
                return {'idempotent':True,'status':row['status'],'paid':False}
            if row['paid_at'] or row['status']!='CREATED' or int(row['expires_at'] or 0)<=now: raise ValueError('merchant_order_not_cancellable')
            if previous and previous['state']=='pending': raise ValueError('merchant_cancel_pending')
            if limit:
                confirmed=db.execute("SELECT COUNT(*) FROM merchant_cancel_requests WHERE user_id=? AND state='confirmed' AND created_at>=?",(user_id,now-window)).fetchone()[0]
                pending=db.execute("SELECT COUNT(*) FROM merchant_cancel_requests WHERE user_id=? AND state='pending' AND created_at>=?",(user_id,now-window)).fetchone()[0]
                if confirmed+pending>=limit: raise ValueError('merchant_cancel_limit_exceeded')
            db.execute('''INSERT INTO merchant_cancel_requests(platform_no,user_id,state,created_at,provider_state) VALUES (?,?,'pending',?,'')
                ON CONFLICT(platform_no) DO UPDATE SET user_id=excluded.user_id,state='pending',created_at=excluded.created_at,provider_state='' ''',(number,user_id,now))
            return {'idempotent':False,'status':row['status'],'paid':False}

    def finish_cancel(self, user_id, number, confirmed, provider_state='', now=None):
        now=int(time.time()) if now is None else int(now)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('''SELECT o.status,o.paid_at FROM merchant_orders m JOIN orders o ON o.out_trade_no=m.platform_no
                WHERE m.platform_no=? AND m.user_id=?''',(number,user_id)).fetchone()
            request=db.execute("SELECT state FROM merchant_cancel_requests WHERE platform_no=? AND user_id=?",(number,user_id)).fetchone()
            if not row or not request or request['state']!='pending': return False
            if confirmed and not row['paid_at'] and row['status']=='CREATED':
                changed=db.execute("UPDATE orders SET status='CLOSED',provider_status=? WHERE out_trade_no=? AND user_id=? AND status='CREATED' AND paid_at IS NULL",(provider_state,number,user_id)).rowcount
                if changed:
                    db.execute("UPDATE merchant_cancel_requests SET state='confirmed',provider_state=? WHERE platform_no=? AND user_id=? AND state='pending'",(provider_state,number,user_id))
                    db.execute("UPDATE payment_checks SET state='closed',locked_until=0 WHERE out_trade_no=?",(number,))
                    db.execute("INSERT INTO events(out_trade_no,accepted,reason,created_at,stage,user_id) VALUES (?,1,'merchant_order_cancelled',?,'order.updated',?)",(number,now,user_id))
                    return True
                return False
            state='superseded' if confirmed else 'failed'
            db.execute("UPDATE merchant_cancel_requests SET state=?,provider_state=? WHERE platform_no=? AND user_id=? AND state='pending'",(state,provider_state,number,user_id))
            return False

    def select_config(self, user_id, channel, channels, requested=None, crypto_asset=None):
        if requested is not None: return requested
        strategy=self.payment_settings(user_id)['settings']['load_balance']
        candidates=sorted((item for item in channels.all() if item['id']==channel and item['enabled'] and item['configured'] and item['integrated']),key=lambda item:item['config_id'])
        if channel=='crypto' and crypto_asset:
            code=str(crypto_asset).upper()
            candidates=[item for item in candidates if f"{item['fields'].get('network','')}_{item['fields'].get('asset','')}".upper()==code]
            if not candidates:raise ValueError('crypto_asset_configuration_missing')
        if not candidates: return None
        if strategy=='first' or len(candidates)==1: return candidates[0]['config_id']
        with self.store.connect() as db:
            if strategy=='round_robin':
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT config_id FROM merchant_payment_cursors WHERE user_id=? AND channel=?',(user_id,channel)).fetchone()
                previous=row['config_id'] if row else 0
                chosen=next((item for item in candidates if item['config_id']>previous),candidates[0])
                db.execute('INSERT INTO merchant_payment_cursors VALUES (?,?,?) ON CONFLICT(user_id,channel) DO UPDATE SET config_id=excluded.config_id',(user_id,channel,chosen['config_id']))
                return chosen['config_id']
            since=int(time.time())-86400
            totals={row['payment_config_id']:row['gross'] for row in db.execute("SELECT payment_config_id,COALESCE(SUM(CAST(ROUND(CAST(amount AS NUMERIC)*100) AS INTEGER)),0) gross FROM orders WHERE user_id=? AND channel=? AND created_at>=? GROUP BY payment_config_id",(user_id,channel,since))}
        return min(candidates,key=lambda item:(totals.get(item['config_id'],0),item['config_id']))['config_id']

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
        fields = {'pid':record['pid'],'trade_no':number,'out_trade_no':record['merchant_no'],'type':EPAY_TYPES.get(order['channel'],order['channel']),
                  'name':order['subject'],'money':order['amount'],'order_amount':order['amount'],'order_currency':'CNY','payment_amount':order['crypto_amount'] if order['channel']=='crypto' else order['amount'],'payment_currency':order['crypto_asset'] if order['channel']=='crypto' else 'CNY','trade_status':'TRADE_SUCCESS'}
        if order['channel'] == 'crypto':
            fields.update(self._crypto_result_fields(order))
        if record['param']: fields['param']=record['param']
        if record['version']=='v2':
            fields['api_trade_no']=order['trade_no'] or ''
            return signed(fields,self.platform,'RSA')
        return signed(fields,self.secret(record),'MD5')

    def query(self, order):
        record = self.order(order['out_trade_no'])
        result = {'trade_no':record['platform_no'],'out_trade_no':record['merchant_no'],'pid':record['pid'],
                'api_trade_no':order['trade_no'] or '', 'type':EPAY_TYPES.get(order['channel'],order['channel']),'name':order['subject'],'money':order['amount'],'order_amount':order['amount'],'order_currency':'CNY','payment_amount':order['crypto_amount'] if order['channel']=='crypto' else order['amount'],'payment_currency':order['crypto_asset'] if order['channel']=='crypto' else 'CNY',
                'param':record['param'],'status':1 if order['paid_at'] else 0,'state':'EXPIRED' if order['status']=='CREATED' and order['expires_at']<=int(time.time()) else order['status'],
                'addtime':time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(order['created_at'])),
                'endtime':time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(order['paid_at'])) if order['paid_at'] else ''}
        if order['channel'] == 'crypto':
            result.update(self._crypto_result_fields(order))
        return result

    @staticmethod
    def _crypto_result_fields(order):
        return {'network':order.get('crypto_network') or '', 'asset':order.get('crypto_asset') or '',
                'crypto_amount':order.get('crypto_amount') or '', 'crypto_txid':order.get('crypto_txid') or ''}
