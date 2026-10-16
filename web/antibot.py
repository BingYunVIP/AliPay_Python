"""Cloudflare Turnstile: server verification plus hostname/action binding."""
import json
from urllib.parse import urlsplit
import httpx

DEFAULTS={'enabled':False,'site_key':'','secret_key':'','hostnames':[],'version':0}


class AntiBot:
    def __init__(self,store,cipher):self.store,self.cipher=store,cipher
    def config(self,db=None):
        if db is None:
            with self.store.connect() as db:return self.config(db)
        row=db.execute("SELECT value FROM console_settings WHERE key='turnstile'").fetchone()
        return {**DEFAULTS,**(json.loads(self.cipher.decrypt(json.loads(row['value']).encode())) if row else {})}
    def public(self):
        cfg=self.config();return {**{k:v for k,v in cfg.items() if k!='secret_key'},'secret_configured':bool(cfg['secret_key'])}
    def save(self,payload):
        if not isinstance(payload,dict) or set(payload)-DEFAULTS.keys():raise ValueError('invalid_turnstile')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self.config(db)
            if payload.get('version')!=old['version']:raise ValueError('settings_conflict')
            cfg={**old,**payload};cfg['secret_key']=payload.get('secret_key') or old['secret_key']
            if type(cfg['enabled'])is not bool or any(not isinstance(cfg[k],str) or len(cfg[k])>256 or any(ord(c)<33 for c in cfg[k]) for k in ('site_key','secret_key')):raise ValueError('invalid_turnstile')
            if not isinstance(cfg['hostnames'],list) or len(cfg['hostnames'])>32 or any(not isinstance(h,str) or not h or len(h)>253 or urlsplit('https://'+h).netloc!=h or any(c in h for c in '/:@?# ') for h in cfg['hostnames']):raise ValueError('invalid_turnstile_hostnames')
            cfg['hostnames']=sorted(set(h.lower() for h in cfg['hostnames']))
            if cfg['enabled'] and (not cfg['site_key'] or not cfg['secret_key'] or not cfg['hostnames']):raise ValueError('turnstile_configuration_required')
            cfg['version']=old['version']+1
            db.execute("INSERT INTO console_settings VALUES ('turnstile',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(self.cipher.encrypt(json.dumps(cfg).encode()).decode()),))
        return self.public()
    def verify(self,token,ip,action):
        cfg=self.config()
        if not cfg['enabled']:return
        if not isinstance(token,str) or not 1<=len(token)<=2048:raise ValueError('human_verification_required')
        try:
            with httpx.Client(timeout=httpx.Timeout(10,connect=4),trust_env=False,follow_redirects=False) as client:
                response=client.post('https://challenges.cloudflare.com/turnstile/v0/siteverify',data={'secret':cfg['secret_key'],'response':token,'remoteip':ip})
                if response.status_code!=200 or len(response.content)>16384:raise ValueError()
                data=response.json()
                if data.get('success')is not True or data.get('hostname','').lower() not in cfg['hostnames'] or data.get('action')!=action:raise ValueError()
        except (ValueError,httpx.HTTPError):raise ValueError('human_verification_failed') from None
