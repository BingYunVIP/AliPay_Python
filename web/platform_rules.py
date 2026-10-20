"""Validated platform policy with explicit defaults and optimistic updates."""
import ipaddress
import json
import re
from decimal import Decimal,InvalidOperation

DEFAULTS={
 'registration_review':True,'registration_paid':True,'registration_fee':'10.00',
 'domain_add':True,'domain_enforce':True,'merchant_refund':False,'refund_fee_payer':'merchant',
 # 商户每笔手续费 = 订单金额 × 费率，四舍五入到分；不足最低手续费时按最低收取（默认 0.01 元）
 'fee_rate':'1.50','fee_threshold':'0.01','fee_minimum':'0.01',
 'blocked_keywords':['代付','跑分','洗钱','赌博','博彩','彩票','私彩','六合彩','色情','裸聊','毒品','枪支','刷单','套现','资金盘','传销','比特币','USDT','casino','gambling','betting','porn'],
 'blocked_message':'该商品暂不支持支付',
 'invite_enabled':False,'invite_basis':'fee','invite_rate':'10.00','invite_cap_fee':True,'invite_timing':'immediate',
 'deposit_enabled':True,'deposit_minimum':'10.00','deposit_freeze_days':30,'merchant_domain_limit':1,
 'risk_enabled':True,'risk_window_seconds':300,'risk_merchant_count':60,'risk_ip_count':30,'risk_daily_amount':'0.00',
 'risk_failure_count':30,'risk_min_success_rate':'5.00','risk_callback_failures':10,'blocked_ips':[],
 'settlement_mode':'off','settlement_days':1,'settlement_minimum':'10.00','settlement_rate':'0.00','settlement_fee_min':'0.00','settlement_fee_max':'0.00','settlement_daily_limit':1,
 'ip_source':'remote','trusted_proxies':[],
 'jobs_interval_seconds':30,'jobs_mail':True,'jobs_risk':True,'jobs_settlement':True,'jobs_invite':True,
 'version':0,
}
BOOLS={k for k,v in DEFAULTS.items() if type(v)is bool}
ENUMS={'refund_fee_payer':{'platform','merchant'},'invite_basis':{'fee','amount'},'invite_timing':{'immediate','daily','settlement'},'settlement_mode':{'off','manual','auto','both'},'ip_source':{'remote','x_real_ip','x_forwarded_for','cf_connecting_ip'}}
INTEGERS={'deposit_freeze_days':(0,3650),'merchant_domain_limit':(1,10),'risk_window_seconds':(60,86400),'risk_merchant_count':(1,10000),'risk_ip_count':(1,10000),'risk_failure_count':(1,10000),'risk_callback_failures':(1,10000),'settlement_days':(0,30),'settlement_daily_limit':(1,100),'jobs_interval_seconds':(5,3600)}
RATES={'fee_rate','invite_rate','risk_min_success_rate','settlement_rate'}
# Cloudflare 回源网段（官方公布）：来自这些地址的请求，真实访客 IP 只在 CF-Connecting-IP 里
CLOUDFLARE_NETWORKS=tuple(ipaddress.ip_network(value) for value in (
    '173.245.48.0/20','103.21.244.0/22','103.22.200.0/22','103.31.4.0/22','141.101.64.0/18','108.162.192.0/18',
    '190.93.240.0/20','188.114.96.0/20','197.234.240.0/22','198.41.128.0/17','162.158.0.0/15','104.16.0.0/13',
    '104.24.0.0/14','172.64.0.0/13','131.0.72.0/22','2400:cb00::/32','2606:4700::/32','2803:f800::/32',
    '2405:b500::/32','2405:8100::/32','2a06:98c0::/29','2c0f:f248::/32'))
MONEY={'registration_fee','fee_threshold','fee_minimum','deposit_minimum','risk_daily_amount','settlement_minimum','settlement_fee_min','settlement_fee_max'}


