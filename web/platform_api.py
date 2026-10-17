"""Administrative policy and merchant operations routes; money uses verified ledgers."""
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from flask import g,jsonify,request

from .mailing import EVENTS,PLACEHOLDERS,default_template,render
from .platform_rules import cents,amount


def install_platform(app,auth,create_payment,ApiError,same_origin):
    ops=app.extensions['operations'];rules=ops.rules;mailer=ops.mailer;bot=app.extensions['antibot'];store=ops.store;trace=ops.trace
    def body():
        value=request.get_json(silent=True)
        if not isinstance(value,dict):raise ApiError('invalid_json')
        return value
    def guarded(fn):
        try:return fn()
        except ValueError as exc:raise ApiError(str(exc),409 if str(exc)=='settings_conflict' else 400) from None

    @app.get('/api/admin/policy')
    @auth(session_only=True,admin=True)
    def policy():return jsonify(rules.read())

    @app.put('/api/admin/policy')
    @auth(session_only=True,admin=True)
    def save_policy():
        result=guarded(lambda:rules.save(body()))
        trace('settings.policy','platform_policy_saved',{'fields':list(body())},user_id=g.user['id'])
        return jsonify(result)

    @app.get('/api/admin/mail')
    @auth(session_only=True,admin=True)
    def mail_config():
        with store.connect() as db:jobs=[dict(r) for r in db.execute('SELECT id,status,attempts,error,created_at FROM mail_jobs ORDER BY id DESC LIMIT 20')]
        return jsonify(config=mailer.public(),jobs=jobs,events=EVENTS,placeholders=PLACEHOLDERS)

    @app.put('/api/admin/mail')
    @auth(session_only=True,admin=True)
    def save_mail():
        result=guarded(lambda:mailer.save(body()));trace('settings.mail','smtp_saved',{'enabled':result['enabled']},user_id=g.user['id']);return jsonify(result)

    @app.post('/api/admin/mail/test')
    @auth(session_only=True,admin=True)
    def test_mail():
        if not store.rate_limit('smtp-test:'+str(g.user['id']),3,60):raise ApiError('rate_limited',429)
        payload=body()
        template=payload.get('template') or mailer.template(payload.get('event','verification'),payload.get('language','zh'))
        rendered=guarded(lambda:render(template,{'site_name':'冰云支付','recipient_name':'测试用户','recipient_email':payload.get('recipient',''),'verification_code':'123456','expires_in_minutes':15,'message':'这是一封测试邮件','amount':'10.00','order_no':'TEST_ORDER'}))
        try:mailer.send(payload.get('recipient',''),rendered)
        except Exception as exc:
            trace('mail.test_failed','smtp_test_failed',{'error_type':type(exc).__name__},user_id=g.user['id'],accepted=False);raise ApiError('smtp_test_failed',502) from None
        trace('mail.test_sent','smtp_test_sent',user_id=g.user['id']);return jsonify(ok=True)

    @app.get('/api/admin/mail/template')
    @auth(session_only=True,admin=True)
    def get_template():return jsonify(guarded(lambda:mailer.template(request.args.get('event','verification'),request.args.get('language','zh'))))

    @app.put('/api/admin/mail/template')
    @auth(session_only=True,admin=True)
    def save_template():
        data=body();value=guarded(lambda:mailer.save_template(data.get('event'),data.get('language'),data.get('template')))
        trace('settings.template','mail_template_saved',{'event':data.get('event'),'language':data.get('language')},user_id=g.user['id']);return jsonify(value)

    @app.post('/api/admin/mail/preview')
    @auth(session_only=True,admin=True)
    def preview():
        data=body()
        if data.get('event','verification') not in EVENTS or data.get('language','zh') not in {'zh','en'}:raise ApiError('invalid_template')
        template=default_template(data.get('event','verification'),data.get('language','zh')) if data.get('reset') else data.get('template')
        result=guarded(lambda:render(template,{'site_name':'冰云支付','recipient_name':'张三','recipient_email':'merchant@example.com','verification_code':'123456','expires_in_minutes':15,'message':'您的商户信息已更新','order_no':'PAY_DEMO_001','amount':'10.00'}))
        result['html']='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; base-uri \'none\'; form-action \'none\'">'+result['html']
        return jsonify(**result,template=template)

    @app.get('/api/admin/turnstile')
    @auth(session_only=True,admin=True)
    def turnstile():return jsonify(bot.public())

    @app.put('/api/admin/turnstile')
    @auth(session_only=True,admin=True)
    def save_turnstile():
        result=guarded(lambda:bot.save(body()));trace('settings.turnstile','turnstile_saved',{'enabled':result['enabled']},user_id=g.user['id']);return jsonify(result)

    @app.post('/api/auth/email-code')
    def email_code():
        same_origin()
        data=body();email=data.get('email','')
        if not isinstance(email,str):raise ApiError('invalid_email')
        ip=rules.client_ip(request)
        guarded(lambda:bot.verify(data.get('turnstile_token'),ip,'email_code'))
        guarded(lambda:mailer.issue_code(email.strip().lower(),ip))
        return jsonify(ok=True,expires_in=900,retry_after=60)

    @app.get('/api/account')
    @auth(session_only=True)
    def account():return jsonify(**ops.account(g.user['id']),rules={k:rules.read()[k] for k in ('domain_add','domain_enforce','merchant_refund','invite_enabled','settlement_mode','settlement_minimum')})

    @app.put('/api/account/beneficiary')
    @auth(session_only=True)
    def beneficiary():guarded(lambda:ops.beneficiary(g.user['id'],body()));return jsonify(ok=True)

    @app.post('/api/account/bill')
    @auth(session_only=True)
    def bill():
        data=body();purpose=data.get('purpose');state=ops.access(g.user['id']);policy=rules.read()
        if purpose=='registration':
            value=max(0,state['fee_due']-state['fee_paid'])
            if not value:raise ApiError('registration_already_paid')
        elif purpose=='deposit':
            if not policy['deposit_enabled']:raise ApiError('deposit_disabled')
            value=guarded(lambda:cents(data.get('amount','')))
            if value<cents(policy['deposit_minimum']):raise ApiError('deposit_minimum_required')
        else:raise ApiError('invalid_purpose')
        # Reuse any active bill to avoid accidental duplicate registration charges
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute("SELECT b.number FROM platform_bills b LEFT JOIN orders o ON o.out_trade_no=b.number WHERE b.user_id=? AND b.purpose=? AND b.amount=? AND b.status='pending' AND (o.out_trade_no IS NULL OR (o.status='CREATED' AND o.expires_at>?)) ORDER BY b.rowid DESC LIMIT 1",(g.user['id'],purpose,value,int(time.time()))).fetchone()
            number=old['number'] if old else 'BILL_'+secrets.token_hex(16)
            if not old:db.execute('INSERT INTO platform_bills(number,user_id,purpose,amount) VALUES (?,?,?,?)',(number,g.user['id'],purpose,value))
        return create_payment({'out_trade_no':number,'subject':'商户注册费' if purpose=='registration' else '商户保证金','amount':amount(value),'product':'page','channel':'alipay'},internal=True)

    @app.post('/api/account/deposits/<number>/release')
    @auth(session_only=True)
    def release_deposit(number):guarded(lambda:ops.release_deposit(g.user['id'],number));return jsonify(ok=True)

    @app.post('/api/account/settlements')
    @auth(session_only=True)
    def request_settlement():return jsonify(id=guarded(lambda:ops.settlement(g.user['id'],body().get('amount'))))

    @app.get('/api/domains')
    @auth(session_only=True)
    def domains():
        with store.connect() as db:rows=[dict(r) for r in db.execute('SELECT d.*,u.email FROM merchant_domains d JOIN users u ON u.id=d.user_id'+('' if g.user['role']=='admin' else ' WHERE user_id=?')+' ORDER BY d.id DESC',() if g.user['role']=='admin' else (g.user['id'],))]
        return jsonify(domains=rows)

    @app.post('/api/domains')
    @auth(session_only=True)
    def add_domain():
        if not rules.read()['domain_add'] and g.user['role']!='admin':raise ApiError('domain_application_disabled',403)
        host=body().get('host','')
        if not isinstance(host,str) or not re.fullmatch(r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,63}',host) or len(host)>253:raise ApiError('invalid_domain')
        with store.connect() as db:db.execute('INSERT OR IGNORE INTO merchant_domains(user_id,host,created_at) VALUES (?,?,?)',(g.user['id'],host.lower(),int(time.time())))
        trace('domain.request','domain_requested',{'host':host},user_id=g.user['id']);return jsonify(ok=True)

    @app.post('/api/admin/domains/<int:identity>')
    @auth(session_only=True,admin=True)
    def review_domain(identity):
        status=body().get('status')
        if status not in {'approved','rejected'}:raise ApiError('invalid_status')
        with store.connect() as db:db.execute('UPDATE merchant_domains SET status=? WHERE id=?',(status,identity))
        trace('domain.review','domain_'+status,{'id':identity},user_id=g.user['id']);return jsonify(ok=True)

    @app.post('/api/admin/users/<int:user>/review')
    @auth(session_only=True,admin=True)
    def review_user(user):
        data=body();status=data.get('status');note=data.get('note','')
        if status not in {'approved','rejected'} or not isinstance(note,str) or len(note)>240:raise ApiError('invalid_review')
        with store.connect() as db:
            row=db.execute('SELECT role,email FROM users WHERE id=?',(user,)).fetchone()
            if not row or row['role']=='admin':raise ApiError('admin_protected',403)
            db.execute('UPDATE merchant_access SET review=?,review_note=? WHERE user_id=?',(status,note,user))
            deposit=amount(ops.balance(db,user,'deposit'))
            mailer.enqueue('reminder',row['email'],{'message':'商户审核'+('已通过' if status=='approved' else '未通过')+' '+note},'review:'+str(user)+':'+str(time.time_ns()),db=db)
        # 审核备注与当时实时读取的保证金余额一起留痕
        trace('merchant.review','merchant_'+status,{'target_user':user,'note':note,'deposit_balance':deposit},user_id=g.user['id']);return jsonify(ok=True)

    @app.post('/api/admin/users/<int:user>/risk-release')
    @auth(session_only=True,admin=True)
    def release_risk(user):
        with store.connect() as db:db.execute("UPDATE merchant_access SET risk_hold=0,risk_reason='' WHERE user_id=?",(user,))
        trace('risk.release','merchant_risk_released',{'target_user':user},user_id=g.user['id']);return jsonify(ok=True)

    @app.get('/api/admin/settlements')
    @auth(session_only=True,admin=True)
    def settlements():
        status=request.args.get('status','all')
        if status not in {'all','pending','paid','cancel'}:raise ApiError('invalid_status')
        with store.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT s.*,u.email FROM settlements s JOIN users u ON u.id=s.user_id'+('' if status=='all' else ' WHERE s.status=?')+' ORDER BY s.created_at DESC LIMIT 200',() if status=='all' else (status,))]
            # totals cover every settlement, so they stay right when the list is limited to the latest 200
            totals={r['status']:{'count':r['count'],'gross':r['gross'],'fee':r['fee']} for r in db.execute('SELECT status,COUNT(*) AS count,COALESCE(SUM(gross),0) AS gross,COALESCE(SUM(fee),0) AS fee FROM settlements GROUP BY status')}
        for row in rows:row['beneficiary']=json.loads(mailer.cipher.decrypt(row['beneficiary'])) if row['beneficiary'] else {}
        return jsonify(settlements=rows,totals=totals)

    @app.post('/api/admin/settlements/<identity>')
    @auth(session_only=True,admin=True)
    def settlement_result(identity):
        data=body();guarded(lambda:ops.finish_settlement(identity,data.get('action'),data.get('reference')))
        trace('settlement.result','settlement_'+data['action'],{'id':identity,'reference':data.get('reference')},user_id=g.user['id']);return jsonify(ok=True)

    @app.get('/api/admin/tasks')
    @auth(session_only=True,admin=True)
    def tasks():
        with store.connect() as db:
            runs=[dict(r) for r in db.execute('SELECT * FROM task_runs')];heartbeats=[dict(r) for r in db.execute('SELECT * FROM service_health')]
            balance=db.execute('SELECT COALESCE(SUM(cents),0) FROM ledger_lines').fetchone()[0]
            bad=db.execute('SELECT COUNT(*) FROM (SELECT event_id FROM ledger_lines GROUP BY event_id HAVING SUM(cents)!=0)').fetchone()[0]
        return jsonify(tasks=runs,heartbeats=heartbeats,ledger_balanced=balance==0 and bad==0,client_ip=rules.client_ip(request),execution='worker')

    @app.post('/api/admin/tasks/run')
    @auth(session_only=True,admin=True)
    def run_tasks():
        ran=ops.tick();trace('task.manual','platform_task_requested',{'claimed':ran},user_id=g.user['id']);return jsonify(claimed=ran)
