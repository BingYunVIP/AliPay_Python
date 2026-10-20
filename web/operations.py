"""Merchant admission and balanced, append-only bookkeeping for new transactions."""
import ipaddress
import json
import re
import secrets
import sqlite3
import time
import unicodedata
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_HALF_UP
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .platform_rules import PlatformRules,cents,amount

ZONE=ZoneInfo('Asia/Shanghai')


def proportional(total,part,whole):return int((Decimal(total)*part/whole).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) if whole else 0

def rate_fee(value,rate):return int((Decimal(value)*Decimal(rate)/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

def due_day(days,now=None):
    dt=datetime.fromtimestamp(now or time.time(),ZONE)
    return int((dt.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=days)).timestamp()) if days else int(now or time.time())


class Operations:
    def __init__(self,store,mailer,trace):
        self.store,self.mailer,self.trace=store,mailer,trace;self.rules=PlatformRules(store)
        with store.connect() as db:
            for row in db.execute('SELECT id FROM users').fetchall(): self.ensure(row['id'],db)
        store.operations=self

    def ensure(self,user,db=None,new=False,inviter=None):
        if db is None:
            with self.store.connect() as db:return self.ensure(user,db,new,inviter)
        rules=self.rules.read(db)
        db.execute('INSERT OR IGNORE INTO merchant_access(user_id,review,fee_due,invite_code,inviter,review_required) VALUES (?,?,?,?,?,?)',(user,'pending' if new and rules['registration_review'] else 'approved',cents(rules['registration_fee']) if new and rules['registration_paid'] else 0,secrets.token_urlsafe(9),inviter,int(bool(new and rules['registration_review']))))
        return dict(db.execute('SELECT * FROM merchant_access WHERE user_id=?',(user,)).fetchone())

    def inviter(self,code):
        if not code:return None
        if not self.rules.read()['invite_enabled']:return None
        with self.store.connect() as db:row=db.execute('SELECT a.user_id FROM merchant_access a JOIN users u ON u.id=a.user_id WHERE a.invite_code=? AND u.disabled=0',(code,)).fetchone()
        if not row:raise ValueError('invalid_invitation')
        return row['user_id']

    def callback_hosts(self):
        with self.store.connect() as db:
            domains={row['host'] for row in db.execute("SELECT host FROM merchant_domains WHERE status='approved'")}
            domains.update(urlsplit(row['notify_url']).hostname for row in db.execute("SELECT DISTINCT notify_url FROM orders WHERE notify_url!=''"))
        return domains-{None}

    def access(self,user):
        state=self.ensure(user);rules=self.rules.read()
        with self.store.connect() as db:deposit=self.balance(db,user,'deposit')
        return {**state,'registration_fee':amount(state['fee_due']),'registration_paid':state['fee_paid']>=state['fee_due'],
                'pay_enabled':bool(state['pay_enabled']),
                'deposit_balance':amount(deposit),'deposit_required':rules['deposit_enabled'],'deposit_minimum':rules['deposit_minimum'],
                'deposit_freeze_days':rules['deposit_freeze_days'],'ready':state['review']=='approved' and state['fee_paid']>=state['fee_due'] and not state['risk_hold'] and (not rules['deposit_enabled'] or deposit>=cents(rules['deposit_minimum']))}

    def precheck(self,user,subject,value,notify,number,ip,admin=False):
        rules=self.rules.read();state=self.ensure(user)
        if not admin:
            if state['review']!='approved':raise ValueError('registration_review_required')
            if state['fee_paid']<state['fee_due']:raise ValueError('registration_payment_required')
            if state['risk_hold']:raise ValueError('merchant_risk_hold')
            if not state['pay_enabled']:raise ValueError('merchant_pay_disabled')
            if rules['deposit_enabled']:
                with self.store.connect() as db:
                    if self.balance(db,user,'deposit')<cents(rules['deposit_minimum']):raise ValueError('deposit_required')
        normalized=unicodedata.normalize('NFKC',subject).casefold()
        if any(unicodedata.normalize('NFKC',key).casefold() in normalized for key in rules['blocked_keywords']):raise ValueError('product_blocked')
        if rules['domain_enforce'] and not admin:
            if not notify:raise ValueError('domain_not_authorized')
            host=urlsplit(notify).hostname
            with self.store.connect() as db:
                if not db.execute("SELECT 1 FROM merchant_domains WHERE user_id=? AND host=? AND status='approved'",(user,host)).fetchone():raise ValueError('domain_not_authorized')
        if rules['risk_enabled'] and not admin:
            try:
                if any(ipaddress.ip_address(ip) in ipaddress.ip_network(v) for v in rules['blocked_ips']):raise ValueError('ip_blocked')
            except ValueError as exc:
                if str(exc)=='ip_blocked':raise
            since=int(time.time())-rules['risk_window_seconds']
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT 1 FROM payment_attempts WHERE number=?',(number,)).fetchone():
                    count=db.execute('SELECT count(*) FROM payment_attempts WHERE user_id=? AND created_at>=?',(user,since)).fetchone()[0]
                    ipcount=db.execute('SELECT count(*) FROM payment_attempts WHERE ip=? AND created_at>=?',(ip,since)).fetchone()[0] if ip else 0
                    if count>=rules['risk_merchant_count'] or ipcount>=rules['risk_ip_count']:raise ValueError('risk_frequency_exceeded')
                    maximum=cents(rules['risk_daily_amount'])
                    if maximum:
                        total=db.execute('SELECT COALESCE(SUM(CAST(ROUND(CAST(amount AS NUMERIC)*100) AS INTEGER)),0) FROM orders WHERE user_id=? AND created_at>=?',(user,int(datetime.now(ZONE).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()))).fetchone()[0]
                        if total+cents(value)>maximum:raise ValueError('risk_daily_amount_exceeded')
                    db.execute('INSERT INTO payment_attempts VALUES (?,?,?,?)',(number,user,ip,int(time.time())))
        return rules

    @staticmethod
    def balance(db,user,account):return -db.execute('SELECT COALESCE(SUM(cents),0) FROM ledger_lines WHERE user_id=? AND account=?',(user,account)).fetchone()[0]

    def deposit_balance(self,user):
        with self.store.connect() as db:return self.balance(db,user,'deposit')

    def credit_deposit(self,user,value,reference):
        """管理员登记商户保证金：只追加一条平衡分录，不改历史分录。"""
        if value<=0:return 0
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.journal(db,'admin-deposit:'+reference,'deposit_adjust',reference,user,[('manual_adjustment',0,value),('deposit',user,-value)]):
                db.execute("INSERT INTO deposits(number,user_id,cents,release_at,status,source,remaining) VALUES (?,?,?,?,'frozen','manual',?)",('MANUAL_'+reference,user,value,int(time.time())+self.rules.read(db)['deposit_freeze_days']*86400,value))
        return value

    def journal(self,db,key,kind,reference,user,lines):
        if db.execute('SELECT 1 FROM ledger_events WHERE id=?',(key,)).fetchone():return False
        if not lines or sum(value for _,_,value in lines)!=0 or any(type(value)is not int for _,_,value in lines):raise ValueError('unbalanced_ledger')
        db.execute('INSERT INTO ledger_events VALUES (?,?,?,?,?)',(key,kind,reference,user,int(time.time())))
        db.executemany('INSERT INTO ledger_lines(event_id,account,user_id,cents) VALUES (?,?,?,?)',[(key,a,u,v) for a,u,v in lines if v])
        return True

    def snapshot(self,db,user,number,value,merchant_fee_rate=None):
        if db.execute('SELECT 1 FROM platform_bills WHERE number=?',(number,)).fetchone():return
        rules=self.rules.read(db);gross=cents(value)
        group=self.store.groups.for_user(user,db)
        cost_rate=Decimal(group['cost_rate'])
        service_rate=Decimal(group['service_rate'])
        rounded_total=rate_fee(gross,str(service_rate+cost_rate))
        channel_cost=rate_fee(gross,str(cost_rate))
        service_fee=max(0,rounded_total-channel_cost)
        if service_fee<cents(rules['fee_threshold']):service_fee=max(service_fee,cents(rules['fee_minimum'])) if service_rate>0 else 0
        total_fee=service_fee+channel_cost
        if total_fee>=gross: service_fee=channel_cost=total_fee=0  # 不让 0.01 元订单被扣成零
        fee=total_fee
        rules={**rules,'fee_rate':format(service_rate+cost_rate,'.2f')}
        state=self.ensure(user,db);inviter=state['inviter'];commission=0
        if group['billing_mode']!='admin_cost_only' and rules['invite_enabled'] and inviter and inviter!=user:
            commission=rate_fee(service_fee if rules['invite_basis']=='fee' else gross,rules['invite_rate'])
            if rules['invite_cap_fee']:commission=min(service_fee,commission)
        db.execute('INSERT OR IGNORE INTO financial_orders(number,gross,fee,commission,inviter,policy,service_fee,channel_cost) VALUES (?,?,?,?,?,?,?,?)',(number,gross,fee,commission,inviter,json.dumps({**rules,'service_rate':str(service_rate),'channel_cost_rate':str(cost_rate),'merchant_group_id':group['id']}),service_fee,channel_cost))

    def paid(self,db,order):
        number,user=order['out_trade_no'],order['user_id'];gross=cents(order['amount'])
        bill=db.execute('SELECT * FROM platform_bills WHERE number=?',(number,)).fetchone()
        if bill:
            if bill['status']=='paid':return
            if bill['amount']!=gross:raise ValueError('billing_amount_mismatch')
            if bill['purpose']=='registration':
                access=self.ensure(user,db);charge=min(gross,max(0,access['fee_due']-access['fee_paid']))
                self.journal(db,'bill:'+number,'registration',number,user,[('clearing',0,gross),('registration_revenue',0,-charge),('available',user,-(gross-charge))])
                db.execute('UPDATE merchant_access SET fee_paid=fee_paid+? WHERE user_id=?',(charge,user))
            elif bill['purpose']=='deposit':
                self.journal(db,'bill:'+number,'deposit',number,user,[('clearing',0,gross),('deposit',user,-gross)])
                db.execute("INSERT OR IGNORE INTO deposits(number,user_id,cents,release_at,status,source,remaining) VALUES (?,?,?,?,'frozen','real',?)",(number,user,gross,int(time.time())+self.rules.read(db)['deposit_freeze_days']*86400,gross))
            db.execute("UPDATE platform_bills SET status='paid' WHERE number=?",(number,));return
        row=db.execute('SELECT * FROM financial_orders WHERE number=?',(number,)).fetchone()
        if not row or row['paid']:return
        rules=json.loads(row['policy']);commission_account='available' if rules['invite_timing']=='immediate' else 'commission_hold'
        group_id=json.loads(row['policy']).get('merchant_group_id',1)
        if group_id==2:
            lines=[('clearing',0,gross),('available',user,-(gross-row['channel_cost'])),('channel_cost_payable',0,-row['channel_cost'])]
        else:
            lines=[('clearing',0,gross),('pending',user,-(gross-row['fee'])),('fee_revenue',0,-row['service_fee']+row['commission']),('channel_cost_payable',0,-row['channel_cost'])]
        if row['commission']:lines.append((commission_account,row['inviter'],-row['commission']))
        self.journal(db,'paid:'+number,'payment',number,user,lines)
        db.execute('UPDATE financial_orders SET paid=1,phase=?,available_at=?,commission_released=? WHERE number=?',('available' if group_id==2 else 'pending',int(time.time()) if group_id==2 else due_day(rules['settlement_days']),int(commission_account=='available'),number))
        cfg=self.mailer.config(db)
        if cfg['notify_payments']:
            email=db.execute('SELECT email FROM users WHERE id=?',(user,)).fetchone()['email'];self.mailer.enqueue('reminder',email,{'message':'订单已支付','order_no':number,'amount':order['amount']},'paid:'+number,db=db)

    def reserve_refund(self,db,order,refund_no,value):
        row=db.execute('SELECT * FROM financial_orders WHERE number=?',(order['out_trade_no'],)).fetchone()
        if not row or not row['paid']:return
        if db.execute('SELECT 1 FROM refund_holds WHERE refund_no=?',(refund_no,)).fetchone():return
        rules=json.loads(row['policy']);refunded=cents(order['refunded_amount']);new=refunded+cents(value)
        service_fee_return=(proportional(row['service_fee'],new,row['gross'])-row['service_fee_reversed']) if rules['refund_fee_payer']=='platform' else 0
        channel_cost_return=(proportional(row['channel_cost'],new,row['gross'])-row['channel_cost_reversed']) if rules['refund_fee_payer']=='platform' else 0
        fee_return=service_fee_return+channel_cost_return
        commission_return=proportional(row['commission'],new,row['gross'])-row['commission_reversed']
        fee_part=proportional(row['fee'],new,row['gross'])-proportional(row['fee'],refunded,row['gross'])
        user=order['user_id'];debit=cents(value)-fee_return
        # Reserve the order's net entitlement first. When the merchant bears
        # fees, the pro-rated fee portion must also come from available funds;
        # the guarantee deposit covers any remainder. This prevents a refund
        # from consuming another pending order's entitlement for the fee.
        principal=max(0,cents(value)-fee_part)
        remaining=principal;allocations={}
        candidates=[row['phase'],'available' if row['phase']=='pending' else 'pending']
        for source in candidates:
            taken=min(remaining,max(0,self.balance(db,user,source)-allocations.get(source,0)))
            if taken:allocations[source]=allocations.get(source,0)+taken;remaining-=taken
            if not remaining:break
        if rules['refund_fee_payer']=='merchant' and fee_part:
            available=max(0,self.balance(db,user,'available')-allocations.get('available',0))
            taken=min(fee_part,available)
            if taken:allocations['available']=allocations.get('available',0)+taken
        remaining=debit-sum(allocations.values())
        if remaining<0:
            excess=-remaining
            for source in ('deposit','available',row['phase'],'pending'):
                take=min(excess,allocations.get(source,0))
                if take:allocations[source]-=take;excess-=take
                if not excess:break
            if excess:raise ValueError('unbalanced_refund_reservation')
            remaining=0
        if remaining:
            deposit_balance=self.balance(db,user,'deposit')
            if deposit_balance<remaining:raise ValueError('refund_balance_insufficient')
            allocations['deposit']=remaining
            lots=db.execute("SELECT d.number,d.remaining,COALESCE(SUM(CASE WHEN a.status='held' THEN a.cents ELSE 0 END),0) reserved FROM deposits d LEFT JOIN refund_deposit_lots a ON a.deposit_no=d.number WHERE d.user_id=? AND d.status='frozen' GROUP BY d.number ORDER BY d.release_at,d.number",(user,)).fetchall()
            lot_left=remaining
            for lot in lots:
                available=max(0,lot['remaining']-lot['reserved'])
                take=min(lot_left,available)
                if take:
                    db.execute("INSERT INTO refund_deposit_lots(refund_no,deposit_no,cents,status) VALUES (?,?,?,'held')",(refund_no,lot['number'],take))
                    lot_left-=take
                if not lot_left:break
            if lot_left:raise ValueError('refund_balance_insufficient')
        self.journal(db,'refund-hold:'+refund_no,'refund_hold',order['out_trade_no'],user,[(source,user,value) for source,value in allocations.items()]+[('refund_hold',user,-debit)])
        db.execute('INSERT INTO refund_holds(refund_no,user_id,source,cents,fee_return,commission_return,service_fee_return,channel_cost_return) VALUES (?,?,?,?,?,?,?,?)',(refund_no,user,json.dumps(allocations),debit,fee_return,commission_return,service_fee_return,channel_cost_return))

    def finish_refund(self,db,order,record,status):
        hold=db.execute('SELECT * FROM refund_holds WHERE refund_no=?',(record['refund_no'],)).fetchone()
        if not hold or hold['status']!='held' or status not in {'SUCCESS','FAILED'}:return
        user=order['user_id'];number=order['out_trade_no'];row=db.execute('SELECT * FROM financial_orders WHERE number=?',(number,)).fetchone()
        if status=='FAILED':
            allocations=json.loads(hold['source']) if hold['source'].startswith('{') else {hold['source']:hold['cents']}
            lines=[('refund_hold',user,hold['cents'])]+[(source,user,-value) for source,value in allocations.items()]
            db.execute("UPDATE refund_deposit_lots SET status='released' WHERE refund_no=? AND status='held'",(record['refund_no'],))
        else:
            lines=[('refund_hold',user,hold['cents']),('fee_revenue',0,hold['service_fee_return']-hold['commission_return']),('channel_cost_payable',0,hold['channel_cost_return']),('clearing',0,-cents(record['amount']))]
            if hold['commission_return']:lines.append(('available' if row['commission_released'] else 'commission_hold',row['inviter'],hold['commission_return']))
            db.execute('UPDATE financial_orders SET fee_reversed=fee_reversed+?,service_fee_reversed=service_fee_reversed+?,channel_cost_reversed=channel_cost_reversed+?,commission_reversed=commission_reversed+? WHERE number=?',(hold['fee_return'],hold['service_fee_return'],hold['channel_cost_return'],hold['commission_return'],number))
            lots=db.execute("SELECT deposit_no,cents FROM refund_deposit_lots WHERE refund_no=? AND status='held'",(record['refund_no'],)).fetchall()
            for lot in lots:
                changed=db.execute("UPDATE deposits SET remaining=remaining-? WHERE number=? AND remaining>=?",(lot['cents'],lot['deposit_no'],lot['cents'])).rowcount
                if not changed:raise ValueError('refund_deposit_lot_conflict')
            db.execute("UPDATE refund_deposit_lots SET status='spent' WHERE refund_no=? AND status='held'",(record['refund_no'],))
        self.journal(db,'refund:'+record['refund_no'],'refund_'+status.lower(),number,user,lines)
        db.execute('UPDATE refund_holds SET status=? WHERE refund_no=?',(status,record['refund_no']))

    def account(self,user):
        with self.store.connect() as db:
            balances={name:amount(self.balance(db,user,name)) for name in ('pending','available','deposit','refund_hold','settlement_hold','commission_hold')}
            events=[dict(r) for r in db.execute('SELECT e.id,e.kind,e.reference,e.created_at,l.account,l.cents FROM ledger_events e JOIN ledger_lines l ON l.event_id=e.id WHERE l.user_id=? ORDER BY e.created_at DESC,l.id DESC LIMIT 100',(user,))]
            settlements=[dict(r) for r in db.execute('SELECT id,gross,fee,status,created_at,paid_at,reference,beneficiary FROM settlements WHERE user_id=? ORDER BY created_at DESC LIMIT 50',(user,))]
            beneficiary=db.execute('SELECT payload FROM settlement_accounts WHERE user_id=?',(user,)).fetchone()
            deposits=[dict(r) for r in db.execute('SELECT * FROM deposits WHERE user_id=? ORDER BY release_at DESC LIMIT 30',(user,))]
        for entry in settlements:entry['beneficiary']=json.loads(self.mailer.cipher.decrypt(entry['beneficiary'])) if entry['beneficiary'] else {}
        return {'balances':balances,'entries':events,'settlements':settlements,'deposits':deposits,'beneficiary':json.loads(self.mailer.cipher.decrypt(beneficiary['payload'])) if beneficiary else {'channel':'alipay','account':'','name':''},'access':self.access(user)}

    def beneficiary(self,user,payload):
        if not isinstance(payload,dict) or not isinstance(payload.get('channel'),str):raise ValueError('invalid_beneficiary')
        if payload['channel'] in {'alipay','bank'}:
            if set(payload)!={'channel','account','name'} or any(not isinstance(v,str) or not v.strip() or len(v)>200 or any(ord(c)<32 for c in v) for v in payload.values()):raise ValueError('invalid_beneficiary')
        else:raise ValueError('invalid_beneficiary')
        with self.store.connect() as db:db.execute('INSERT INTO settlement_accounts VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload',(user,self.mailer.cipher.encrypt(json.dumps(payload).encode())))

    def settlement(self,user,value=None,automatic=False):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');rules=self.rules.read(db)
            if rules['settlement_mode'] not in ({'auto','both'} if automatic else {'manual','both'}):raise ValueError('settlement_disabled')
            access=self.ensure(user,db)
            active=db.execute('SELECT disabled FROM users WHERE id=?',(user,)).fetchone()
            if not active or active['disabled'] or access['review']!='approved' or access['risk_hold']:raise ValueError('merchant_not_ready')
            beneficiary=db.execute('SELECT payload FROM settlement_accounts WHERE user_id=?',(user,)).fetchone()
            if not beneficiary:raise ValueError('beneficiary_required')
            available=self.balance(db,user,'available');gross=cents(value) if value is not None else available
            if gross<cents(rules['settlement_minimum']) or gross>available:raise ValueError('settlement_balance_insufficient')
            start=int(datetime.now(ZONE).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
            if db.execute('SELECT count(*) FROM settlements WHERE user_id=? AND created_at>=?',(user,start)).fetchone()[0]>=rules['settlement_daily_limit']:raise ValueError('settlement_daily_limit')
            fee=max(rate_fee(gross,rules['settlement_rate']),cents(rules['settlement_fee_min']))
            if cents(rules['settlement_fee_max']):fee=min(fee,cents(rules['settlement_fee_max']))
            if fee>=gross:raise ValueError('settlement_fee_exceeds_amount')
            number='ST_'+secrets.token_hex(12)
            self.journal(db,'settlement:'+number,'settlement_request',number,user,[('available',user,gross),('settlement_hold',user,-gross)])
            db.execute('INSERT INTO settlements(id,user_id,gross,fee,created_at,beneficiary) VALUES (?,?,?,?,?,?)',(number,user,gross,fee,int(time.time()),beneficiary['payload']))
        return number

    def finish_settlement(self,number,action,reference):
        if action not in {'paid','cancel'}:raise ValueError('settlement_evidence_required')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM settlements WHERE id=?',(number,)).fetchone()
            if not row:raise ValueError('settlement_not_pending')
            if row['status']!='pending':
                if action==row['status'] and (action=='cancel' or reference==row['reference']):return True
                raise ValueError('settlement_not_pending')
            user=row['user_id']
            if action=='paid' and (self.balance(db,user,'available')<0 or self.ensure(user,db)['risk_hold']):raise ValueError('settlement_balance_changed')
            if action=='paid' and (not isinstance(reference,str) or not 6<=len(reference)<=128 or any(ord(c)<32 for c in reference)):
                raise ValueError('settlement_evidence_required')
            lines=[('settlement_hold',user,row['gross'])]
            if action=='cancel':lines.append(('available',user,-row['gross']))
            else:lines += [('clearing',0,-(row['gross']-row['fee'])),('settlement_fee',0,-row['fee'])]
            self.journal(db,'settlement-result:'+number,'settlement_'+action,number,user,lines)
            try:db.execute('UPDATE settlements SET status=?,reference=?,paid_at=? WHERE id=?',(action,reference if action=='paid' else None,int(time.time()) if action=='paid' else None,number))
            except sqlite3.IntegrityError:raise ValueError('settlement_reference_used') from None

    def release_deposit(self,user,number):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT * FROM deposits WHERE number=? AND user_id=? AND status='frozen'",(number,user))
            row=row.fetchone();access=self.ensure(user,db)
            rules=self.rules.read(db)
            last=db.execute('SELECT MAX(created_at) FROM orders WHERE user_id=? AND out_trade_no NOT IN (SELECT number FROM platform_bills)',(user,)).fetchone()[0] or 0
            unresolved=db.execute("SELECT 1 FROM orders WHERE user_id=? AND (status='REFUNDING' OR (status='CREATED' AND expires_at>?)) LIMIT 1",(user,int(time.time()))).fetchone()
            if not row or row['release_at']>time.time() or last+rules['deposit_freeze_days']*86400>time.time() or access['risk_hold'] or unresolved:raise ValueError('deposit_still_frozen')
            value=row['remaining']
            if self.balance(db,user,'deposit')<value:raise ValueError('deposit_still_frozen')
            self.journal(db,'deposit-release:'+number,'deposit_release',number,user,[('deposit',user,value),('available',user,-value)])
            db.execute("UPDATE deposits SET status='released',remaining=0 WHERE number=?",(number,))

    def tick(self):
        now=int(time.time());rules=self.rules.read();name='platform'
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO task_runs(name) VALUES (?)",(name,))
            claimed=db.execute("UPDATE task_runs SET locked_until=?,started_at=?,status='running' WHERE name=? AND locked_until<=? AND COALESCE(finished_at,0)<=?",(now+120,now,name,now,now-rules['jobs_interval_seconds'])).rowcount
        if not claimed:return False
        try:
            if rules['jobs_settlement'] or rules['jobs_invite']:
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    rows=db.execute("SELECT f.*,o.user_id,o.refunded_amount,o.status,o.paid_at FROM financial_orders f JOIN orders o ON o.out_trade_no=f.number WHERE f.paid=1 AND ((f.phase='pending' AND f.available_at<=? AND o.status!='REFUNDING') OR (f.commission_released=0 AND ((json_extract(f.policy,'$.invite_timing')='daily' AND o.paid_at<?) OR (json_extract(f.policy,'$.invite_timing')='settlement' AND f.phase='available')))) ORDER BY f.available_at LIMIT 500",(now,int(datetime.now(ZONE).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()))).fetchall()
                    for row in rows:
                        policy=json.loads(row['policy']);user=row['user_id'];phase=row['phase']
                        if rules['jobs_settlement'] and row['available_at']<=now and phase=='pending' and row['status']!='REFUNDING':
                            net=max(0,row['gross']-row['fee']-cents(row['refunded_amount'])+proportional(row['fee'],cents(row['refunded_amount']),row['gross']))
                            if net:self.journal(db,'available:'+row['number'],'available',row['number'],user,[('pending',user,net),('available',user,-net)])
                            db.execute("UPDATE financial_orders SET phase='available' WHERE number=?",(row['number'],));phase='available'
                        release=policy['invite_timing']=='daily' and due_day(1,row['paid_at'])<=now or policy['invite_timing']=='settlement' and phase=='available'
                        if rules['jobs_invite'] and not row['commission_released'] and release:
                            commission=row['commission']-row['commission_reversed']
                            if commission:self.journal(db,'invite:'+row['number'],'invite_release',row['number'],row['inviter'],[('commission_hold',row['inviter'],commission),('available',row['inviter'],-commission)])
                            db.execute('UPDATE financial_orders SET commission_released=1 WHERE number=?',(row['number'],))
            if rules['jobs_risk'] and rules['risk_enabled']:self.risk_tick(rules)
            if rules['jobs_settlement'] and rules['settlement_mode'] in {'auto','both'}:
                with self.store.connect() as db:users=[r[0] for r in db.execute("SELECT DISTINCT user_id FROM ledger_lines WHERE account='available' AND user_id>0 LIMIT 1000")]
                for user in users:
                    try:self.settlement(user,automatic=True)
                    except ValueError:pass
            with self.store.connect() as db:
                db.execute('DELETE FROM email_codes WHERE expires<?',(now-86400,))
                db.execute("DELETE FROM mail_jobs WHERE status IN ('sent','failed') AND created_at<?",(now-30*86400,))
                db.execute('DELETE FROM payment_attempts WHERE created_at<?',(now-2*86400,))
                db.execute("UPDATE task_runs SET status='success',finished_at=?,locked_until=0,error='' WHERE name=?",(int(time.time()),name))
        except Exception as exc:
            with self.store.connect() as db:db.execute("UPDATE task_runs SET status='failed',finished_at=?,locked_until=0,error=? WHERE name=?",(int(time.time()),type(exc).__name__,name))
            self.trace('task.failed','platform_task_failed',{'exception':type(exc).__name__},accepted=False)
        return True

    def risk_tick(self,rules):
        now=int(time.time())
        with self.store.connect() as db:
            users=db.execute("SELECT u.id,u.email FROM users u JOIN merchant_access a ON a.user_id=u.id WHERE u.role='user' AND a.risk_hold=0").fetchall()
            for user in users:
                orders=db.execute('SELECT paid_at FROM orders WHERE user_id=? AND expires_at>=? AND expires_at<? AND out_trade_no NOT IN (SELECT number FROM platform_bills)',(user['id'],now-rules['risk_window_seconds'],now)).fetchall()
                reason=''
                if len(orders)>=rules['risk_failure_count'] and sum(bool(r['paid_at']) for r in orders)*100/len(orders)<float(rules['risk_min_success_rate']):reason='low_payment_success'
                outcomes=db.execute('SELECT status FROM outbox WHERE user_id=? ORDER BY rowid DESC LIMIT ?',(user['id'],rules['risk_callback_failures'])).fetchall()
                if len(outcomes)>=rules['risk_callback_failures'] and all(r['status']=='failed' for r in outcomes):reason='callback_failures'
                if reason:
                    db.execute('UPDATE merchant_access SET risk_hold=1,risk_reason=? WHERE user_id=?',(reason,user['id']))
                    if self.mailer.config(db)['notify_risk']:self.mailer.enqueue('reminder',user['email'],{'message':'商户收款因风控规则暂停，请联系管理员'},'risk:'+str(user['id'])+':'+str(now),db=db)
        for user in users:
            state=self.ensure(user['id'])
            if state['risk_hold']:self.trace('risk.hold',state['risk_reason'],user_id=user['id'],accepted=False)
