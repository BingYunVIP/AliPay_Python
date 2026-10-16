"""Encrypted SMTP configuration, bounded templates, transactional mail queue and OTPs."""
import hashlib
import hmac
import html
import json
import re
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr

MAIL_DEFAULTS={'enabled':False,'host':'smtp.qq.com','port':465,'security':'ssl','username':'','password':'','sender':'','sender_name':'冰云支付','registration_code':False,'notify_registration':True,'notify_payments':False,'notify_risk':True,'version':0}
PLACEHOLDERS=['site_name','recipient_name','recipient_email','verification_code','expires_in_minutes','message','order_no','amount']
EVENTS={'verification':'邮箱验证码','registration':'注册通知','reminder':'业务提醒'}


def default_template(event,language):
    english=language=='en';title={'verification':'Email verification','registration':'Welcome to Pay','reminder':'Account notice'}[event] if english else EVENTS[event]
    content=('Your verification code' if english else '您的验证码')+'<div style="font-size:30px;letter-spacing:8px;margin:24px 0;font-weight:700">{{verification_code}}</div>'+('Expires in {{expires_in_minutes}} minutes' if english else '{{expires_in_minutes}} 分钟内有效') if event=='verification' else '{{message}}'
    if event=='reminder':content += '<p>{{order_no}} {{amount}}</p>'
    return {'subject':'[{{site_name}}] '+title,'html':f'<!doctype html><html><body style="margin:0;background:#f4f6fa;padding:24px;font-family:Arial,sans-serif"><div style="max-width:620px;margin:auto;background:#fff;border-radius:16px;overflow:hidden"><div style="background:#2563eb;padding:28px;color:#fff;font-size:24px;font-weight:700">{title}</div><div style="padding:32px;color:#182233;line-height:1.8"><p>{{{{recipient_name}}}}</p><p>{content}</p></div><div style="padding:20px 32px;background:#f8fafc;font-size:12px;color:#64748b">{{{{site_name}}}}</div></div></body></html>'}


def email_address(value):return isinstance(value,str) and len(value)<=254 and re.fullmatch(r'[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+',value)


def render(template,variables):
    if not isinstance(template,dict) or set(template)!={'subject','html'}:raise ValueError('invalid_template')
    for key,maximum in [('subject',200),('html',60000)]:
        if not isinstance(template[key],str) or not template[key].strip() or len(template[key])>maximum:raise ValueError('invalid_template')
        if key=='subject' and any(c in template[key] for c in '\r\n'):raise ValueError('invalid_template')
        if any(name not in PLACEHOLDERS for name in re.findall(r'{{\s*([^{}]+?)\s*}}',template[key])):raise ValueError('unknown_placeholder')
    result={}
    for kind,value in template.items():
        result[kind]=re.sub(r'{{\s*([a-z_]+)\s*}}',lambda m:html.escape(str(variables.get(m[1],'')),quote=True) if kind=='html' else str(variables.get(m[1],'')),value)
    if any(c in result['subject'] for c in '\r\n'):raise ValueError('invalid_template')
    return result


