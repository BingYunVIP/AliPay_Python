"""Persistent market snapshots and independent fixed payment quotations."""
import json
import secrets
import time
from urllib.parse import urlsplit

import httpx

from .payment_terms import two,decimal

DEFAULTS={'fixed_rates':{'USDT':'','USDC':''},'interval_seconds':60,'history_days':30,
    'api_url':'https://api.coingecko.com','api_key':'','version':0}

class Pricing:
    def __init__(self,store,cipher,proxy_url=''):
        self.store,self.cipher=store,cipher
        # Explicit server setting: never inherit ALL_PROXY or forward a key to
        # another price provider. TLS still verifies the configured provider.
        self.proxy_url=proxy_url or None
        if self.proxy_url:
            parsed=urlsplit(self.proxy_url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.query or parsed.fragment:
                raise ValueError('invalid_market_proxy')

    def settings(self,db=None,private=False):
        if db is None:
            with self.store.connect() as connection:return self.settings(connection,private)
        row=db.execute("SELECT value FROM console_settings WHERE key='pricing'").fetchone()
        data={**DEFAULTS,'fixed_rates':dict(DEFAULTS['fixed_rates'])}
        if row:data.update(json.loads(self.cipher.decrypt(json.loads(row['value']).encode())))
        if not private:
            data['api_key_saved']=bool(data.pop('api_key',''))
        return data

    def save(self,payload):
        if not isinstance(payload,dict) or set(payload)-set(DEFAULTS):raise ValueError('invalid_pricing_settings')
        old=self.settings(private=True)
        if type(payload.get('version'))is not int or payload['version']!=old['version']:raise ValueError('settings_conflict')
        cfg={**old,**payload};fixed=cfg['fixed_rates']
        if not isinstance(fixed,dict) or set(fixed)!={'USDT','USDC'}:raise ValueError('invalid_fixed_rates')
        cfg['fixed_rates']={asset:two(value) if value!='' else '' for asset,value in fixed.items()}
        if any(value and decimal(value)<=0 for value in cfg['fixed_rates'].values()):raise ValueError('invalid_fixed_rates')
        if type(cfg['interval_seconds'])is not int or not 30<=cfg['interval_seconds']<=120:raise ValueError('invalid_sync_interval')
        if type(cfg['history_days'])is not int or not 1<=cfg['history_days']<=365:raise ValueError('invalid_history_days')
        if not isinstance(cfg['api_url'],str):raise ValueError('invalid_price_source')
        parsed=urlsplit(cfg['api_url'])
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None,443):raise ValueError('invalid_price_source')
        import ipaddress,socket
        try:addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
        except OSError:raise ValueError('price_source_dns_unavailable') from None
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):raise ValueError('invalid_price_source')
        if not isinstance(cfg['api_key'],str) or len(cfg['api_key'])>512 or any(c in cfg['api_key'] for c in '\r\n'):raise ValueError('invalid_api_key')
        # Retain a masked key only for the exact same provider URL.
        cfg['api_url']=cfg['api_url'].rstrip('/')
        if not cfg['api_key']:cfg['api_key']=old['api_key'] if cfg['api_url']==old['api_url'].rstrip('/') else ''
        cfg['version']=old['version']+1
        encrypted=self.cipher.encrypt(json.dumps(cfg).encode()).decode()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.settings(db,True)['version']!=old['version']:raise ValueError('settings_conflict')
            db.execute("INSERT INTO console_settings VALUES ('pricing',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(encrypted),))
        return self.settings()

    def market(self,asset,db=None):
        if asset not in ('USDT','USDC'):raise ValueError('invalid_asset')
        if db is None:
            with self.store.connect() as connection:return self.market(asset,connection)
        row=db.execute('SELECT * FROM rate_snapshots WHERE asset=? ORDER BY id DESC LIMIT 1',(asset,)).fetchone()
        if not row or not 0<=int(time.time())-row['quoted_at']<=180:return None
        return dict(row)

    def quote(self,asset):
        with self.store.connect() as db:
            cfg=self.settings(db,True);market=self.market(asset,db)
        fixed=cfg['fixed_rates'].get(asset,'')
        if not fixed and not market:raise ValueError('crypto_rate_unavailable')
        return {'rate':fixed or market['rate'],'fixed_rate':fixed,'market_rate':market['rate'] if market else '',
            'source':'fixed' if fixed else market['source'],'updated_at':int(time.time()) if fixed else market['quoted_at'],
            'observed_at':int(time.time()),'market_updated_at':market['quoted_at'] if market else 0}

    def sync(self):
        cfg=self.settings(private=True);now=int(time.time());lease=secrets.token_hex(12)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT OR IGNORE INTO task_runs(name) VALUES ('pricing')")
            if not db.execute("UPDATE task_runs SET locked_until=?,status=?,started_at=? WHERE name='pricing' AND locked_until<=?",(now+25,lease,now,now)).rowcount:raise ValueError('rate_sync_in_progress')
        try:
            headers={'x-cg-demo-api-key':cfg['api_key']} if cfg['api_key'] else {}
            response=httpx.get(cfg['api_url']+'/api/v3/simple/price',params={'ids':'tether,usd-coin','vs_currencies':'cny','include_last_updated_at':'true'},headers=headers,timeout=10,follow_redirects=False,trust_env=False,proxy=self.proxy_url)
            response.raise_for_status();data=response.json();values=[]
            for asset,key in (('USDT','tether'),('USDC','usd-coin')):
                item=data[key];quoted=int(item['last_updated_at']);rate=two(item['cny'])
                if decimal(rate)<=0 or not 0<=now-quoted<=180:raise ValueError('stale_market_quote')
                values.append((asset,rate,quoted,now,cfg['api_url']))
            with self.store.connect() as db:
                db.executemany('INSERT INTO rate_snapshots(asset,rate,quoted_at,observed_at,source) VALUES (?,?,?,?,?)',values)
                db.execute('INSERT INTO rate_sync_runs(created_at,success,message) VALUES (?,1,?)',(now,'行情同步成功'))
                db.execute('DELETE FROM rate_sync_runs WHERE created_at<?',(now-cfg['history_days']*86400,))
                db.execute('DELETE FROM rate_snapshots WHERE observed_at<?',(now-cfg['history_days']*86400,))
                db.execute("UPDATE task_runs SET locked_until=0,status='success',finished_at=?,error='' WHERE name='pricing' AND status=?",(int(time.time()),lease))
            return {'synced':True,'rates':{v[0]:v[1] for v in values}}
        except Exception as exc:
            # Keep provider response bodies, URLs and proxy credentials out of
            # persistent history and API errors.
            if isinstance(exc,httpx.HTTPStatusError):code='market_http_'+str(exc.response.status_code)
            elif isinstance(exc,httpx.ProxyError):code='market_proxy_unavailable'
            elif isinstance(exc,httpx.TimeoutException):code='market_request_timeout'
            elif isinstance(exc,httpx.ConnectError):code='market_connection_failed'
            elif isinstance(exc,(KeyError,TypeError,ValueError)):
                code='stale_market_quote' if str(exc)=='stale_market_quote' else 'market_invalid_response'
            else:code='market_source_unavailable'
            with self.store.connect() as db:
                db.execute('INSERT INTO rate_sync_runs(created_at,success,message) VALUES (?,0,?)',(now,code))
                db.execute("UPDATE task_runs SET locked_until=0,status='failed',finished_at=?,error=? WHERE name='pricing' AND status=?",(int(time.time()),code,lease))
            raise ValueError(code) from None

    def tick(self):
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE role='admin' AND must_change_credentials=1").fetchone():return False
            last=db.execute('SELECT MAX(created_at) FROM rate_sync_runs').fetchone()[0] or 0
        if int(time.time())-last<self.settings()['interval_seconds']:return False
        self.sync();return True

    def history(self,page=1):
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM rate_sync_runs ORDER BY id DESC LIMIT 20 OFFSET ?',((page-1)*20,))]
            total=db.execute('SELECT COUNT(*) FROM rate_sync_runs').fetchone()[0]
        return {'records':rows,'total':total,'rates':{asset:self.market(asset) for asset in ('USDT','USDC')}}