def cents(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,2})?',value):raise ValueError('invalid_money')
    try:n=Decimal(value)
    except InvalidOperation:raise ValueError('invalid_money') from None
    if not n.is_finite() or n<0 or n>Decimal('100000000') or n.as_tuple().exponent < -2:raise ValueError('invalid_money')
    return int(n*100)


def amount(value):return format(Decimal(value)/100,'.2f')


class PlatformRules:
    def __init__(self,store):self.store=store
    def read(self,db=None):
        if db is None:
            with self.store.connect() as db:return self.read(db)
        row=db.execute("SELECT value FROM console_settings WHERE key='platform_rules'").fetchone()
        return {**DEFAULTS,**(json.loads(row['value']) if row else {})}
    def save(self,updates):
        if not isinstance(updates,dict) or not updates or set(updates)-DEFAULTS.keys():raise ValueError('invalid_policy')
        if 'registration_review' in updates and updates['registration_review'] is not True:raise ValueError('registration_review_required')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self.read(db)
            if type(updates.get('version'))is not int or updates['version']!=old['version']:raise ValueError('settings_conflict')
            new={**old,**updates}
            for key in BOOLS:
                if type(new[key])is not bool:raise ValueError('invalid_policy:'+key)
            for key,choices in ENUMS.items():
                if new[key] not in choices:raise ValueError('invalid_policy:'+key)
            for key,(low,high) in INTEGERS.items():
                if type(new[key])is not int or not low<=new[key]<=high:raise ValueError('invalid_policy:'+key)
            for key in MONEY|RATES:
                value=cents(new[key])
                if key in RATES and value>10000:raise ValueError('invalid_policy:'+key)
                if key in {'registration_fee','deposit_minimum','settlement_minimum'} and value<1:raise ValueError('invalid_policy:'+key)
                new[key]=amount(value)
            if new['settlement_fee_max']!='0.00' and cents(new['settlement_fee_min'])>cents(new['settlement_fee_max']):raise ValueError('invalid_policy:settlement_fee_max')
            for key in ('blocked_keywords','blocked_ips','trusted_proxies'):
                values=new[key]
                if not isinstance(values,list) or len(values)>200 or any(not isinstance(v,str) or not v.strip() or len(v)>128 for v in values):raise ValueError('invalid_policy:'+key)
                new[key]=list(dict.fromkeys(v.strip() for v in values))
                if key!='blocked_keywords':
                    try:new[key]=[str(ipaddress.ip_network(v,strict=False)) for v in new[key]]
                    except ValueError:raise ValueError('invalid_policy:'+key) from None
            if not isinstance(new['blocked_message'],str) or not 1<=len(new['blocked_message'].strip())<=240:raise ValueError('invalid_policy:blocked_message')
            if new['ip_source']!='remote' and not new['trusted_proxies']:raise ValueError('trusted_proxies_required')
            new['version']=old['version']+1
            db.execute("INSERT INTO console_settings VALUES ('platform_rules',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(new,ensure_ascii=False),))
        return new

    def client_ip(self,request):
        rules=self.read()
        raw=request.environ.get('werkzeug.proxy_fix.orig',{}).get('REMOTE_ADDR',request.environ.get('REMOTE_ADDR',''))
        try:peer=ipaddress.ip_address(raw)
        except ValueError:return ''
        trusted=[ipaddress.ip_network(value) for value in rules['trusted_proxies']]
        if not any(peer in network for network in trusted):return str(peer)
        if any(peer in network for network in CLOUDFLARE_NETWORKS):
            try:return str(ipaddress.ip_address(request.headers.get('CF-Connecting-IP','')))
            except ValueError:return str(peer)
        value=request.headers.get('X-Forwarded-For','')
        if len(value)>2048:return str(peer)
        try:
            parts=[part.strip() for part in value.split(',') if part.strip()]
            if len(parts)>16:return str(peer)
            chain=[ipaddress.ip_address(item) for item in parts]+[peer]
            while len(chain)>1 and any(chain[-1] in network for network in trusted):chain.pop()
            return str(chain[-1])
        except ValueError:return str(peer)
