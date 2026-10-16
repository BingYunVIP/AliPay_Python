"""Repeatable browser smoke: real Pay/demo HTTP, fixture Alipay, no real payment.

Run with a Python environment containing project requirements and Playwright.
Only isolated temporary credentials/databases are created; live services are untouched.
"""
import argparse
import base64
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
# Local tooling can use the application's venv while Playwright lives in the host env
if (ROOT/'.venv/lib').exists():
    packages=list((ROOT/'.venv/lib').glob(f'python{sys.version_info.major}.{sys.version_info.minor}/site-packages'))
    if packages:sys.path.insert(0,str(packages[0]))
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'merchant-demo'))

import httpx
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from dotenv import set_key
from playwright.sync_api import sync_playwright,expect
from werkzeug.serving import make_server
from werkzeug.serving import WSGIRequestHandler
from web.app import create_app as create_pay
from web.webhooks import deliver_one
from bypay.alipay_client import AlipayClient
from bypay.epay_protocol import private_key,public_pem
from bypay import logger


class QuietHandler(WSGIRequestHandler):
    def log(self,*args,**kwargs):pass


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def run():
    parser=argparse.ArgumentParser();parser.add_argument('--artifacts');args=parser.parse_args()
    evidence=Path(args.artifacts) if args.artifacts else Path(tempfile.mkdtemp(prefix='bypay-epay-smoke-'))
    evidence.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bypay-epay-runtime-') as folder:
        data=Path(folder);pay_port,demo_port=free_port(),free_port()
        pay_origin=f'http://127.0.0.1:{pay_port}';demo_origin=f'http://127.0.0.1:{demo_port}'
        logger.configure(data/'sdk-logs',console=False)
        app_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        provider=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        private=app_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
        pay=create_pay({'TESTING':True,'PUBLIC_BASE_URL':f'http://localhost:{pay_port}','BYPAY_LOCAL_CALLBACK_PORT':demo_port,'DATABASE':str(data/'pay/web.sqlite3'),
                        'ALLOW_REGISTRATION':'true','BYPAY_ADMIN_EMAIL':'fixture-admin','BYPAY_ADMIN_PASSWORD':'Fixture-password-123',
                        'BYPAY_TRUST_PROXY':'0','ALIPAY_ENV':'sandbox','ALIPAY_APP_ID':'2026000000000000','ALIPAY_SELLER_ID':'2088000000000000',
                        'ALIPAY_PRIVATE_KEY':private,'ALIPAY_PUBLIC_KEY':public_pem(provider),'ALIPAY_PRIVATE_KEY_FILE':'','ALIPAY_PUBLIC_KEY_FILE':'','ALIPAY_NOTIFY_URL':'',
                        'FRONTEND_DIST':os.environ.get('BYPAY_SMOKE_DIST',str(ROOT/'frontend/dist'))})
        env=data/'demo/.env';env.parent.mkdir();env.touch(mode=0o600)
        for key,value in {'PAY_ORIGIN':pay_origin,'DEMO_ORIGIN':demo_origin,'PAY_API_VERSION':'v1'}.items():set_key(env,key,value)
        spec=importlib.util.spec_from_file_location('merchant_smoke',ROOT/'merchant-demo/app.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        demo=module.create_app({'TESTING':True,'CONFIG_FILE':str(env),'PAY_ORIGIN':pay_origin,'DEMO_ORIGIN':demo_origin,'DEMO_PORT':str(demo_port),'DATABASE':str(data/'demo/web.sqlite3')})
        servers=[make_server('127.0.0.1',pay_port,pay,threaded=True,request_handler=QuietHandler),make_server('127.0.0.1',demo_port,demo,threaded=True,request_handler=QuietHandler)]
        for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with patch('web.cashier.fetch_cashier_qr',return_value='https://qr.alipay.com/epay-smoke-fixture'),sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                merchant=browser.new_page(viewport={'width':1440,'height':1080});errors=[];posted=[]
                merchant.on('pageerror',lambda e:errors.append(str(e)))
                merchant.on('request',lambda r:posted.append(r.post_data or '') if r.method=='POST' else None)
                merchant.goto(pay_origin+'/register');merchant.wait_for_load_state('networkidle')
                merchant.locator('#email').fill('smoke-merchant@example.test');merchant.locator('#password').fill('Fixture-password-123');merchant.locator('form button.button').click()
                merchant.wait_for_url('**/dashboard')
                merchant.goto(pay_origin+'/dashboard?tab=account');merchant.wait_for_load_state('networkidle')
                merchant.get_by_role('button',name='接口配置',exact=True).click();merchant_api=merchant.get_by_role('dialog',name='商户接口配置')
                merchant_api.get_by_role('tab',name='V2 · RSA').click()
                expect(merchant.get_by_role('button',name='生成 RSA 密钥对',exact=True)).to_be_visible()
                merchant.get_by_role('button',name='生成 RSA 密钥对',exact=True).click()
                merchant.locator('dialog').get_by_role('button',name='确认生成',exact=True).click()
                field=merchant.get_by_label('商户私钥',exact=True);expect(field).to_be_visible(timeout=15000)
                merchant_private=field.input_value()
                response=merchant.request.get(pay_origin+'/api/merchant/config');assert response.status==200
                profile=response.json();assert profile['merchant_public_key']==public_pem(private_key(merchant_private))
                assert not any('PRIVATE KEY' in value for value in posted),'Merchant private key left the browser'
                merchant.locator('dialog').get_by_role('button',name='关闭',exact=True).click()
                merchant.screenshot(path=str(evidence/'merchant-config.png'),full_page=True)
                print('PASS registration, merchant ID, browser RSA key generation, public-key upload',flush=True)

                context=browser.new_context(viewport={'width':1440,'height':1080})
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(demo_origin);page.wait_for_load_state('networkidle')
                page.locator('#config-merchant-id').fill(str(profile['merchant_id']))
                page.locator('#config-demo-origin').fill(f'http://localhost:{demo_port}')
                page.locator('#config-md5').fill(profile['md5_key'])
                for version in ('v1','v2'):
                    page.locator('#config-version').select_option(version)
                    if version=='v2':
                        page.locator('#config-private-key').fill(merchant_private);page.locator('#config-platform-key').fill(profile['platform_public_key'])
                    with pay.extensions['store'].connect() as db:before=db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
                    page.locator('#check-config').click();expect(page.locator('#config-message')).to_contain_text('接口验证通过',timeout=15000);expect(page.locator('#config-fields')).to_be_enabled()
                    with pay.extensions['store'].connect() as db:assert db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==before
                    page.locator('#save-config').click();expect(page.locator('#config-message')).to_contain_text('商户接口已保存');expect(page.locator('#submit')).to_be_enabled()
                    assert not page.locator('#config-md5').input_value();assert not page.locator('#config-private-key').input_value()
                    page.locator('#submit').click();expect(page.locator('#message')).to_contain_text('订单已创建',timeout=15000)
                    frame=page.frame_locator('#cashier')
                    expect(frame.get_by_alt_text('支付宝付款二维码')).to_be_visible(timeout=20000)
                    assert frame.locator('.byh-nav').count()==0
                    assert frame.locator('.cashier-amount').inner_text().find('0.01')>=0
                    with httpx.Client(trust_env=False,timeout=10) as client:
                        row=client.get(demo_origin+'/api/state').json()['orders'][0]
                        assert row['api_version']==version and row['status']=='WAITING'
                        page.goto(demo_origin+'/return?out_trade_no='+row['number']+'&trade_status=TRADE_SUCCESS')
                        page.wait_for_load_state('networkidle')
                        expect(page.locator('#payment-success')).to_be_hidden()
                        expect(page.frame_locator('#cashier').get_by_alt_text('支付宝付款二维码')).to_be_visible()
                        # Even a forged notification with the apparent cashier source only triggers a server query
                        page.evaluate('''({origin,trade})=>window.dispatchEvent(new MessageEvent('message',{origin,source:document.querySelector('#cashier').contentWindow,data:{type:'bypay:paid',trade_no:trade}}))''',{'origin':pay_origin,'trade':row['platform_no']})
                        page.wait_for_load_state('networkidle')
                        assert client.get(demo_origin+'/api/state').json()['orders'][0]['status']=='WAITING'
                        page.screenshot(path=str(evidence/f'{version}-embedded-cashier.png'),full_page=True)
                        params={'app_id':'2026000000000000','seller_id':'2088000000000000','out_trade_no':row['platform_no'],'total_amount':'0.01','trade_no':'20261016'+row['platform_no'][-14:],'trade_status':'TRADE_SUCCESS','sign_type':'RSA2'}
                        params['sign']=base64.b64encode(provider.sign(AlipayClient.build_sign_content(params).encode(),padding.PKCS1v15(),hashes.SHA256())).decode()
                        assert client.post(pay_origin+'/alipay/notify',data=params).text=='success'
                        assert deliver_one(pay.extensions['store'],pay.extensions['webhook_signer'],pay.extensions['trace'],pay.extensions['callback_hosts'],local_origins=pay.extensions['callback_local_origins'])
                        assert client.get(demo_origin+'/api/state').json()['orders'][0]['status']=='PAID'
                    page.locator('#refresh').click();expect(page.locator('#payment-success')).to_be_visible(timeout=12000)
                    expect(page.locator('#success-amount')).to_contain_text('0.01')
                    page.screenshot(path=str(evidence/f'{version}-verified-success.png'),full_page=True)
                    print(f'PASS {version}: configure, signed create, embedded platform QR, forged-browser-message rejection, signed callback, merchant success',flush=True)
                page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(evidence/'mobile-success.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors,errors
                assert merchant_private not in (data/'demo/logs/demo.jsonl').read_text()
                browser.close()
            (evidence/'result.json').write_text(json.dumps({'provider':'isolated fixture, no real payment','versions':['v1','v2'],'browser_errors':errors,'result':'passed'},ensure_ascii=False,indent=2))
            print('Artifacts: '+str(evidence),flush=True)
        finally:
            for server in servers:server.shutdown();server.server_close()


if __name__=='__main__':run()
