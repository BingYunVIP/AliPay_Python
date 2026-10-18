"""Merchant admission and balanced, append-only bookkeeping for new transactions."""
import ipaddress
import json
import re
import secrets
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
        with store.connect() as db:db.executescript('''
          CREATE TABLE IF NOT EXISTS merchant_access(user_id INTEGER PRIMARY KEY REFERENCES users(id),review TEXT DEFAULT 'approved',review_note TEXT DEFAULT '',fee_due INTEGER DEFAULT 0,fee_paid INTEGER DEFAULT 0,inviter INTEGER,invite_code TEXT UNIQUE,risk_hold INTEGER DEFAULT 0,risk_reason TEXT DEFAULT '');
          CREATE TABLE IF NOT EXISTS merchant_domains(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,host TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at INTEGER NOT NULL,UNIQUE(user_id,host));
          CREATE TABLE IF NOT EXISTS ledger_events(id TEXT PRIMARY KEY,kind TEXT NOT NULL,reference TEXT NOT NULL,user_id INTEGER NOT NULL,created_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS ledger_lines(id INTEGER PRIMARY KEY,event_id TEXT NOT NULL REFERENCES ledger_events(id),account TEXT NOT NULL,user_id INTEGER NOT NULL,cents INTEGER NOT NULL);
          CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_lines BEGIN SELECT RAISE(ABORT,'immutable_ledger');END;
          CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_lines BEGIN SELECT RAISE(ABORT,'immutable_ledger');END;
          CREATE TABLE IF NOT EXISTS financial_orders(number TEXT PRIMARY KEY,gross INTEGER NOT NULL,fee INTEGER NOT NULL,commission INTEGER NOT NULL,inviter INTEGER,policy TEXT NOT NULL,paid INTEGER DEFAULT 0,phase TEXT DEFAULT 'pending',available_at INTEGER,fee_reversed INTEGER DEFAULT 0,commission_reversed INTEGER DEFAULT 0,commission_released INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS platform_bills(number TEXT PRIMARY KEY,user_id INTEGER NOT NULL,purpose TEXT NOT NULL,amount INTEGER NOT NULL,status TEXT DEFAULT 'pending');
          CREATE TABLE IF NOT EXISTS refund_holds(refund_no TEXT PRIMARY KEY,user_id INTEGER NOT NULL,source TEXT NOT NULL,cents INTEGER NOT NULL,fee_return INTEGER NOT NULL,commission_return INTEGER NOT NULL,status TEXT DEFAULT 'held');
          CREATE TABLE IF NOT EXISTS deposits(number TEXT PRIMARY KEY,user_id INTEGER NOT NULL,cents INTEGER NOT NULL,release_at INTEGER NOT NULL,status TEXT DEFAULT 'frozen');
          CREATE TABLE IF NOT EXISTS settlements(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,gross INTEGER NOT NULL,fee INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at INTEGER NOT NULL,paid_at INTEGER,reference TEXT UNIQUE,beneficiary BLOB);
          CREATE TABLE IF NOT EXISTS settlement_accounts(user_id INTEGER PRIMARY KEY,payload BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS task_runs(name TEXT PRIMARY KEY,locked_until INTEGER DEFAULT 0,started_at INTEGER,finished_at INTEGER,status TEXT DEFAULT 'idle',error TEXT DEFAULT '');
          CREATE TABLE IF NOT EXISTS payment_attempts(number TEXT PRIMARY KEY,user_id INTEGER NOT NULL,ip TEXT NOT NULL,created_at INTEGER NOT NULL);
        ''')
        with store.connect() as db:
            access_columns={row['name'] for row in db.execute('PRAGMA table_info(merchant_access)')}
            if 'pay_enabled' not in access_columns:db.execute('ALTER TABLE merchant_access ADD COLUMN pay_enabled INTEGER NOT NULL DEFAULT 1')
            for row in db.execute('SELECT id FROM users').fetchall():self.ensure(row['id'],db)
        store.operations=self

    def ensure(self,user,db=None,new=False,inviter=None):
        if db is None:
            with self.store.connect() as db:return self.ensure(user,db,new,inviter)
        rules=self.rules.read(db)
        db.execute('INSERT OR IGNORE INTO merchant_access(user_id,review,fee_due,invite_code,inviter) VALUES (?,?,?,?,?)',(user,'pending' if new and rules['registration_review'] else 'approved',cents(rules['registration_fee']) if new and rules['registration_paid'] else 0,secrets.token_urlsafe(9),inviter))
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
                    ipcount=db.execute('SELECT count(*) FROM payment_attempts WHERE ip=? AND created_at>=?',(ip,since)).fetchone()[0]
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
            self.journal(db,'admin-deposit:'+reference,'deposit_adjust',reference,user,[('clearing',0,value),('deposit',user,-value)])
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
        if merchant_fee_rate is not None: rules['fee_rate']=str(merchant_fee_rate)
        fee=rate_fee(gross,rules['fee_rate'])
        if fee<cents(rules['fee_threshold']):fee=max(fee,cents(rules['fee_minimum']))
        fee=min(gross,fee)
        if fee>=gross:fee=0  # 金额太小时手续费会等于整笔金额，直接免收
        state=self.ensure(user,db);inviter=state['inviter'];commission=0
        if rules['invite_enabled'] and inviter and inviter!=user:
            commission=rate_fee(fee if rules['invite_basis']=='fee' else gross,rules['invite_rate'])
            if rules['invite_cap_fee']:commission=min(fee,commission)
        db.execute('INSERT OR IGNORE INTO financial_orders(number,gross,fee,commission,inviter,policy) VALUES (?,?,?,?,?,?)',(number,gross,fee,commission,inviter,json.dumps(rules)))

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
                db.execute('INSERT OR IGNORE INTO deposits VALUES (?,?,?,?,\'frozen\')',(number,user,gross,int(time.time())+self.rules.read(db)['deposit_freeze_days']*86400))
            db.execute("UPDATE platform_bills SET status='paid' WHERE number=?",(number,));return
        row=db.execute('SELECT * FROM financial_orders WHERE number=?',(number,)).fetchone()
        if not row or row['paid']:return
        rules=json.loads(row['policy']);commission_account='available' if rules['invite_timing']=='immediate' else 'commission_hold'
        lines=[('clearing',0,gross),('pending',user,-(gross-row['fee'])),('fee_revenue',0,-row['fee']+row['commission'])]
        if row['commission']:lines.append((commission_account,row['inviter'],-row['commission']))
        self.journal(db,'paid:'+number,'payment',number,user,lines)
        db.execute('UPDATE financial_orders SET paid=1,available_at=?,commission_released=? WHERE number=?',(due_day(rules['settlement_days']),int(commission_account=='available'),number))
        cfg=self.mailer.config(db)
        if cfg['notify_payments']:
            email=db.execute('SELECT email FROM users WHERE id=?',(user,)).fetchone()['email'];self.mailer.enqueue('reminder',email,{'message':'订单已支付','order_no':number,'amount':order['amount']},'paid:'+number,db=db)

    def reserve_refund(self,db,order,refund_no,value):
        row=db.execute('SELECT * FROM financial_orders WHERE number=?',(order['out_trade_no'],)).fetchone()
        if not row or not row['paid']:return
        if db.execute('SELECT 1 FROM refund_holds WHERE refund_no=?',(refund_no,)).fetchone():return
        rules=json.loads(row['policy']);refunded=cents(order['refunded_amount']);new=refunded+cents(value)
        fee_return=(proportional(row['fee'],new,row['gross'])-row['fee_reversed']) if rules['refund_fee_payer']=='platform' else 0
        commission_return=proportional(row['commission'],new,row['gross'])-row['commission_reversed']
        fee_part=proportional(row['fee'],new,row['gross'])-proportional(row['fee'],refunded,row['gross'])
        user=order['user_id'];debit=cents(value)-fee_return
        allocations={row['phase']:cents(value)-fee_part}
        if rules['refund_fee_payer']=='merchant':allocations['available']=allocations.get('available',0)+fee_part
        for source,value in allocations.items():
            if self.balance(db,user,source)<value:raise ValueError('refund_balance_insufficient')
        self.journal(db,'refund-hold:'+refund_no,'refund_hold',order['out_trade_no'],user,[(source,user,value) for source,value in allocations.items()]+[('refund_hold',user,-debit)])
        db.execute('INSERT INTO refund_holds(refund_no,user_id,source,cents,fee_return,commission_return) VALUES (?,?,?,?,?,?)',(refund_no,user,json.dumps(allocations),debit,fee_return,commission_return))

    def finish_refund(self,db,order,record,status):
        hold=db.execute('SELECT * FROM refund_holds WHERE refund_no=?',(record['refund_no'],)).fetchone()
        if not hold or hold['status']!='held' or status not in {'SUCCESS','FAILED'}:return
        user=order['user_id'];number=order['out_trade_no'];row=db.execute('SELECT * FROM financial_orders WHERE number=?',(number,)).fetchone()
        if status=='FAILED':
            allocations=json.loads(hold['source']) if hold['source'].startswith('{') else {hold['source']:hold['cents']}
            lines=[('refund_hold',user,hold['cents'])]+[(source,user,-value) for source,value in allocations.items()]
        else:
            lines=[('refund_hold',user,hold['cents']),('fee_revenue',0,hold['fee_return']-hold['commission_return']),('clearing',0,-cents(record['amount']))]
            if hold['commission_return']:lines.append(('available' if row['commission_released'] else 'commission_hold',row['inviter'],hold['commission_return']))
            db.execute('UPDATE financial_orders SET fee_reversed=fee_reversed+?,commission_reversed=commission_reversed+? WHERE number=?',(hold['fee_return'],hold['commission_return'],number))
        self.journal(db,'refund:'+record['refund_no'],'refund_'+status.lower(),number,user,lines)
        db.execute('UPDATE refund_holds SET status=? WHERE refund_no=?',(status,record['refund_no']))

    def account(self,user):
        with self.store.connect() as db:
            balances={name:amount(self.balance(db,user,name)) for name in ('pending','available','deposit','refund_hold','settlement_hold','commission_hold')}
            events=[dict(r) for r in db.execute('SELECT e.id,e.kind,e.reference,e.created_at,l.account,l.cents FROM ledger_events e JOIN ledger_lines l ON l.event_id=e.id WHERE l.user_id=? ORDER BY e.created_at DESC,l.id DESC LIMIT 100',(user,))]
            settlements=[dict(r) for r in db.execute('SELECT id,gross,fee,status,created_at,paid_at,reference FROM settlements WHERE user_id=? ORDER BY created_at DESC LIMIT 50',(user,))]
            beneficiary=db.execute('SELECT payload FROM settlement_accounts WHERE user_id=?',(user,)).fetchone()
            deposits=[dict(r) for r in db.execute('SELECT * FROM deposits WHERE user_id=? ORDER BY release_at DESC LIMIT 30',(user,))]
        return {'balances':balances,'entries':events,'settlements':settlements,'deposits':deposits,'beneficiary':json.loads(self.mailer.cipher.decrypt(beneficiary['payload'])) if beneficiary else {'channel':'alipay','account':'','name':''},'access':self.access(user)}

    def beneficiary(self,user,payload):
        if not isinstance(payload,dict) or set(payload)!={'channel','account','name'} or payload['channel'] not in {'alipay','bank'} or any(not isinstance(v,str) or not v.strip() or len(v)>200 or any(ord(c)<32 for c in v) for v in payload.values()):raise ValueError('invalid_beneficiary')
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
        if action not in {'paid','cancel'} or (action=='paid' and (not isinstance(reference,str) or not 6<=len(reference)<=128 or any(ord(c)<32 for c in reference))):raise ValueError('settlement_evidence_required')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM settlements WHERE id=?',(number,)).fetchone()
            if not row or row['status']!='pending':raise ValueError('settlement_not_pending')
            user=row['user_id']
            if action=='paid' and (self.balance(db,user,'available')<0 or self.ensure(user,db)['risk_hold']):raise ValueError('settlement_balance_changed')
            lines=[('settlement_hold',user,row['gross'])]
            if action=='cancel':lines.append(('available',user,-row['gross']))
            else:lines += [('clearing',0,-(row['gross']-row['fee'])),('settlement_fee',0,-row['fee'])]
            self.journal(db,'settlement-result:'+number,'settlement_'+action,number,user,lines)
            db.execute('UPDATE settlements SET status=?,reference=?,paid_at=? WHERE id=?',(action,reference if action=='paid' else None,int(time.time()) if action=='paid' else None,number))

    def release_deposit(self,user,number):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT * FROM deposits WHERE number=? AND user_id=? AND status='frozen'",(number,user))
            row=row.fetchone();access=self.ensure(user,db)
            rules=self.rules.read(db)
            last=db.execute('SELECT MAX(created_at) FROM orders WHERE user_id=? AND out_trade_no NOT IN (SELECT number FROM platform_bills)',(user,)).fetchone()[0] or 0
            unresolved=db.execute("SELECT 1 FROM orders WHERE user_id=? AND (status='REFUNDING' OR (status='CREATED' AND expires_at>?)) LIMIT 1",(user,int(time.time()))).fetchone()
            if not row or row['release_at']>time.time() or last+rules['deposit_freeze_days']*86400>time.time() or access['risk_hold'] or unresolved:raise ValueError('deposit_still_frozen')
            self.journal(db,'deposit-release:'+number,'deposit_release',number,user,[('deposit',user,row['cents']),('available',user,-row['cents'])])
            db.execute("UPDATE deposits SET status='released' WHERE number=?",(number,))

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
