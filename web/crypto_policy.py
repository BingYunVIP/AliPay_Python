"""Administrator scan and matching policy for newly created orders."""
import json
from decimal import Decimal
from bypay.payment_validation import money
from .payment_terms import MODES

DEFAULTS={'block_max_gap':1000,'offset_confirm':True,'lookback_hours':3,'match_mode':'exact','version':0}

class CryptoPolicy:
    def __init__(self,store,site):self.store,self.site=store,site
    def read(self,db=None):
        if db is None:
            with self.store.connect() as connection:return self.read(connection)
        row=db.execute("SELECT value FROM console_settings WHERE key='crypto_policy'").fetchone()
        site=self.site.read(db)
        return {**DEFAULTS,**(json.loads(row['value']) if row else {}),'min_amount':site['min_amount'],'max_amount':site['max_amount'],'site_version':site['version']}
    def save(self,payload):
        if not isinstance(payload,dict) or set(payload)-set(DEFAULTS)-{'min_amount','max_amount','site_version'}:raise ValueError('invalid_crypto_policy')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self.read(db);cfg={**old,**payload}
            if type(payload.get('version'))is not int or payload['version']!=old['version'] or payload.get('site_version')!=old['site_version']:raise ValueError('settings_conflict')
            if type(cfg['block_max_gap'])is not int or not 1<=cfg['block_max_gap']<=100000:raise ValueError('invalid_block_max_gap')
            if type(cfg['lookback_hours'])is not int or not 1<=cfg['lookback_hours']<=168:raise ValueError('invalid_lookback_hours')
            if type(cfg['offset_confirm'])is not bool or cfg['match_mode'] not in MODES:raise ValueError('invalid_matching_policy')
            low,high=money(cfg['min_amount']),money(cfg['max_amount'])
            if Decimal(low)>Decimal(high) or Decimal(high)>Decimal(self.site.ceiling):raise ValueError('invalid_amount_limits')
            cfg['version']=old['version']+1
            saved={key:cfg[key] for key in DEFAULTS}
            for key,value in (('crypto_policy',saved),('min_amount',low),('max_amount',high),('version',old['site_version']+1)):
                db.execute('INSERT INTO console_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value)))
        return self.read()