class Mailer:
    def __init__(self,store,cipher,trace):
        self.store,self.cipher,self.trace=store,cipher,trace
        with store.connect() as db:db.executescript('''CREATE TABLE IF NOT EXISTS mail_templates(event TEXT,language TEXT,subject TEXT,html TEXT,PRIMARY KEY(event,language));
          CREATE TABLE IF NOT EXISTS mail_jobs(id INTEGER PRIMARY KEY,dedupe TEXT UNIQUE,payload BLOB,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,next_at INTEGER,locked_until INTEGER DEFAULT 0,error TEXT DEFAULT '',created_at INTEGER);
          CREATE TABLE IF NOT EXISTS email_codes(email TEXT PRIMARY KEY,hash TEXT,expires INTEGER,attempts INTEGER DEFAULT 0,consumed INTEGER DEFAULT 0);''')
    def config(self,db=None):
        if db is None:
            with self.store.connect() as db:return self.config(db)
        row=db.execute("SELECT value FROM console_settings WHERE key='smtp_settings'").fetchone()
        return {**MAIL_DEFAULTS,**(json.loads(self.cipher.decrypt(json.loads(row['value']).encode())) if row else {})}
    def public(self):
        cfg=self.config();return {**{k:v for k,v in cfg.items() if k!='password'},'password_configured':bool(cfg['password'])}
    def save(self,payload):
        if not isinstance(payload,dict) or set(payload)-MAIL_DEFAULTS.keys():raise ValueError('invalid_smtp')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self.config(db)
            if payload.get('version')!=old['version']:raise ValueError('settings_conflict')
            cfg={**old,**payload};cfg['password']=payload.get('password') or old['password']
            for key in ('enabled','registration_code','notify_registration','notify_payments','notify_risk'):
                if type(cfg[key])is not bool:raise ValueError('invalid_smtp')
            if not isinstance(cfg['host'],str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',cfg['host']) or type(cfg['port'])is not int or not 1<=cfg['port']<=65535 or cfg['security'] not in {'ssl','starttls'}:raise ValueError('invalid_smtp')
            for key in ('username','password','sender','sender_name'):
                if not isinstance(cfg[key],str) or len(cfg[key])>512 or any(c in cfg[key] for c in '\r\n'):raise ValueError('invalid_smtp')
            if cfg['enabled'] and (not email_address(cfg['sender']) or not cfg['username'] or not cfg['password']):raise ValueError('smtp_credentials_required')
            if cfg['registration_code'] and not cfg['enabled']:raise ValueError('smtp_required_for_verification')
            cfg['version']=old['version']+1
            db.execute("INSERT INTO console_settings VALUES ('smtp_settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(self.cipher.encrypt(json.dumps(cfg).encode()).decode()),))
        return self.public()
    def template(self,event,language,db=None):
        if event not in EVENTS or language not in {'zh','en'}:raise ValueError('invalid_template')
        if db is None:
            with self.store.connect() as db:return self.template(event,language,db)
        row=db.execute('SELECT subject,html FROM mail_templates WHERE event=? AND language=?',(event,language)).fetchone()
        return dict(row) if row else default_template(event,language)
    def save_template(self,event,language,template):
        self.template(event,language);render(template,{})
        with self.store.connect() as db:db.execute('INSERT INTO mail_templates VALUES (?,?,?,?) ON CONFLICT(event,language) DO UPDATE SET subject=excluded.subject,html=excluded.html',(event,language,template['subject'],template['html']))
        return template
    def enqueue(self,event,email,variables,dedupe,language='zh',db=None):
        if db is None:
            with self.store.connect() as db:return self.enqueue(event,email,variables,dedupe,language,db)
        if not self.config(db)['enabled'] or not email_address(email):return
        body=render(self.template(event,language,db),{'site_name':'冰云支付','recipient_email':email,'recipient_name':email.split('@')[0],**variables})
        payload=self.cipher.encrypt(json.dumps({'to':email,**body}).encode())
        db.execute('INSERT OR IGNORE INTO mail_jobs(dedupe,payload,next_at,created_at) VALUES (?,?,?,?)',(dedupe,payload,int(time.time()),int(time.time())))
    def send(self,recipient,body):
        cfg=self.config()
        if not cfg['enabled']:raise ValueError('smtp_disabled')
        if not email_address(recipient):raise ValueError('invalid_email')
        msg=EmailMessage();msg['Subject']=body['subject'];msg['From']=formataddr((cfg['sender_name'],cfg['sender']));msg['To']=recipient
        msg.set_content(re.sub('<[^>]+>',' ',body['html']));msg.add_alternative(body['html'],subtype='html')
        context=ssl.create_default_context()
        client=smtplib.SMTP_SSL(cfg['host'],cfg['port'],timeout=12,context=context) if cfg['security']=='ssl' else smtplib.SMTP(cfg['host'],cfg['port'],timeout=12)
        with client:
            if cfg['security']=='starttls':client.starttls(context=context)
            client.login(cfg['username'],cfg['password']);client.send_message(msg)
    def tick(self):
        now=int(time.time())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');job=db.execute("SELECT * FROM mail_jobs WHERE status='pending' AND next_at<=? AND locked_until<=? ORDER BY id LIMIT 1",(now,now)).fetchone()
            if not job:return False
            db.execute('UPDATE mail_jobs SET attempts=attempts+1,locked_until=? WHERE id=?',(now+90,job['id']))
        error=''
        try:
            payload=json.loads(self.cipher.decrypt(job['payload']));self.send(payload.pop('to'),payload);status='sent'
        except Exception as exc:error=type(exc).__name__;status='failed' if job['attempts']>=4 else 'pending'
        with self.store.connect() as db:db.execute('UPDATE mail_jobs SET status=?,error=?,locked_until=0,next_at=?,payload=CASE WHEN ?=\'sent\' THEN NULL ELSE payload END WHERE id=?',(status,error,now+min(1800,30*2**job['attempts']),status,job['id']))
        self.trace('mail.'+status,'mail_delivery_'+status,{'job_id':job['id'],'error_type':error},accepted=status=='sent')
        return True
    def issue_code(self,email,ip):
        if not self.config()['registration_code']:raise ValueError('email_verification_disabled')
        if not email_address(email):raise ValueError('invalid_email')
        if not self.store.rate_limit('email-code:'+email,1,60) or not self.store.rate_limit('email-ip:'+ip,10,3600):raise ValueError('rate_limited')
        code=str(secrets.randbelow(1000000)).zfill(6);salt=secrets.token_hex(16);digest=salt+':'+hashlib.sha256((salt+code).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('INSERT INTO email_codes VALUES (?,?,?,0,0) ON CONFLICT(email) DO UPDATE SET hash=excluded.hash,expires=excluded.expires,attempts=0,consumed=0',(email,digest,int(time.time())+900))
            self.enqueue('verification',email,{'verification_code':code,'expires_in_minutes':15},'code:'+secrets.token_hex(16),db=db)
    def consume_code(self,email,code):
        if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code):raise ValueError('invalid_email_code')
        valid=False
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM email_codes WHERE email=?',(email,)).fetchone()
            if row and not row['consumed'] and row['expires']>time.time() and row['attempts']<5:
                salt,digest=row['hash'].split(':');valid=hmac.compare_digest(digest,hashlib.sha256((salt+code).encode()).hexdigest())
                db.execute('UPDATE email_codes SET attempts=attempts+1,consumed=? WHERE email=?',(int(valid),email))
        if not valid:raise ValueError('invalid_email_code')
