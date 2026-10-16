"""Browser acceptance for platform settings and operations using isolated data.
No real emails, charges, refunds or payouts. Run with requirements-test.txt + Chromium.
"""
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
packages=list((ROOT/'.venv/lib').glob(f'python{sys.version_info.major}.{sys.version_info.minor}/site-packages'))
if packages:sys.path.insert(0,str(packages[0]))
sys.path.insert(0,str(ROOT))
import json,tempfile,threading,time
from unittest.mock import patch
from playwright.sync_api import sync_playwright,expect
from werkzeug.serving import make_server,WSGIRequestHandler
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from web.app import create_app
from bypay.epay_protocol import public_pem

class Quiet(WSGIRequestHandler):
    def log(self,*args,**kwargs):pass

def run():
    parser=argparse.ArgumentParser();parser.add_argument('--artifacts',default='/tmp/bypay-platform-smoke');args=parser.parse_args();evidence=Path(args.artifacts);evidence.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bypay-platform-runtime-') as folder:
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        app=create_app({'TESTING':True,'DATABASE':str(Path(folder)/'web.sqlite3'),'PUBLIC_BASE_URL':'http://127.0.0.1:18889','BYPAY_ADMIN_EMAIL':'admin','BYPAY_ADMIN_PASSWORD':'admin','ALLOW_REGISTRATION':'true','ALIPAY_ENV':'sandbox','ALIPAY_APP_ID':'2026000000000000','ALIPAY_SELLER_ID':'2088000000000000','ALIPAY_PRIVATE_KEY':key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),'ALIPAY_PUBLIC_KEY':public_pem(key),'BYPAY_TRUST_PROXY':'0'})
        user=app.extensions['store'].register('review@example.test','Review-password-123');app.extensions['merchants'].ensure(user['id']);app.extensions['operations'].ensure(user['id'])
        server=make_server('127.0.0.1',18889,app,threaded=True,request_handler=Quiet);threading.Thread(target=server.serve_forever,daemon=True).start()
        origin='http://127.0.0.1:18889'
        try:
            with patch('web.cashier.fetch_cashier_qr',return_value='https://qr.alipay.com/platform-ui-fixture'),patch('web.mailing.smtplib.SMTP_SSL') as smtp,sync_playwright() as p:
                browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(origin);page.wait_for_load_state('networkidle');assert page.get_by_role('button',name='暂停动效',exact=True).count()==0
                page.goto(origin+'/login');page.locator('#email').fill('admin');page.locator('#password').fill('admin');page.locator('form button.button').click();page.wait_for_url('**/dashboard')
                page.goto(origin+'/dashboard?tab=users');page.wait_for_load_state('networkidle');url=page.url
                page.locator('table').get_by_role('button',name='接口配置',exact=True).first.click();dialog=page.get_by_role('dialog',name='商户接口配置')
                expect(dialog).to_be_visible();dialog.get_by_role('tab',name='V2 · RSA').click();expect(dialog.get_by_role('button',name='生成 RSA 密钥对',exact=True)).to_be_visible();assert page.url==url;dialog.get_by_role('tab',name='V1 · MD5').click();page.screenshot(path=str(evidence/'merchant-modal.png'));dialog.get_by_role('button',name='关闭接口配置').click()
                page.goto(origin+'/dashboard?tab=testpay');page.wait_for_load_state('networkidle');expect(page.get_by_label('选择收款配置')).to_be_visible();assert page.locator('.fixed-channel').count()==0
                page.get_by_label('商品名称',exact=True).fill('浏览器测试商品');page.get_by_role('button',name='创建支付订单',exact=True).click();expect(page.locator('.payment-qr-card .qr-expand img')).to_be_visible(timeout=15000)
                assert page.locator('.native-alipay-qr').count()==0;assert page.get_by_role('button',name='二维码失效时点此刷新').count()==0
                expect(page.locator('.qr-receipt-header')).to_contain_text('支付宝');expect(page.locator('.qr-receipt-header')).to_contain_text('0.01');expect(page.locator('.qr-countdown')).to_be_visible()
                qr=page.locator('.payment-qr-card .qr-expand img');response=page.request.get(origin+qr.get_attribute('src'));assert '<svg' in response.text() and '#075fbd' in response.text()
                page.screenshot(path=str(evidence/'payment-qr.png'))
                page.goto(origin+'/dashboard?tab=orders');page.wait_for_load_state('networkidle');page.get_by_label('支付场景筛选').select_option('page');expect(page.get_by_role('option',name='全部渠道',exact=True)).to_have_count(1)
                page.get_by_role('button',name='选择本页',exact=True).click()
                with page.expect_download() as download:page.get_by_role('button',name='导出选中 CSV',exact=True).click()
                downloaded=download.value;assert downloaded.suggested_filename.endswith('.csv')
                page.get_by_role('button',name='删除选中',exact=True).click();confirmation=page.get_by_role('dialog');confirmation.get_by_role('button',name='确认',exact=False).click();expect(page.get_by_text('暂无匹配订单',exact=True)).to_be_visible()
                page.get_by_label('订单列表范围').select_option('1');page.get_by_role('button',name='全选筛选结果',exact=True).click();page.get_by_role('button',name='恢复选中',exact=True).click();page.get_by_role('dialog').get_by_role('button',name='确认',exact=False).click()
                print('PASS no pause control, merchant modal V1/V2, single channel select, branded QR and batch/export',flush=True)
                page.goto(origin+'/dashboard?tab=settings');page.wait_for_load_state('networkidle');page.get_by_role('tab',name='邮箱与模板').click()
                expect(page.get_by_label('SMTP 服务器',exact=True)).to_have_value('smtp.qq.com')
                page.get_by_label('启用邮件服务',exact=True).check();page.get_by_label('SMTP 账号',exact=True).fill('sender@qq.com');page.get_by_label('SMTP 授权码',exact=True).fill('smtp-fixture-secret');page.get_by_label('发件邮箱',exact=True).fill('sender@qq.com');page.get_by_role('button',name='保存邮箱配置',exact=True).click();expect(page.get_by_text('邮箱配置已保存',exact=True)).to_be_visible()
                page.get_by_label('主题',exact=True).fill('[{{site_name}}] 浏览器模板测试');page.get_by_label('邮件 HTML 模板').fill('<html><body><h1>你好 {{recipient_name}}</h1><p>{{verification_code}}</p><script>parent.document.body.dataset.hacked="yes"</script></body></html>')
                preview=page.frame_locator('iframe[title="邮件实时预览"]');expect(preview.get_by_role('heading',name='你好 张三')).to_be_visible();assert page.evaluate('document.body.dataset.hacked')is None
                page.get_by_role('button',name='保存模板',exact=True).click();expect(page.get_by_text('模板已保存',exact=True)).to_be_visible()
                page.get_by_label('测试收件邮箱').fill('recipient@example.test');page.get_by_role('button',name='发送测试邮件',exact=True).click();expect(page.get_by_text('测试邮件已提交 SMTP 服务器',exact=True)).to_be_visible();smtp.return_value.send_message.assert_called_once()
                page.get_by_role('button',name='恢复默认模板',exact=True).click();expect(preview.get_by_text('您的验证码',exact=False)).to_be_visible()
                page.locator('.email-template-workbench').screenshot(path=str(evidence/'mail-template.png'))
                print('PASS QQ SMTP settings, masked secret, HTML template save, sandbox live preview and mock test send',flush=True)
                page.get_by_role('tab',name='商户与资金规则').click();expect(page.get_by_label('注册费用（元）',exact=True)).to_have_value('10.00')
                page.get_by_label('开启注册审核',exact=True).check();page.get_by_label('商品屏蔽关键词（每行一个）').fill('BLOCKED_ITEM')
                page.get_by_label('商品被屏蔽时显示内容',exact=True).fill('商品需人工确认');page.get_by_role('button',name='保存平台规则',exact=True).click();expect(page.get_by_text('平台规则已保存，新订单使用新规则',exact=True)).to_be_visible()
                page.get_by_role('tab',name='人机验证').click();page.get_by_label('Site Key',exact=True).fill('fixture-site');page.get_by_label('Secret Key',exact=True).fill('fixture-secret');page.get_by_label('允许验证的域名（每行一个，不含协议和端口）').fill('localhost\n127.0.0.1');page.get_by_role('button',name='保存配置',exact=True).click();expect(page.get_by_text('Turnstile 配置已保存',exact=True)).to_be_visible()
                page.get_by_role('tab',name='计划任务').click();expect(page.get_by_text('账本借贷平衡检查',exact=True)).to_be_visible();page.get_by_role('button',name='执行一次平台任务',exact=True).click()
                page.goto(origin+'/dashboard?tab=account');page.wait_for_load_state('networkidle');expect(page.get_by_role('heading',name='商户接入状态')).to_be_visible();expect(page.get_by_role('heading',name='账务流水')).to_be_visible()
                page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(evidence/'account-mobile.png'),full_page=True);assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors,errors
                browser.close()
                print('PASS policy save, Turnstile config, task run, account/settlement page, no browser errors',flush=True)
            (evidence/'result.json').write_text(json.dumps({'result':'passed','external_services':'isolated mocks; no live SMTP/Turnstile/payment/payout','browser_errors':errors},ensure_ascii=False,indent=2))
        finally:server.shutdown();server.server_close()

if __name__=='__main__':run()
