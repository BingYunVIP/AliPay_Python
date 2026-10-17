"""Independent merchant: Epay V1/V2 signing, embedded Pay cashier and verified receipts."""
import base64
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

from configuration import Configuration, ConfigurationError, local_hosts
from connection import TYPES, ConnectionError, call, create, verify_notification

ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env')


def create_app(settings=None):
    app=Flask(__name__,static_folder=None)
    defaults={'PAY_ORIGIN':'http://127.0.0.1:888','DEMO_ORIGIN':'http://127.0.0.1:8100','DEMO_PORT':'8100',
              'PAY_MERCHANT_ID':'','PAY_API_VERSION':'v1','PAY_MD5_KEY':'','PAY_MERCHANT_PRIVATE_KEY':'','PAY_PLATFORM_PUBLIC_KEY':'',
              'PAY_PUBLIC_KEY_FILE':str(ROOT/'secrets/bypay-public.pem'),'DATABASE':str(ROOT/'data/demo.sqlite3')}
    app.config.update({k:os.getenv(k,v) for k,v in defaults.items()},MAX_CONTENT_LENGTH=32768,DEMO_SETUP_TOKEN=os.getenv('DEMO_SETUP_TOKEN',secrets.token_urlsafe(32)))
    app.config.update(settings or {})
    port=int(app.config['DEMO_PORT']);database=Path(app.config['DATABASE'])
    database.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    source=app.config.get('CONFIG_FILE',database.parent/'.env' if app.testing else ROOT/'.env')
    config=Configuration(source,app.config,database.parent/'config.lock');initial=config.read()
    app.extensions['configuration']=config
    allowed_origins={initial['DEMO_ORIGIN']}|{f"http://{'['+h+']' if ':' in h else h}:{port}" for h in local_hosts()}

    @contextmanager
    def db():
        connection=sqlite3.connect(database,timeout=15);connection.row_factory=sqlite3.Row
        try:
            with connection:yield connection
        finally:connection.close()

    with db() as connection:
        connection.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS orders(number TEXT PRIMARY KEY,amount TEXT NOT NULL,status TEXT NOT NULL,pay_url TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,trade_no TEXT,paid_at INTEGER);
          CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,body_hash TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY,time INTEGER NOT NULL,stage TEXT NOT NULL,number TEXT,detail TEXT NOT NULL);''')
        columns={r[1] for r in connection.execute('PRAGMA table_info(orders)')}
        for name,definition in {'public_key_pem':"TEXT NOT NULL DEFAULT ''",'platform_no':"TEXT NOT NULL DEFAULT ''",'api_version':"TEXT NOT NULL DEFAULT 'legacy'",'merchant_id':"TEXT NOT NULL DEFAULT ''",'connection_enc':"TEXT NOT NULL DEFAULT ''",'last_query_at':'INTEGER NOT NULL DEFAULT 0','confirmed_by':"TEXT NOT NULL DEFAULT ''",'pay_type':"TEXT NOT NULL DEFAULT 'alipay'"}.items():
            if name not in columns:connection.execute(f'ALTER TABLE orders ADD COLUMN {name} {definition}')
    database.chmod(0o600)

    def can_manage():
        token=request.headers.get('X-Demo-Setup','')
        if token and hmac.compare_digest(token,app.config['DEMO_SETUP_TOKEN']):return True
        try:peer=ipaddress.ip_address(request.remote_addr or '');local_peer=peer.is_loopback or peer.is_private
        except ValueError:local_peer=False
        return local_peer and request.scheme=='http' and urlsplit(request.host_url).hostname in local_hosts() and not any(k.lower()=='forwarded' or k.lower().startswith('x-forwarded-') for k in request.headers.keys())

    def log(stage,number=None,detail=None):
        detail=detail or {}
        with db() as connection:
            connection.execute('INSERT INTO logs(time,stage,number,detail) VALUES (?,?,?,?)',(int(time.time()),stage,number,json.dumps(detail,ensure_ascii=False)))
            connection.execute('DELETE FROM logs WHERE id < (SELECT COALESCE(MAX(id),0)-5000 FROM logs)')
        record=json.dumps({'time':int(time.time()),'stage':stage,'number':number,'detail':detail},ensure_ascii=False)
        directory=database.parent/'logs';directory.mkdir(parents=True,exist_ok=True,mode=0o700);path=directory/'demo.jsonl'
        with (directory/'demo.lock').open('a') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            if path.exists() and path.stat().st_size>5_000_000:path.replace(path.with_suffix('.jsonl.1'))
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(fd,'a') as stream:stream.write(record+'\n')
        print(record,flush=True)

    def order(number):
        with db() as connection:row=connection.execute('SELECT * FROM orders WHERE number=?',(number,)).fetchone()
        return dict(row) if row else None

    def public(row):
        return {k:row[k] for k in ('number','amount','status','pay_url','created_at','paid_at','platform_no','api_version','merchant_id','confirmed_by','pay_type')}

    @app.before_request
    def browser_guard():
        if request.path=='/notify':return
        if request.method=='POST' and (request.headers.get('Origin') not in allowed_origins|{config.read()['DEMO_ORIGIN']} or request.headers.get('X-Demo-Request')!='1' or not request.is_json):
            return jsonify(error='invalid_request'),403

    @app.after_request
    def headers(response):
        origin=config.read()['PAY_ORIGIN']
        parsed=urlsplit(origin);frames={origin}
        if parsed.scheme=='http' and parsed.hostname in {'localhost','127.0.0.1','::1'}:
            suffix=':'+str(parsed.port) if parsed.port else ''
            frames.update('http://'+host+suffix for host in ('localhost','127.0.0.1','[::1]'))
        frame_sources=' '.join(sorted(frames))
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                                 'Content-Security-Policy':f"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src {frame_sources}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        return response

    @app.get('/health')
    def health():return jsonify(ok=True)

    @app.get('/')
    @app.get('/return')
    def home():
        if request.path=='/return':log('return.received',detail={'note':'浏览器返回不作为到账凭证'})
        return send_from_directory(ROOT/'static','index.html')

    @app.get('/static/<path:path>')
    def static_files(path):return send_from_directory(ROOT/'static',path)

    @app.get('/api/state')
    def state():
        with db() as connection:
            orders=[public(dict(r)) for r in connection.execute('SELECT * FROM orders ORDER BY created_at DESC,rowid DESC LIMIT 30')]
            logs=[{**dict(r),'detail':json.loads(r['detail'])} for r in connection.execute('SELECT * FROM logs ORDER BY id DESC LIMIT 80')]
        return jsonify(orders=orders,logs=logs,**config.public(editable=can_manage()))

    @app.get('/api/config')
    def get_config():return jsonify(**config.public(editable=can_manage()),local_pay_origin='http://127.0.0.1:888',local_demo_origin=f'http://127.0.0.1:{port}')

    @app.post('/api/config')
    @app.post('/api/config/check')
    def update_config():
        if not can_manage():return jsonify(error='configuration_access_required'),403
        save=request.path=='/api/config'
        try:verified=config.apply(request.get_json(silent=True),save=save)
        except ConfigurationError as exc:
            log('config.rejected',detail={'error':exc.code,'saved':False})
            return jsonify(error=exc.code,saved=False),exc.http_status
        except OSError:
            return jsonify(error='config_write_failed',saved=False),500
        result=config.public(editable=True)
        log('config.saved' if save else 'config.checked',detail={**verified['checked'],'saved':save})
        return jsonify(**result,saved=save,verified=True)

    @app.post('/api/orders')
    def create_order():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict):return jsonify(error='invalid_request'),400
        amount=payload.get('amount','')
        if not isinstance(amount,str) or not re.fullmatch(r'\d{1,2}(?:\.\d{1,2})?',amount) or not Decimal('0')<Decimal(amount)<=Decimal('10'):
            return jsonify(error='amount_between_001_and_10'),400
        pay_type=payload.get('type','alipay')  # an older page that sends no type still pays with Alipay
        if not isinstance(pay_type,str) or pay_type not in TYPES:return jsonify(error='invalid_pay_type'),400
        settings=config.read();amount=format(Decimal(amount),'.2f')
        number='DEMO_'+str(int(time.time()))+'_'+secrets.token_hex(6)
        try:
            from connection import credentials
            credentials(settings)
        except ConnectionError as exc:return jsonify(error=exc.code),400
        with db() as connection:
            connection.execute("INSERT INTO orders(number,amount,status,created_at,api_version,merchant_id,connection_enc,pay_type) VALUES (?,?,'CREATING',?,?,?,?,?)",(number,amount,int(time.time()),settings['PAY_API_VERSION'],settings['PAY_MERCHANT_ID'],config.seal(settings),pay_type))
        log('create.request',number,{'merchant_id':settings['PAY_MERCHANT_ID'],'api_version':settings['PAY_API_VERSION'],'type':pay_type,'amount':amount,'notify_url':settings['DEMO_ORIGIN']+'/notify'})
        try:
            result=create(settings,number,amount,'独立商户支付测试',request.remote_addr or '127.0.0.1',pay_type)
            with db() as connection:
                row=connection.execute('SELECT platform_no FROM orders WHERE number=?',(number,)).fetchone()
                if row['platform_no'] and row['platform_no']!=result['trade_no']:raise ConnectionError('invalid_checkout_response')
                connection.execute("UPDATE orders SET status=CASE WHEN status='CREATING' THEN 'WAITING' ELSE status END,pay_url=?,platform_no=? WHERE number=?",(result['pay_url'],result['trade_no'],number))
        except ConnectionError as exc:
            with db() as connection:connection.execute("UPDATE orders SET status='CREATE_FAILED' WHERE number=? AND status='CREATING'",(number,))
            if exc.code in {'invalid_signature','merchant_unavailable','rsa_required'}:config.mark_key(settings,'invalid')
            log('create.failed',number,{'error':exc.code})
            return jsonify(error=exc.code),502
        log('create.accepted',number,{'platform_no':result['trade_no'],'type':pay_type,'amount':amount,'cashier_origin':settings['PAY_ORIGIN']})
        return jsonify(order=public(order(number))),201

    def confirm(row,params,source):
        number=row['number'];trade=str(params.get('trade_no',''))
        # the payment type must be the one this order was created with: an Alipay notice never settles a WeChat order, nor the other way round
        if not re.fullmatch(r'[A-Za-z0-9_-]{6,64}',trade) or params.get('out_trade_no')!=number or str(params.get('pid'))!=row['merchant_id'] or params.get('type')!=row['pay_type'] or Decimal(str(params.get('money','')))!=Decimal(row['amount']):raise ValueError('order_mismatch')
        if row['platform_no'] and row['platform_no']!=trade:raise ValueError('platform_order_mismatch')
        canonical=json.dumps({'pid':row['merchant_id'],'trade_no':trade,'out_trade_no':number,'money':row['amount'],'type':row['pay_type']},sort_keys=True)
        receipt='epay:'+row['merchant_id']+':'+trade
        digest=hashlib.sha256(canonical.encode()).hexdigest()
        with db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            old=connection.execute('SELECT body_hash FROM receipts WHERE id=?',(receipt,)).fetchone()
            if old:
                if old['body_hash']!=digest:raise ValueError('receipt_mismatch')
                return False
            connection.execute("UPDATE orders SET status='PAID',platform_no=?,trade_no=?,paid_at=COALESCE(paid_at,?),confirmed_by=? WHERE number=?",(trade,params.get('api_trade_no',trade),int(time.time()),source,number))
            connection.execute('INSERT INTO receipts VALUES (?,?)',(receipt,digest))
        return True

    @app.post('/api/orders/<number>/sync')
    def sync_order(number):
        row=order(number)
        if not row:return jsonify(error='order_not_found'),404
        if row['status']!='WAITING' or not row['connection_enc']:return jsonify(order=public(row))
        with db() as connection:
            acquired=connection.execute('UPDATE orders SET last_query_at=? WHERE number=? AND last_query_at<=?',(int(time.time()),number,int(time.time())-3)).rowcount
        if not acquired:return jsonify(order=public(row))
        try:
            settings=config.open_snapshot(row['connection_enc'])
            result=call(settings,'query',{'out_trade_no':number})
            if result.get('status')==1:
                if confirm(row,result,'query'):log('query.verified',number,{'platform_no':result['trade_no'],'status':'PAID'})
            elif result.get('state') in {'EXPIRED','CLOSED'}:
                with db() as connection:connection.execute("UPDATE orders SET status=? WHERE number=? AND status='WAITING'",(result['state'],number))
        except (ConnectionError,ValueError,InvalidOperation,InvalidToken) as exc:
            code=exc.code if isinstance(exc,ConnectionError) else 'query_verification_failed'
            log('query.rejected',number,{'error':code});return jsonify(error=code),502
        return jsonify(order=public(order(number)))

    def legacy_notify():
        raw=request.get_data();stamp=request.headers.get('X-ByPay-Timestamp','')
        if abs(time.time()-int(stamp))>300:raise ValueError('expired_timestamp')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('invalid_event')
        row=order(value.get('out_trade_no',''))
        if not row or row['api_version']!='legacy':raise ValueError('not_legacy_order')
        pem=row['public_key_pem'] or config.read().get('PAY_PUBLIC_KEY_PEM') or Path(app.config['PAY_PUBLIC_KEY_FILE']).read_text()
        key=serialization.load_pem_public_key(pem.encode())
        if not isinstance(key,Ed25519PublicKey):raise ValueError('invalid_key')
        key.verify(base64.b64decode(request.headers.get('X-ByPay-Signature',''),validate=True),stamp.encode()+b'.'+raw)
        if value.get('id')!=request.headers.get('X-ByPay-Event-Id') or value.get('event')!='payment.paid' or value.get('status')!='PAID' or value.get('currency')!='CNY' or value.get('channel')!='alipay' or Decimal(value['amount'])!=Decimal(row['amount']) or not re.fullmatch(r'[A-Za-z0-9_-]{6,64}',str(value.get('trade_no',''))) or type(value.get('paid_at'))is not int or value['paid_at']<=0:raise ValueError('invalid_event')
        with db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous=connection.execute('SELECT body_hash FROM receipts WHERE id=?',(value['id'],)).fetchone();digest=hashlib.sha256(raw).hexdigest()
            if previous:
                if previous['body_hash']!=digest:raise ValueError('receipt_mismatch')
                return
            if row['trade_no'] and row['trade_no']!=value['trade_no']:raise ValueError('trade_mismatch')
            connection.execute("UPDATE orders SET status='PAID',trade_no=?,paid_at=?,confirmed_by='legacy_notify' WHERE number=?",(value['trade_no'],value['paid_at'],row['number']))
            connection.execute('INSERT INTO receipts VALUES (?,?)',(value['id'],digest))
        log('notify.verified',row['number'],{'api_version':'legacy','status':'PAID'})

    @app.route('/notify',methods=['GET','POST'])
    def notify():
        try:
            if request.is_json:
                legacy_notify()
            else:
                fields=request.args if request.method=='GET' else request.form
                if not fields or any(len(fields.getlist(k))!=1 for k in fields):raise ValueError('invalid_fields')
                params=fields.to_dict();row=order(params.get('out_trade_no',''))
                if not row or not row['connection_enc']:raise ValueError('order_not_found')
                verify_notification(config.open_snapshot(row['connection_enc']),params)
                fresh=confirm(row,params,'notify')
                log('notify.verified' if fresh else 'notify.duplicate',row['number'],{'api_version':row['api_version'],'platform_no':params['trade_no'],'status':'PAID'})
        except (ValueError,InvalidOperation,OSError,InvalidSignature,InvalidToken,TypeError,KeyError):
            log('notify.rejected',detail={'reason':'签名、时间、商户、订单或金额校验失败'})
            return 'failure',400,{'Content-Type':'text/plain'}
        return 'success',200,{'Content-Type':'text/plain'}

    log('app.started',detail={'pay_origin':initial['PAY_ORIGIN'],'callback_url':initial['DEMO_ORIGIN']+'/notify','api_version':initial['PAY_API_VERSION']})
    return app
