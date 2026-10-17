"""Browser acceptance for platform settings, the event log and settlements, on isolated data.
No real emails, charges, refunds or payouts. Run with requirements-test.txt + Chromium.
Set BYPAY_SMOKE_DIST to test a build that is not frontend/dist.
"""
import argparse
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
packages = list((ROOT / '.venv/lib').glob(f'python{sys.version_info.major}.{sys.version_info.minor}/site-packages'))
if packages: sys.path.insert(0, str(packages[0]))
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server

from bypay.epay_protocol import public_pem
from web.app import create_app

PORT = 18889
ORIGIN = f'http://127.0.0.1:{PORT}'


class Quiet(WSGIRequestHandler):
    def log(self, *args, **kwargs): pass


def run():
    parser = argparse.ArgumentParser(); parser.add_argument('--artifacts', default='/tmp/bypay-platform-smoke'); args = parser.parse_args()
    evidence = Path(args.artifacts); evidence.mkdir(parents=True, exist_ok=True)
    failed = []

    def check(name, ok, detail=''):
        print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''), flush=True)
        if not ok: failed.append(name)

    with tempfile.TemporaryDirectory(prefix='bypay-platform-runtime-') as folder:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        app = create_app({'TESTING': True, 'DATABASE': str(Path(folder) / 'web.sqlite3'), 'PUBLIC_BASE_URL': ORIGIN, 'BYPAY_ADMIN_EMAIL': 'admin', 'BYPAY_ADMIN_PASSWORD': 'admin', 'ALLOW_REGISTRATION': 'true',
                          'ALIPAY_ENV': 'sandbox', 'ALIPAY_APP_ID': '2026000000000000', 'ALIPAY_SELLER_ID': '2088000000000000',
                          'ALIPAY_PRIVATE_KEY': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(), 'ALIPAY_PUBLIC_KEY': public_pem(key),
                          'BYPAY_TRUST_PROXY': '0', 'FRONTEND_DIST': os.environ.get('BYPAY_SMOKE_DIST', str(ROOT / 'frontend/dist'))})
        # 冒烟只验收界面与流程：商户准入和退款服务费按测试基线打开（真实默认值见 tests/support.py）
        app.extensions['operations'].rules.save({'registration_review': False, 'registration_paid': False, 'deposit_enabled': False,
                                                 'risk_enabled': False, 'domain_add': True, 'domain_enforce': False,
                                                 'refund_fee_payer': 'platform', 'version': app.extensions['operations'].rules.read()['version']})
        store, ops = app.extensions['store'], app.extensions['operations']
        user = store.register('review@example.test', 'Review-password-123'); app.extensions['merchants'].ensure(user['id']); ops.ensure(user['id'])
        # three real settlement requests: money in the merchant's balance, a payout account, manual settlement on
        with store.connect() as db: ops.journal(db, 'smoke:fund', 'payment', 'SMOKE', user['id'], [('available', user['id'], -100000), ('clearing', 0, 100000)])
        ops.rules.save({'settlement_mode': 'manual', 'settlement_days': 0, 'settlement_daily_limit': 10, 'version': ops.rules.read()['version']})
        ops.beneficiary(user['id'], {'channel': 'alipay', 'account': 'review@example.test', 'name': '评审用户'})
        settlements = [ops.settlement(user['id'], amount) for amount in ('50.00', '30.00', '20.00')]
        server = make_server('127.0.0.1', PORT, app, threaded=True, request_handler=Quiet)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def settlement(number):
            with store.connect() as db: return dict(db.execute('SELECT status,reference,paid_at FROM settlements WHERE id=?', (number,)).fetchone())

        def ledger_balanced():
            with store.connect() as db:
                return db.execute('SELECT COALESCE(SUM(cents),0) FROM ledger_lines').fetchone()[0] == 0 and db.execute('SELECT count(*) FROM (SELECT event_id FROM ledger_lines GROUP BY event_id HAVING sum(cents)!=0)').fetchone()[0] == 0

        try:
            with patch('web.cashier.fetch_cashier_qr', return_value='https://qr.alipay.com/platform-ui-fixture'), patch('web.mailing.smtplib.SMTP_SSL') as smtp, sync_playwright() as p:
                browser = p.chromium.launch(headless=True); page = browser.new_page(viewport={'width': 1440, 'height': 1050}); errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('console', lambda m: errors.append('console:' + m.text) if m.type == 'error' else None)
                page.goto(ORIGIN); page.wait_for_load_state('networkidle')
                check('no pause control on the home page', page.get_by_role('button', name='暂停动效', exact=True).count() == 0)
                page.goto(ORIGIN + '/login'); page.locator('#email').fill('admin'); page.locator('#password').fill('admin'); page.locator('form button.button').click(); page.wait_for_url('**/dashboard')
                errors.clear()  # the signed-out probes of the home and sign-in pages answer 401, as they should

                # ---- merchant interface dialog, test payment, orders (unchanged pages)
                page.goto(ORIGIN + '/dashboard?tab=users'); page.wait_for_load_state('networkidle'); url = page.url
                page.locator('table').get_by_role('button', name='接口配置', exact=True).first.click(); dialog = page.get_by_role('dialog', name='商户接口配置')
                expect(dialog).to_be_visible(); dialog.get_by_role('tab', name='V2 · RSA').click(); expect(dialog.get_by_role('button', name='生成 RSA 密钥对', exact=True)).to_be_visible()
                check('opening the merchant dialog keeps the address', page.url == url)
                dialog.get_by_role('tab', name='V1 · MD5').click(); page.screenshot(path=str(evidence / 'merchant-modal.png')); dialog.get_by_role('button', name='关闭接口配置').click()
                page.goto(ORIGIN + '/dashboard?tab=testpay'); page.wait_for_load_state('networkidle'); expect(page.get_by_label('选择收款配置')).to_be_visible()
                page.get_by_label('商品名称', exact=True).fill('浏览器测试商品'); page.get_by_role('button', name='创建支付订单', exact=True).click(); expect(page.locator('.payment-qr-card .qr-expand img')).to_be_visible(timeout=15000)
                expect(page.locator('.qr-receipt-header')).to_contain_text('支付宝'); expect(page.locator('.qr-receipt-header')).to_contain_text('0.01'); expect(page.locator('.qr-countdown')).to_be_visible()
                source = page.locator('.payment-qr-card .qr-expand img').get_attribute('src')  # a website order's image comes inline with the answer
                svg = unquote(source.split(',', 1)[1]) if source.startswith('data:image/svg+xml') else page.request.get(ORIGIN + source).text()
                check('Alipay QR is a branded svg', '<svg' in svg and '#075fbd' in svg)
                page.goto(ORIGIN + '/dashboard?tab=orders'); page.wait_for_load_state('networkidle'); page.get_by_label('支付场景筛选').select_option('page'); expect(page.get_by_role('option', name='全部渠道', exact=True)).to_have_count(1)
                page.get_by_role('button', name='全选', exact=True).click()
                with page.expect_download() as download: page.get_by_role('button', name='导出选择 CSV', exact=True).click()
                check('selected orders export as csv', download.value.suggested_filename.endswith('.csv'))
                page.get_by_role('button', name='删除选中', exact=True).click(); page.get_by_role('dialog').get_by_role('button', name='确认', exact=False).click(); expect(page.get_by_text('暂无匹配订单', exact=True)).to_be_visible()
                page.get_by_label('订单列表范围').select_option('1'); page.get_by_role('button', name='全选', exact=True).click(); page.get_by_role('button', name='恢复选中', exact=True).click(); page.get_by_role('dialog').get_by_role('button', name='确认', exact=False).click()

                # ---- settings: sections, drafts and the save bar
                page.goto(ORIGIN + '/dashboard?tab=settings'); page.wait_for_load_state('networkidle')
                tabs = page.get_by_role('tablist', name='系统设置分类').get_by_role('tab')
                check('five settings sections, the first one open', tabs.count() == 5 and tabs.nth(0).get_attribute('aria-selected') == 'true')
                page.get_by_role('tab', name='商户与资金规则').click(); expect(page.locator('#policy-registration_fee')).to_be_visible()
                check('the open section is in the address', 'section=policy' in page.url)
                page.get_by_role('tab', name='商户与资金规则').press('ArrowRight'); expect(page.get_by_role('tab', name='邮箱与模板')).to_have_attribute('aria-selected', 'true')
                check('arrow keys move between sections', 'section=mail' in page.url)
                page.goto(ORIGIN + '/dashboard?tab=settings&section=policy'); page.wait_for_load_state('networkidle')
                check('a section can be opened by its address', page.get_by_role('tab', name='商户与资金规则').get_attribute('aria-selected') == 'true')
                page.get_by_role('tab', name='站点与订单').click(); expect(page.locator('#site-order_timeout_minutes')).to_be_visible()
                bar = page.locator('.form-footer')
                check('the save bar stays hidden while nothing changed', not bar.is_visible())
                geometry = page.evaluate("""() => [...document.querySelectorAll('.form-card')].map(card => {
                    const box = card.getBoundingClientRect()
                    const controls = [...card.querySelectorAll('.form-row:not(.stacked) .form-control > :is(.field, .unit-field, .form-switch)')].map(el => el.getBoundingClientRect())
                    return { right: box.right, gaps: controls.map(c => Math.round(box.right - c.right)), widths: controls.map(c => Math.round(c.width)) } })""")
                check('controls share one right edge inside every card', all(len(set(g['gaps'])) <= 1 for g in geometry if g['gaps']), str(geometry))
                check('the email domain allowlist starts with the four defaults', page.locator('#site-email_domains').locator('xpath=..').locator('.tag-chip').count() == 4)
                page.locator('#site-callback_hosts').fill('https://demo.example.test/path?x=1'); page.locator('#site-callback_hosts').press('Enter')
                check('a callback domain becomes a chip without scheme or path', page.locator('.tag-chip', has_text='demo.example.test').count() == 1 and page.locator('#site-callback_hosts').input_value() == '')
                page.locator('.tag-chip', has_text='demo.example.test').get_by_role('button').click()
                check('a chip can be removed with its cross', page.locator('.tag-chip', has_text='demo.example.test').count() == 0)
                page.locator('#site-order_timeout_minutes').fill('20')
                expect(bar).to_be_visible(); box = bar.bounding_box(); heading = page.locator('.console-page-heading').first.bounding_box()
                check('editing shows 放弃修改 and 保存设置 at the right of the page heading', box['y'] >= heading['y'] - 2 and box['y'] + box['height'] <= heading['y'] + heading['height'] + 2 and box['x'] + box['width'] <= heading['x'] + heading['width'] + 2 and box['x'] > heading['x'] + heading['width'] / 2, str(box))
                page.get_by_role('tab', name='人机验证').click(); page.get_by_role('tab', name='站点与订单').click()
                check('a draft survives switching sections', page.locator('#site-order_timeout_minutes').input_value() == '20')
                page.get_by_role('button', name='放弃修改').click()
                check('discarding restores the saved value and hides the bar', page.locator('#site-order_timeout_minutes').input_value() == '15' and not bar.is_visible())
                page.locator('#site-origin').fill('ftp://bad'); page.get_by_role('button', name='保存设置').click()
                expect(page.locator('#site-origin-error')).to_be_visible()
                check('a rejected value is named next to its field and focused', page.evaluate('document.activeElement.id') == 'site-origin' and page.locator('#site-origin').get_attribute('aria-invalid') == 'true')
                check('the bar says what to do', '请检查标出的设置' in bar.inner_text())
                errors.clear()  # the browser logs the refused save above as a console error, which is the behaviour under test
                page.get_by_role('button', name='放弃修改').click()
                dialogs = []
                page.once('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
                page.locator('#site-order_timeout_minutes').fill('21'); page.locator('.console-sidebar').get_by_role('button', name='订单记录', exact=True).click(); page.wait_for_timeout(400)
                check('leaving with unsaved changes asks first and can be cancelled', len(dialogs) == 1 and 'tab=settings' in page.url, str(dialogs))
                page.once('dialog', lambda d: d.accept()); page.locator('.console-sidebar').get_by_role('button', name='订单记录', exact=True).click(); page.wait_for_url('**tab=orders**')
                page.goto(ORIGIN + '/dashboard?tab=settings'); page.wait_for_load_state('networkidle')
                page.locator('#site-order_timeout_minutes').fill('16'); page.get_by_role('button', name='保存设置').click(); expect(page.get_by_text('设置已保存并生效', exact=True)).to_be_visible()
                page.reload(); page.wait_for_load_state('networkidle')
                check('a saved setting survives a reload', page.locator('#site-order_timeout_minutes').input_value() == '16')
                page.locator('#site-order_timeout_minutes').fill('15'); page.get_by_role('button', name='保存设置').click(); expect(page.get_by_text('设置已保存并生效', exact=True)).to_be_visible()
                page.screenshot(path=str(evidence / 'settings-site.png'))

                page.get_by_role('tab', name='邮箱与模板').click()
                expect(page.get_by_label('SMTP 服务器', exact=True)).to_have_value('smtp.qq.com')
                page.get_by_label('启用邮件服务', exact=True).check(); page.get_by_label('SMTP 账号', exact=True).fill('sender@qq.com'); page.get_by_label('SMTP 授权码', exact=True).fill('smtp-fixture-secret'); page.get_by_label('发件邮箱', exact=True).fill('sender@qq.com')
                page.get_by_role('button', name='保存邮箱配置', exact=True).click(); expect(page.get_by_text('邮箱配置已保存', exact=True)).to_be_visible()
                check('the saved authorization code is not shown again', page.get_by_label('SMTP 授权码', exact=True).input_value() == '' and '已保存' in (page.get_by_label('SMTP 授权码', exact=True).get_attribute('placeholder') or ''))
                page.get_by_label('主题', exact=True).fill('[{{site_name}}] 浏览器模板测试')
                page.get_by_label('邮件 HTML 模板').fill('<html><body><h1>你好 {{recipient_name}}</h1><p>{{verification_code}}</p><script>parent.document.body.dataset.hacked="yes"</script></body></html>')
                preview = page.frame_locator('iframe[title="邮件实时预览"]'); expect(preview.get_by_role('heading', name='你好 张三')).to_be_visible()
                check('the live preview cannot reach the console', page.evaluate('document.body.dataset.hacked') is None)
                page.get_by_role('button', name='保存模板', exact=True).click(); expect(page.get_by_text('模板已保存', exact=True)).to_be_visible()
                page.get_by_label('测试收件邮箱').fill('recipient@example.test'); page.get_by_role('button', name='发送测试邮件', exact=True).click(); expect(page.get_by_text('测试邮件已提交 SMTP 服务器', exact=True)).to_be_visible()
                check('the test mail reached the SMTP fixture once', smtp.return_value.send_message.call_count == 1)
                page.get_by_role('button', name='恢复默认模板', exact=True).click(); expect(preview.get_by_text('您的验证码', exact=False)).to_be_visible()
                check('restoring the default template is a draft until saved', page.locator('.form-footer').last.is_visible())
                page.locator('.mail-workbench').screenshot(path=str(evidence / 'mail-template.png'))
                page.get_by_role('button', name='放弃修改').last.click()
                print('PASS QQ SMTP settings, masked secret, HTML template save, sandbox live preview and mock test send', flush=True)

                page.get_by_role('tab', name='商户与资金规则').click(); expect(page.get_by_label('注册费用', exact=True)).to_have_value('10.00')
                page.get_by_label('平台服务费率', exact=True).fill('150'); page.get_by_role('button', name='保存平台规则', exact=True).click()
                check('a value outside its limit is refused next to its field', page.locator('#policy-fee_rate-error').is_visible() and '100' in page.locator('#policy-fee_rate-error').inner_text())
                page.get_by_label('平台服务费率', exact=True).fill('1.50')
                page.get_by_label('开启注册审核', exact=True).check(); page.get_by_label('商品屏蔽关键词', exact=True).fill('BLOCKED_ITEM'); page.get_by_label('商品屏蔽关键词', exact=True).press('Enter')
                page.get_by_label('商品被屏蔽时显示内容', exact=True).fill('商品需人工确认'); page.get_by_role('button', name='保存平台规则', exact=True).click(); expect(page.get_by_text('平台规则已保存，新订单使用新规则', exact=True)).to_be_visible()
                check('the policy is stored with the keyword list', 'BLOCKED_ITEM' in ops.rules.read()['blocked_keywords'] and ops.rules.read()['registration_review'] is True)
                page.screenshot(path=str(evidence / 'settings-policy.png'), full_page=True)
                page.get_by_role('tab', name='人机验证').click(); page.get_by_label('Site Key', exact=True).fill('fixture-site'); page.get_by_label('Secret Key', exact=True).fill('fixture-secret')
                page.get_by_label('允许验证的域名', exact=True).fill('localhost\n127.0.0.1'); page.get_by_role('button', name='保存配置', exact=True).click(); expect(page.get_by_text('Turnstile 配置已保存', exact=True)).to_be_visible()
                page.get_by_role('tab', name='计划任务').click(); expect(page.get_by_text('账本借贷平衡检查', exact=True)).to_be_visible(); page.get_by_role('button', name='执行一次平台任务', exact=True).click(); expect(page.locator('.console-toast')).to_be_visible()
                check('the ledger check reports balanced', '通过' in page.locator('.stat-tile').first.inner_text())
                print('PASS policy save, Turnstile config, task run', flush=True)

                # ---- event log
                page.goto(ORIGIN + '/dashboard?tab=logs'); page.wait_for_load_state('networkidle')
                rows = page.locator('.log-row')
                check('the log lists what happened, newest first, under a day heading', rows.count() >= 3 and page.locator('.log-day').first.inner_text() == '今天')
                first = rows.first.inner_text()
                check('events are described in words, not codes', 'platform_' not in first and 'settings' not in first.split('\n')[0].lower())
                page.get_by_label('日志分类').select_option('settings'); page.wait_for_timeout(500)
                chips = page.locator('.log-chip').all_inner_texts()
                check('the category filter narrows the log to that category', chips and set(chips) == {'系统设置'}, str(chips))
                check('the category is in the address', 'category=settings' in page.url)
                page.reload(); page.wait_for_load_state('networkidle')
                check('a filtered log survives a reload', page.get_by_label('日志分类').input_value() == 'settings')
                page.locator('.log-row').first.locator('summary').click(); expect(page.locator('.log-row[open] pre')).to_be_visible()
                check('an opened event shows its details', '{' in page.locator('.log-row[open] pre').inner_text())
                xs = page.evaluate("""() => [...document.querySelectorAll('.log-row > summary')].map(s => ['.log-time', '.log-chip', '.log-message', '.log-order'].map(sel => Math.round(s.querySelector(sel).getBoundingClientRect().left)))""")
                check('the log columns line up from row to row', len({tuple(row) for row in xs}) == 1, str(xs[:3]))
                page.get_by_role('button', name='待验证 / 异常').click(); page.wait_for_timeout(500)
                check('the attention filter hides routine events', page.locator('.log-row').count() == 0 and page.get_by_text('暂无匹配日志').is_visible())
                page.screenshot(path=str(evidence / 'logs.png'))

                # ---- settlements: three requests, one paid out, one cancelled
                page.goto(ORIGIN + '/dashboard?tab=settlements'); page.wait_for_load_state('networkidle')
                tiles = page.locator('.stat-tile').all_inner_texts()
                check('the pending tile adds up the money waiting for payout', '100.00' in tiles[0] and '3 笔' in tiles[0], str(tiles))
                check('the first view is the work to do', page.locator('tbody tr').count() == 3 and 'tab=settlements' in page.url)
                slots = page.evaluate("""() => [...document.querySelectorAll('.action-pair')].map(pair => [...pair.children].map(slot => Math.round(slot.getBoundingClientRect().left)))""")
                check('the two actions sit in the same slots on every row', len({tuple(s) for s in slots}) == 1, str(slots))
                page.screenshot(path=str(evidence / 'settlements.png'))
                row = page.locator('tbody tr').filter(has_text='50.00')
                row.get_by_role('button', name='登记出款').click(); confirm = page.get_by_role('dialog')
                expect(confirm).to_be_visible(); confirm.get_by_role('button', name='确认已出款').click()
                check('a payout needs a transfer reference', confirm.locator('#settlement-reference-error').is_visible() and settlement(settlements[0])['status'] == 'pending')
                confirm.get_by_label('渠道转账流水号').fill('ALI2026TEST0001'); confirm.get_by_role('button', name='确认已出款').click(); expect(page.get_by_text('已登记出款凭据', exact=True)).to_be_visible()
                check('the payout is recorded with its reference', settlement(settlements[0])['status'] == 'paid' and settlement(settlements[0])['reference'] == 'ALI2026TEST0001')
                check('the counts follow', page.get_by_role('button', name='待处理').inner_text().replace('\n', ' ').endswith('2') and page.get_by_role('button', name='已出款').inner_text().replace('\n', ' ').endswith('1'))
                page.locator('tbody tr').filter(has_text='30.00').get_by_role('button', name='取消申请').click(); confirm = page.get_by_role('dialog')
                expect(confirm).to_be_visible(); confirm.get_by_role('button', name='确认取消').click(); expect(page.get_by_text('结算申请已取消', exact=True)).to_be_visible()
                check('a cancelled settlement releases the money', settlement(settlements[1])['status'] == 'cancel' and ops.account(user['id'])['balances']['available'] == '930.00')
                page.get_by_role('button', name='已出款').click(); page.wait_for_timeout(300)
                check('the paid view shows the reference and has no actions', 'ALI2026TEST0001' in page.locator('tbody').inner_text() and page.get_by_role('button', name='登记出款').count() == 0)
                check('the filter is in the address', 'status=paid' in page.url)
                check('the books still balance', ledger_balanced())
                page.screenshot(path=str(evidence / 'settlements-paid.png'))

                # ---- every section fits the screen: tablet and phone
                for width, height in ((1024, 900), (390, 844)):
                    page.set_viewport_size({'width': width, 'height': height})
                    for url in ('?tab=settings&section=site', '?tab=settings&section=policy', '?tab=settings&section=mail', '?tab=settings&section=human', '?tab=settings&section=tasks', '?tab=logs', '?tab=settlements&status=all'):
                        page.goto(ORIGIN + '/dashboard' + url); page.wait_for_load_state('networkidle'); page.wait_for_timeout(300)
                        check(f'{url} fits a {width}px screen', page.evaluate('document.documentElement.scrollWidth <= innerWidth'), str(page.evaluate('document.documentElement.scrollWidth')))
                page.goto(ORIGIN + '/dashboard?tab=settlements&status=all'); page.wait_for_load_state('networkidle')
                check('on a phone a settlement is a card', page.evaluate("getComputedStyle(document.querySelector('.settlement-table tbody tr')).display") == 'grid')
                page.screenshot(path=str(evidence / 'settlements-mobile.png'), full_page=True)
                page.goto(ORIGIN + '/dashboard?tab=account'); page.wait_for_load_state('networkidle'); expect(page.get_by_role('heading', name='商户接入状态')).to_be_visible(); expect(page.get_by_role('heading', name='账务流水')).to_be_visible()
                page.screenshot(path=str(evidence / 'account-mobile.png'), full_page=True); check('the account page fits a phone', page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                unexpected = [e for e in errors if 'Blocked script execution' not in e]  # the sandboxed preview refusing the injected script is the point of that test
                check('no browser errors', not unexpected, str(unexpected[:4]))
                browser.close()
            (evidence / 'result.json').write_text(json.dumps({'result': 'passed' if not failed else 'failed', 'failed': failed, 'external_services': 'isolated mocks; no live SMTP/Turnstile/payment/payout'}, ensure_ascii=False, indent=2))
        finally:
            server.shutdown(); server.server_close()
    print(f"\n{'PASS' if not failed else 'FAIL'}: {len(failed)} failed")
    return 1 if failed else 0


if __name__ == '__main__': sys.exit(run())
