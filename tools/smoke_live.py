#!/usr/bin/env python3
"""Real configured-service acceptance; never overrides custom endpoints or invents payments."""
import argparse
import getpass
import json
import os
import sys
import time
from urllib.parse import urlsplit
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ready',action='store_true',help='Confirm the user has finished configuring the real service')
    parser.add_argument('--origin',default='http://127.0.0.1:888')
    parser.add_argument('--browser',action='store_true')
    parser.add_argument('--report',default=str(ROOT/'artifacts/live-acceptance.json'))
    args=parser.parse_args()
    if not args.ready:raise SystemExit('尚未开始真实冒烟：请先修改默认账号密码并填写真实渠道、钱包、RPC、报价、HTTPS和需要的回调/SMTP配置，告知完成后再使用 --ready')
    parsed=urlsplit(args.origin)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or not parsed.hostname or (parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in {'localhost','127.0.0.1','::1'})):
        raise SystemExit('验收地址须为公网HTTPS或本机HTTP地址')
    email=os.environ.get('BYPAY_SMOKE_EMAIL') or input('已修改的管理员邮箱：').strip()
    password=os.environ.get('BYPAY_SMOKE_PASSWORD') or getpass.getpass('管理员密码：')
    if email=='admin@pay.com' or password=='Admin123':raise SystemExit('请先完成首次凭据修改')
    origin=args.origin.rstrip('/');report={'started_at':int(time.time()),'checks':[],'funds_acceptance':'not performed by this script'}
    def record(name,ok,detail=''):
        report['checks'].append({'name':name,'passed':bool(ok),'detail':detail})
        print(('PASS ' if ok else 'FAIL ')+name+(' · '+str(detail) if detail else ''),flush=True)
    with httpx.Client(base_url=origin,timeout=70,follow_redirects=False,trust_env=False) as client:
        response=client.post('/api/auth/login',json={'email':email,'password':password},headers={'Origin':origin});response.raise_for_status()
        if response.json()['user'].get('must_change_credentials'):raise SystemExit('必须先修改初始凭据')
        client.headers.update({'Origin':origin,'X-CSRF-Token':response.json()['csrf_token']})
        networks=client.get('/api/admin/crypto-networks');networks.raise_for_status()
        for row in networks.json()['networks']:
            response=client.post('/api/admin/crypto-networks/'+row['network']+'/check');data=response.json()
            record('RPC '+row['network'],response.is_success and data.get('verified') is True,data.get('provider_code',''))
            time.sleep(2.2)
        # Use the saved addresses and credentials; no registry-contract address masquerades as the receiving wallet.
        from web.app import create_app
        from web.runtime import load_runtime
        from web.crypto import ASSETS
        app=create_app(load_runtime());channels=app.extensions['channels'];nets=app.extensions['crypto_networks'];gateway=app.extensions['crypto_gateway']
        configs=[channels.get(item['id'],item['config_id'])[0] for item in channels.all() if item['id']=='crypto' and item['configured']]
        for spec in ASSETS:
            cfg=next((item for item in configs if item.get('network')==spec.network),None)
            if not cfg:record('Asset '+spec.code,False,'missing configured public receiving wallet');continue
            result=gateway.check({**cfg,'asset':spec.asset},nets.get(spec.network))
            record('Asset '+spec.code,result.get('verified') is True,result.get('provider_code',''))
        page=1;all_channels=[]
        while True:
            response=client.get('/api/admin/channels',params={'page':page,'page_size':9});response.raise_for_status();data=response.json();all_channels.extend(data['channels'])
            if len(all_channels)>=data['total']:break
            page+=1
        for ch in all_channels:
            if ch['id'] not in ('alipay','wechat','crypto'):continue
            if not ch['configured']:record('Channel '+ch['name'],False,'real configuration missing');continue
            response=client.post('/api/admin/channel-configs/'+str(ch['config_id'])+'/check');data=response.json()
            record('Channel '+ch['name'],response.is_success and data.get('verified') is True,data.get('provider_code',''));time.sleep(2.2)
        response=client.post('/api/admin/rates/sync');record('Market sync',response.is_success,response.json().get('error',''))
        if args.browser:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':1440,'height':900},locale='zh-CN');errors=[]
                    page.on('pageerror',lambda exc:errors.append(str(exc)))
                    page.goto(origin+'/login');page.wait_for_load_state('networkidle')
                    page.locator('#email').fill(email);page.locator('#password').fill(password);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url('**/admin/overview')
                    for path in ['/admin/channels','/admin/orders','/admin/rates/history','/admin/rates/settings','/admin/settings/crypto','/admin/settings/nodes']:
                        page.goto(origin+path);page.wait_for_load_state('networkidle');record('Browser '+path,not errors,'; '.join(errors))
                    record('No browser exceptions',not errors,'; '.join(errors))
                finally:browser.close()
    report['finished_at']=int(time.time());report['passed']=bool(report['checks']) and all(item['passed'] for item in report['checks'])
    target=Path(args.report);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('Report: '+str(target))
    raise SystemExit(0 if report['passed'] else 1)

if __name__=='__main__':main()
