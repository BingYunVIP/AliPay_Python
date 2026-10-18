"""Browser acceptance for WeChat Pay, on isolated data with an in-process WeChat gateway.

Covers: filling the WeChat Pay configuration (specific explanations for wrong pastes, the online check), the four
payment products in the test-payment page, the confirmed state after a signed notice, refunds, the orders list and the
Epay-compatible cashier for type=wxpay. No real payment, refund or network call: the gateway lives in tests/wechat_fake.py.
Run with requirements-test.txt + Chromium. Set BYPAY_SMOKE_DIST to test a build that is not frontend/dist.
"""
import argparse
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
packages = list((ROOT / '.venv/lib').glob(f'python{sys.version_info.major}.{sys.version_info.minor}/site-packages'))
if packages: sys.path.insert(0, str(packages[0]))
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests'))

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server

from bypay.epay_protocol import public_pem, signed
from bypay.wechat_client import WechatClient
from web.app import create_app
from wechat_fake import FakeWechat

PORT = 18896
ORIGIN = f'http://127.0.0.1:{PORT}'


class Quiet(WSGIRequestHandler):
    def log(self, *args, **kwargs): pass


def run():
    parser = argparse.ArgumentParser(); parser.add_argument('--artifacts', default='/tmp/bypay-wechat-smoke'); args = parser.parse_args()
    evidence = Path(args.artifacts); evidence.mkdir(parents=True, exist_ok=True)
    failed = []

    def check(name, ok, detail=''):
        print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''), flush=True)
        if not ok: failed.append(name)

    fake = FakeWechat()
    with tempfile.TemporaryDirectory(prefix='bypay-wechat-smoke-') as folder:
        from cryptography.hazmat.primitives.asymmetric import rsa
        merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        app = create_app({'TESTING': True, 'DATABASE': str(Path(folder) / 'web.sqlite3'), 'PUBLIC_BASE_URL': ORIGIN, 'BYPAY_ADMIN_EMAIL': 'admin', 'BYPAY_ADMIN_PASSWORD': 'admin',
                          'ALLOW_REGISTRATION': 'true', 'BYPAY_TRUST_PROXY': '0', 'BYPAY_CALLBACK_HOSTS': 'merchant.example',
                          'FRONTEND_DIST': os.environ.get('BYPAY_SMOKE_DIST', str(ROOT / 'frontend/dist')),
                          'WECHAT_CLIENT_FACTORY': lambda cfg: WechatClient(cfg, transport=fake.transport)})
        # 冒烟只验收界面与流程：商户准入和退款服务费按测试基线打开（真实默认值见 tests/support.py）
        app.extensions['operations'].rules.save({'registration_review': False, 'registration_paid': False, 'deposit_enabled': False,
                                                 'risk_enabled': False, 'domain_add': True, 'domain_enforce': False,
                                                 'refund_fee_payer': 'platform', 'version': app.extensions['operations'].rules.read()['version']})
        # WeChat only accepts a public https notify_url, while this browser talks to the loopback address
        app.extensions['site_settings'].save({'origin': 'https://pay.example.test'})
        store = app.extensions['store']
        merchants = app.extensions['merchants']
        shop = store.register('shop@example.test', 'Shop-password-123')
        profile = merchants.update(shop['id'], {'merchant_public_key': public_pem(merchant_key)})
        server = make_server('127.0.0.1', PORT, app, threaded=True, request_handler=Quiet)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def deliver_notice(number):
            """What WeChat's server does after the buyer pays: a signed, encrypted notice to our notify_url."""
            fake.pay(number)
            headers, body = fake.notification(number)
            reply = app.test_client().post(f'/wechat/notify/{number}', data=body, headers=headers)
            assert reply.status_code == 204, reply.data

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(ORIGIN + '/login'); page.locator('#email').fill('admin'); page.locator('#password').fill('admin'); page.locator('form button.button').click(); page.wait_for_url('**/dashboard**')
                page.on('console', lambda m: errors.append('console:' + m.text) if m.type == 'error' else None)

                # ---- configuration: explain wrong pastes, accept the real files, check online
                page.goto(ORIGIN + '/dashboard?tab=channels'); page.wait_for_load_state('networkidle')
                card = page.locator('.payment-channel-card').filter(has_text='微信支付')
                check('WeChat card starts unconfigured and is marked integrated', '待配置' in card.inner_text() and '尚未接入' not in card.inner_text())
                card.get_by_role('button', name='配置渠道').click()
                dialog = page.locator('dialog.channel-editor[open]'); expect(dialog).to_be_visible()
                config = fake.config()
                for key in ('app_id', 'mch_id', 'certificate_serial', 'api_v3_key', 'platform_public_key_id'):
                    dialog.locator(f'#channel-{key}').fill(config[key])
                dialog.locator('#channel-platform_public_key').fill(config['platform_public_key'])
                certificate = '-----BEGIN CERTIFICATE-----\nMIIDfTCCAmWgAwIBAgIUEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLE\n-----END CERTIFICATE-----'
                dialog.locator('#channel-merchant_private_key').fill(certificate)
                dialog.get_by_role('button', name='保存配置').click()
                expect(dialog.locator('.error-box')).to_contain_text('这是证书，不是密钥', timeout=8000)
                check('a certificate pasted as the private key is explained, naming the field', '商户私钥' in dialog.locator('.error-box').inner_text())
                errors.clear()  # the browser logs the refusal above as a console error, which is the behaviour under test
                dialog.locator('#channel-merchant_private_key').fill('root@ByPC:~# cat apiclient_key.pem\n' + config['merchant_private_key'])
                dialog.locator('#channel-certificate_serial').fill('serial=' + fake.serial.lower())
                dialog.get_by_role('button', name='保存配置').click()
                expect(page.locator('.console-toast')).to_contain_text('渠道配置已保存', timeout=8000)
                card = page.locator('.payment-channel-card').filter(has_text='微信支付')
                check('after saving the card says 已配置 and shows the merchant number', '已配置' in card.inner_text() and fake.mch_id in card.inner_text())
                card.get_by_role('button', name='检查配置').click()
                expect(card.locator('.channel-check.ok')).to_contain_text('均验证通过', timeout=8000)
                page.screenshot(path=str(evidence / 'channels.png'))

                # ---- the four products
                page.goto(ORIGIN + '/dashboard?tab=testpay'); page.wait_for_load_state('networkidle')
                page.get_by_label('选择收款配置').select_option(label='微信支付')
                check('production badge is shown for WeChat Pay', '正式环境' in page.locator('.console-page-heading').inner_text())
                numbers = {}
                for product, label in (('page', '网页支付'), ('wap', '手机支付'), ('app', 'APP 支付'), ('mini', '小程序支付')):
                    page.get_by_role('button', name=label, exact=True).click()
                    if product == 'mini':
                        check('mini program asks for a WeChat openid only', page.locator('#buyer-type').count() == 0)
                        page.locator('#buyer-identity').fill('oFixtureOpenId0123456789ab')
                    page.get_by_label('商品名称', exact=True).fill(f'{label}微信测试')
                    page.get_by_role('button', name='创建支付订单').click()
                    expect(page.locator('.payment-qr-card.qr-wechat')).to_be_visible(timeout=15000); page.wait_for_timeout(600)
                    numbers[product] = page.locator('.created-payment code').inner_text()
                    head, left, amount, right = (page.locator(f'.payment-qr-card {sel}').first.bounding_box() for sel in ('.qr-receipt-header', '.qr-channel', '.qr-amount', '.qr-header-end'))
                    check(f'{product}: WeChat card header is channel | amount | countdown',
                          '微信支付' in page.locator('.payment-qr-card .qr-channel').inner_text() and left['x'] - head['x'] < 2 and abs(amount['x'] + amount['width'] / 2 - (head['x'] + head['width'] / 2)) < 3
                          and abs(right['x'] + right['width'] - (head['x'] + head['width'])) < 3 and page.locator('.payment-qr-card .qr-countdown').count() == 1)
                    if product in ('page', 'wap'):
                        svg = page.request.get(ORIGIN + page.locator('.payment-qr-card .qr-expand img').get_attribute('src')).text()
                        check(f'{product}: QR is compact, WeChat green, with the WeChat logo', int(svg.split('viewBox="0 0 ')[1].split(' ')[0]) < 70 and '#087c49' in svg and 'currentColor' in svg)
                    else:
                        expected = 'WeChat APP SDK' if product == 'app' else 'wx.requestPayment'
                        check(f'{product}: invocation parameters are shown with their label', expected in page.locator('.payment-qr-card .qr-code-head').inner_text() and 'sign' in page.locator('.payment-qr-card pre').inner_text().lower())
                    check(f'{product}: the open-checkout link exists only for the mobile order', page.get_by_role('link', name='打开微信支付收银台').count() == (1 if product == 'wap' else 0))
                    page.locator('.payment-qr-card').screenshot(path=str(evidence / f'card-{product}.png'))
                phone = app.test_client().get(f"/m/{store.short_link(numbers['wap'])}")
                check('the phone link goes to WeChat H5 with a return address', phone.status_code == 302 and phone.headers['Location'].startswith('https://wx.tenpay.com/') and 'redirect_url=' in phone.headers['Location'])

                # ---- payment confirmed by a signed notice, no reload
                page.get_by_role('button', name='网页支付', exact=True).click(); page.get_by_label('商品名称', exact=True).fill('到账测试'); page.get_by_role('button', name='创建支付订单').click()
                expect(page.locator('.payment-qr-card .qr-expand img')).to_be_visible(timeout=15000)
                paid_number = page.locator('.created-payment code').inner_text()
                forged = fake.notification(paid_number, key='f' * 32)
                fake.pay(paid_number)
                check('a notice that cannot be decrypted is refused', app.test_client().post(f'/wechat/notify/{paid_number}', data=forged[1], headers=forged[0]).status_code == 400)
                deliver_notice(paid_number)
                expect(page.locator('.payment-qr-card .qr-confirmed')).to_be_visible(timeout=10000)
                check('confirmed: no countdown, 已支付 badge', page.locator('.payment-qr-card .qr-countdown').count() == 0 and '已支付' in page.locator('.payment-qr-card .qr-header-end').inner_text())
                page.locator('.payment-qr-card').screenshot(path=str(evidence / 'card-confirmed.png'))

                # ---- orders list, details and a refund
                page.goto(ORIGIN + '/dashboard?tab=orders'); page.wait_for_load_state('networkidle')
                row = page.locator('.orders-table tbody tr').filter(has_text=paid_number)
                check('orders list names the WeChat Pay configuration with its logo', row.locator('.table-channel').inner_text().strip() == '微信支付' and row.locator('.table-channel .brand-image').count() == 1)
                row.get_by_role('button', name='详情').click(); detail = page.locator('.order-dialog[open]'); expect(detail).to_be_visible()
                check('details use WeChat wording', '微信支付交易号' in detail.inner_text() and '微信支付状态' in detail.inner_text() and detail.get_by_role('button', name='查询微信支付').count() == 1)
                page.keyboard.press('Escape')
                row.get_by_role('button', name='退款', exact=True).click(); refund = page.locator('.confirm-dialog[open]'); expect(refund).to_be_visible()
                refund.locator(f'#refund-amount-{paid_number}').fill('0.01'); refund.get_by_role('button', name='确认退款').click()
                expect(page.locator('.console-toast')).to_contain_text('退款已确认', timeout=8000)
                sent = json.loads(fake.calls[-1][2])
                check('the refund reached WeChat with cents and the transaction id', sent['amount'] == {'refund': 1, 'total': 1, 'currency': 'CNY'} and sent['transaction_id'] == store.order(paid_number)['trade_no'])
                page.wait_for_timeout(700)
                check('the order is now 已退款', '已退款' in page.locator('.orders-table tbody tr').filter(has_text=paid_number).inner_text())

                # ---- an Epay merchant paying with type=wxpay gets a WeChat cashier
                created = app.test_client().post('/mapi.php', data=signed({'pid': str(profile['merchant_id']), 'type': 'wxpay', 'out_trade_no': 'SHOP_WX_01', 'name': '商户微信商品', 'money': '0.01', 'notify_url': 'https://merchant.example/notify'}, profile['md5_key'], 'MD5')).json
                check('type=wxpay creates a cashier order', created.get('code') == 1, str(created))
                cashier = browser.new_page(viewport={'width': 480, 'height': 900}, locale='zh-CN')
                cashier.goto(ORIGIN + urlsplit(created['payurl']).path); cashier.wait_for_load_state('networkidle')
                expect(cashier.get_by_alt_text('微信付款二维码')).to_be_visible(timeout=15000)
                check('cashier names WeChat and shows the compact green code', '打开微信扫一扫' in cashier.locator('body').inner_text() and cashier.evaluate('document.querySelector(".cashier-qr img").naturalWidth') > 0)
                cashier.screenshot(path=str(evidence / 'cashier.png'))
                deliver_notice(created['trade_no'])
                expect(cashier.locator('.cashier-done')).to_contain_text('支付成功', timeout=10000)
                check('the merchant notification says wxpay', merchants.notification(created['trade_no'])['type'] == 'wxpay')
                check('no browser errors', not errors, str(errors[:4]))
                browser.close()
        finally:
            server.shutdown(); server.server_close()
    (evidence / 'result.json').write_text(json.dumps({'result': 'passed' if not failed else 'failed', 'failed': failed, 'external_services': 'in-process WeChat gateway fixture; no real payment, refund or network'}, ensure_ascii=False, indent=2))
    print(f"\n{'PASS' if not failed else 'FAIL'}: {len(failed)} failed")
    return 1 if failed else 0


if __name__ == '__main__': sys.exit(run())
