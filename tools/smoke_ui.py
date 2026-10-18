"""Browser acceptance for the console UI rules, on isolated data with a fixture Alipay.

Covers: the three-segment payment card for every product (waiting, confirmed, expired), the centred order
dialog, the orders table (filters, selection that survives searching, batch export/delete/restore), the refund
dialog validation, the three-slot row actions, and the merchant API dialog.
No real payment, refund or network call: Alipay is replaced by fixtures. Run with requirements-test.txt + Chromium.
Set BYPAY_SMOKE_DIST to test a build that is not frontend/dist.
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
packages = list((ROOT / '.venv/lib').glob(f'python{sys.version_info.major}.{sys.version_info.minor}/site-packages'))
if packages: sys.path.insert(0, str(packages[0]))
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server

from bypay.alipay_client import AlipayClient
from bypay.epay_protocol import public_pem
from web.app import create_app

PORT = 18895
ORIGIN = f'http://127.0.0.1:{PORT}'


class Quiet(WSGIRequestHandler):
    def log(self, *args, **kwargs): pass


class Checks:
    def __init__(self): self.failed = []

    def __call__(self, name, ok, detail=''):
        print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''), flush=True)
        if not ok: self.failed.append(name)


def run():
    parser = argparse.ArgumentParser(); parser.add_argument('--artifacts', default='/tmp/bypay-ui-smoke'); args = parser.parse_args()
    evidence = Path(args.artifacts); evidence.mkdir(parents=True, exist_ok=True)
    check = Checks()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    refunds = []

    def mini(number, amount, subject, *, buyer=None):
        return {'is_success': True, 'out_trade_no': number, 'trade_no': 'MINI' + hashlib.sha1(number.encode()).hexdigest()[:20], 'total_amount': amount, 'sdk_response': {}}

    def refund(client, number, amount, reason, *, out_request_no='', trade_no=''):
        refunds.append((number, amount, reason))
        return {'is_success': True, 'out_trade_no': number, 'trade_no': trade_no, 'refund_fee': amount, 'sdk_response': {}}

    def query(client, out_trade_no='', trade_no=''):
        return {'is_success': False, 'code': '40004', 'sub_code': 'ACQ.TRADE_NOT_EXIST', 'sdk_response': {}}

    with tempfile.TemporaryDirectory(prefix='bypay-ui-smoke-') as folder:
        app = create_app({'TESTING': True, 'DATABASE': str(Path(folder) / 'web.sqlite3'), 'PUBLIC_BASE_URL': ORIGIN, 'BYPAY_ADMIN_EMAIL': 'admin', 'BYPAY_ADMIN_PASSWORD': 'admin',
            'ALLOW_REGISTRATION': 'true', 'ALIPAY_ENV': 'sandbox', 'ALIPAY_APP_ID': '2026000000000000', 'ALIPAY_SELLER_ID': '2088000000000000', 'BYPAY_TRUST_PROXY': '0',
            'FRONTEND_DIST': os.environ.get('BYPAY_SMOKE_DIST', str(ROOT / 'frontend/dist')),
            'ALIPAY_PRIVATE_KEY': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(), 'ALIPAY_PUBLIC_KEY': public_pem(key)})
        # 冒烟只验收界面与流程：商户准入和退款服务费按测试基线打开（真实默认值见 tests/support.py）
        app.extensions['operations'].rules.save({'registration_review': False, 'registration_paid': False, 'deposit_enabled': False,
                                                 'risk_enabled': False, 'domain_add': True, 'domain_enforce': False,
                                                 'refund_fee_payer': 'platform', 'version': app.extensions['operations'].rules.read()['version']})
        store = app.extensions['store']
        merchant = store.register('merchant@example.test', 'Merchant-password-123')
        app.extensions['merchants'].ensure(merchant['id']); app.extensions['operations'].ensure(merchant['id'])
        with store.connect() as db: db.execute("UPDATE merchant_access SET risk_hold=1,risk_reason='fixture' WHERE user_id=?", (merchant['id'],))
        for i in range(1, 31):
            store.create_order(1, {'out_trade_no': f'BULK_{i:03}', 'subject': f'批量订单 {i}', 'amount': '1.00', 'product': ['page', 'wap', 'app', 'mini'][i % 4]}, 'https://example.test', app.extensions['alipay_config'])

        def pay(number):
            order = store.order(number)
            params = {'out_trade_no': number, 'total_amount': order['amount'], 'trade_status': 'TRADE_SUCCESS', 'trade_no': '20261017' + hashlib.sha256(number.encode()).hexdigest()[:18],
                      'app_id': '2026000000000000', 'seller_id': '2088000000000000', 'sign_type': 'RSA2'}
            params['sign'] = base64.b64encode(key.sign(AlipayClient.build_sign_content(params).encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
            assert app.test_client().post('/alipay/notify', data=params).data == b'success'

        server = make_server('127.0.0.1', PORT, app, threaded=True, request_handler=Quiet)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        patches = [patch('web.cashier.fetch_cashier_qr', return_value='https://qr.alipay.com/ui-smoke-fixture'), patch.object(AlipayClient, 'create_mini_trade', side_effect=mini),
                   patch.object(AlipayClient, 'refund', autospec=True, side_effect=refund), patch.object(AlipayClient, 'query', autospec=True, side_effect=query)]
        for item in patches: item.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True, locale='zh-CN')
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(ORIGIN); page.wait_for_load_state('networkidle')
                check('window title is 冰云支付 - 聚合支付', page.title() == '冰云支付 - 聚合支付', page.title())
                check('home column heading is 产品形态', page.locator('.byh-col-h').first.inner_text() == '产品形态')
                page.get_by_role('button', name='Switch to English').click()
                check('English window title', page.title() == 'BingYun Pay - Payment Aggregation', page.title())
                page.get_by_role('button', name='切换到中文').click()
                page.goto(ORIGIN + '/login'); page.locator('#email').fill('admin'); page.locator('#password').fill('admin'); page.locator('form button.button').click(); page.wait_for_url('**/dashboard**')
                page.on('console', lambda m: errors.append('console:' + m.text) if m.type == 'error' else None)

                # ---- test payment page: removed elements and a card per product
                page.goto(ORIGIN + '/dashboard?tab=testpay'); page.wait_for_load_state('networkidle')
                check('sidebar has no merchant API page', page.locator('.console-sidebar nav').get_by_text('接口配置').count() == 0)
                check('removed: creation hint, context note, terminal footer, activity card',
                      page.get_by_text('创建订单，获取对应产品的支付入口').count() + page.locator('.context-note, .terminal-bottom, .payment-events').count() == 0)
                numbers = {}
                for product, label in (('page', '网页支付'), ('wap', '手机支付'), ('app', 'APP 支付'), ('mini', '小程序支付')):
                    page.get_by_role('button', name=label, exact=True).click()
                    if product == 'mini': page.locator('#buyer-identity').fill('smoke_OpenID_123456')
                    page.get_by_label('商品名称', exact=True).fill(f'{label}测试商品')
                    page.get_by_role('button', name='创建支付订单').click()
                    expect(page.locator('.payment-qr-card')).to_be_visible(timeout=15000); page.wait_for_timeout(700)
                    numbers[product] = page.locator('.created-payment code').inner_text()
                    box = lambda selector: page.locator(f'.payment-qr-card {selector}').first.bounding_box()
                    head, left, amount, right = box('.qr-receipt-header'), box('.qr-channel'), box('.qr-amount'), box('.qr-header-end')
                    check(f'{product}: header is channel | amount | countdown',
                          left['x'] - head['x'] < 2 and abs(amount['x'] + amount['width'] / 2 - (head['x'] + head['width'] / 2)) < 3
                          and abs(right['x'] + right['width'] - (head['x'] + head['width'])) < 3 and page.locator('.payment-qr-card .qr-countdown').count() == 1)
                    if product in ('page', 'wap'):
                        source = page.locator('.payment-qr-card .qr-expand img').get_attribute('src')
                        # a website order's image arrives with the answer that says its code is ready; a phone order's is loaded from its address
                        check(f'{product}: the image {"comes with the answer, no second request" if product == "page" else "is loaded from its address"}', source.startswith('data:image/svg+xml') == (product == 'page'), source[:40])
                        svg = unquote(source.split(',', 1)[1]) if source.startswith('data:') else page.request.get(ORIGIN + source).text(); size = int(svg.split('viewBox="0 0 ')[1].split(' ')[0])
                        check(f'{product}: QR is compact, brand coloured and carries the channel logo', size < 70 and '#075fbd' in svg and 'currentColor' in svg, f'modules={size}')
                        check(f'{product}: the image is drawn', page.locator('.payment-qr-card .qr-expand img').evaluate('image => image.complete && image.naturalWidth > 0'))
                    else:
                        check(f'{product}: call parameters shown in the same card', page.locator('.payment-qr-card .qr-code-block pre').inner_text().strip() != '')
                    page.locator('.payment-qr-card').screenshot(path=str(evidence / f'card-{product}.png'))
                short = app.extensions['store'].short_link(numbers['wap'])
                phone = app.test_client().get('/m/' + short)
                check('mobile short link redirects to the signed wap checkout', phone.status_code == 302 and 'alipay.trade.wap.pay' in phone.headers['Location'])

                # ---- confirmed and expired cards update without a reload
                page.get_by_role('button', name='网页支付', exact=True).click(); page.get_by_role('button', name='创建支付订单').click()
                expect(page.locator('.payment-qr-card .qr-expand img')).to_be_visible(timeout=15000)
                paid_number = page.locator('.created-payment code').inner_text(); pay(paid_number)
                expect(page.locator('.payment-qr-card .qr-confirmed')).to_be_visible(timeout=10000)
                badge = page.locator('.payment-qr-card .qr-header-end')
                check('confirmed: no countdown, 已支付 badge, QR gone', page.locator('.payment-qr-card .qr-countdown').count() == 0 and '已支付' in badge.inner_text() and page.locator('.payment-qr-card .qr-expand').count() == 0)
                page.locator('.payment-qr-card').screenshot(path=str(evidence / 'card-confirmed.png'))
                page.get_by_role('button', name='创建支付订单').click(); expect(page.locator('.payment-qr-card .qr-expand img')).to_be_visible(timeout=15000)
                with store.connect() as db: db.execute('UPDATE orders SET expires_at=? WHERE out_trade_no=?', (int(time.time()) - 5, page.locator('.created-payment code').inner_text()))
                expect(page.locator('.payment-qr-card')).to_contain_text('订单已过期', timeout=10000)
                check('expired: no countdown, 已过期 badge', page.locator('.payment-qr-card .qr-countdown').count() == 0 and '已过期' in page.locator('.payment-qr-card .qr-header-end').inner_text())

                # ---- order dialog opened from the test page
                page.get_by_role('button', name='查看订单详情与查询状态').click(); expect(page.locator('.order-dialog[open]')).to_be_visible()
                modal, sidebar = page.locator('.order-modal').bounding_box(), page.locator('.console-sidebar').bounding_box()
                centre = (sidebar['x'] + sidebar['width'] + 1440) / 2
                check('order dialog is centred in the content area, sidebar untouched', abs(modal['x'] + modal['width'] / 2 - centre) < 2 and modal['x'] > sidebar['x'] + sidebar['width'])
                check('dialog header: 订单详情 / 订单时间线 left, 查询支付宝 + close right', page.locator('.order-tabs button').all_inner_texts() == ['订单详情', '订单时间线']
                      and page.locator('.order-modal-actions .button-secondary').inner_text().strip() == '查询支付宝' and page.locator('.order-modal-actions .icon-button[aria-label="关闭详情"]').count() == 1)
                labels = [['商品名称', None, None], ['商户订单号', '支付产品', '支付宝交易号'], ['创建时间', '支付时间', '付款截止时间'], ['已退款金额', '支付宝状态', '商户回调地址']]
                rows = page.locator('.fact-row'); ok = rows.count() == 4
                for index, names in enumerate(labels):
                    cells = rows.nth(index).locator('.fact'); boxes = [cells.nth(i).bounding_box() for i in range(3)]
                    ok = ok and cells.count() == 3 and boxes[0]['x'] < boxes[1]['x'] < boxes[2]['x'] and all(not name or cells.nth(i).locator('span').first.inner_text() == name for i, name in enumerate(names))
                check('dialog rows: four rows, three segments each, labels as specified', ok)
                check('dialog shows the payment card', page.locator('.order-modal .payment-qr-card .qr-receipt-header').count() == 1)
                page.get_by_role('tab', name='订单时间线').click(); expect(page.locator('.order-timeline li').first).to_be_visible(); page.get_by_role('tab', name='订单详情').click()
                page.get_by_role('button', name='查询支付宝').click(); expect(page.locator('.order-modal .notice-box')).to_be_visible(timeout=8000)
                page.screenshot(path=str(evidence / 'order-dialog.png')); page.keyboard.press('Escape'); expect(page.locator('.order-dialog[open]')).to_have_count(0)

                # ---- orders table
                page.goto(ORIGIN + '/dashboard?tab=orders'); page.wait_for_load_state('networkidle')
                selects = page.locator('.table-toolbar select'); shown = [selects.nth(i).evaluate('e => e.selectedOptions[0].textContent') for i in range(selects.count())]
                check('toolbar: 全部状态 全部渠道 全部场景 正常订单 全部时间 on one row', shown == ['全部状态', '全部渠道', '全部场景', '正常订单', '全部时间'] and len({round(selects.nth(i).bounding_box()['y']) for i in range(5)}) == 1, str(shown))
                filter_row = page.locator('.table-filter-row')
                controls = [filter_row.get_by_role('button', name='全选', exact=True), filter_row.get_by_role('button', name='不选', exact=True), filter_row.locator('.selected-count'), filter_row.locator('.console-search'), filter_row.get_by_role('button', name='删除选中'), filter_row.get_by_role('button', name='导出CSV')]
                centres = [c.bounding_box()['y'] + c.bounding_box()['height'] / 2 for c in controls]; lefts = [c.bounding_box()['x'] for c in controls]
                check('filter row: 全选 不选 已选 0 笔 search 删除选中 导出CSV on one row', max(centres) - min(centres) <= 6 and lefts == sorted(lefts) and filter_row.locator('.selected-count').inner_text() == '已选 0 笔')
                check('removed: refresh icon, export-filtered button, selection toolbar', page.get_by_label('刷新订单').count() + page.get_by_role('button', name='导出筛选结果').count() + page.locator('.order-selection-toolbar, .order-row-actions').count() == 0)
                check('table headers', page.locator('.orders-table thead th').all_inner_texts()[1:] == ['商户 ID / 邮箱', '商品 / 订单号', '渠道 / 产品', '实付金额', '手续费', '交易状态', '创建时间', '支付时间', '用户IP', '操作'])
                first = page.locator('.orders-table tbody tr').first
                check('channel cell shows logo + configuration name; product alone below', first.locator('.table-channel').inner_text().strip() == '支付宝' and '·' not in first.locator('td').nth(3).inner_text())
                check('merchant column and fee column are filled', first.locator('td').nth(1).locator('code').inner_text().strip() != '' and first.locator('td').nth(4).inner_text().startswith('¥'))
                check('the client IP column shows the real address', first.locator('td').nth(9).inner_text().strip() not in ('', '—'))
                with store.connect() as db: total = db.execute('SELECT COUNT(*) FROM orders WHERE deleted_at IS NULL').fetchone()[0]
                filter_row.get_by_role('button', name='全选', exact=True).click(); expect(filter_row.locator('.selected-count')).to_have_text(f'已选 {total} 笔', timeout=6000)
                filter_row.locator('input').fill('批量订单 1'); page.wait_for_timeout(900); during = filter_row.locator('.selected-count').inner_text()
                filter_row.locator('input').fill(''); page.wait_for_timeout(900)
                check('searching and clearing the search keep the selection', during == f'已选 {total} 笔' and filter_row.locator('.selected-count').inner_text() == f'已选 {total} 笔', during)
                filter_row.get_by_role('button', name='不选', exact=True).click()
                check('不选 clears; batch buttons disabled', filter_row.locator('.selected-count').inner_text() == '已选 0 笔' and filter_row.get_by_role('button', name='删除选中').is_disabled())
                body_rows = page.locator('.orders-table tbody tr')
                body_rows.nth(2).locator('input[type=checkbox]').check(); body_rows.nth(3).locator('input[type=checkbox]').check()
                picked = [body_rows.nth(i).locator('td').nth(2).locator('code').inner_text() for i in (2, 3)]
                with page.expect_download() as download: filter_row.get_by_role('button', name='导出CSV').click()
                exported = open(download.value.path(), encoding='utf-8-sig').read()
                check('selected export contains exactly the selected orders', all(n in exported for n in picked) and len(exported.strip().splitlines()) == 3, exported[:200])
                filter_row.get_by_role('button', name='删除选中').click(); page.locator('.confirm-dialog[open]').get_by_role('button', name='确认删除').click(); expect(page.locator('.console-toast')).to_contain_text('已处理 2 笔', timeout=8000)
                page.locator('.table-toolbar select').nth(3).select_option('1'); page.wait_for_timeout(800)
                filter_row.get_by_role('button', name='全选', exact=True).click(); expect(filter_row.locator('.selected-count')).to_have_text('已选 2 笔', timeout=6000)
                filter_row.get_by_role('button', name='恢复选中').click(); page.locator('.confirm-dialog[open]').get_by_role('button', name='确认恢复').click(); expect(page.locator('.console-toast')).to_contain_text('已处理 2 笔', timeout=8000)
                page.locator('.table-toolbar select').nth(3).select_option('0'); page.wait_for_timeout(600)

                # ---- refund dialog (the provider call itself is a fixture)
                filter_row.locator('input').fill(paid_number); page.wait_for_timeout(900)
                row = page.locator('.orders-table tbody tr').first
                slots = row.locator('.action-triple > span')
                check('row actions: 详情 | 退款 | 删除 in three slots', [slots.nth(i).inner_text().strip() for i in range(3)] == ['详情', '退款', '删除'])
                row.get_by_role('button', name='退款', exact=True).click(); dialog = page.locator('.confirm-dialog[open]'); expect(dialog).to_be_visible()
                icon, title = dialog.locator('.confirm-icon').bounding_box(), dialog.locator('.confirm-head h2').bounding_box()
                cells = dialog.locator('.confirm-summary > div'); spans = [cells.nth(i).bounding_box() for i in range(3)]
                amount_input = dialog.locator(f'#refund-amount-{paid_number}'); confirm, cancel = dialog.get_by_role('button', name='确认退款'), dialog.get_by_role('button', name='取消')
                frame = dialog.bounding_box()
                check('refund dialog: icon + 确认单笔退款 on one row', abs(icon['y'] + icon['height'] / 2 - (title['y'] + title['height'] / 2)) < 4 and dialog.locator('h2').inner_text() == '确认单笔退款')
                check('refund dialog: item | amount | order number on one row', spans[0]['x'] < spans[1]['x'] < spans[2]['x'] and abs(spans[0]['y'] - spans[2]['y']) < 6 and paid_number in cells.nth(2).inner_text())
                check('refund dialog: amount not pre-filled, old description removed', amount_input.input_value() == '' and dialog.get_by_text('款项将按原支付路径退回').count() == 0)
                check('refund dialog: cancel left, confirm right', cancel.bounding_box()['x'] < frame['x'] + 40 and confirm.bounding_box()['x'] + confirm.bounding_box()['width'] > frame['x'] + frame['width'] - 40)
                state = [confirm.is_disabled()]
                for value in ('0.02', 'abc', '0'): amount_input.fill(value); state.append(confirm.is_disabled() and dialog.locator('.field-error').count() == 1)
                amount_input.fill('0.01'); state.append(confirm.is_enabled() and dialog.locator('.field-error').count() == 0)
                check('refund dialog: amount above payment, text and zero are rejected; valid enables confirm', all(state), str(state))
                dialog.screenshot(path=str(evidence / 'refund-dialog.png'))
                confirm.click(); expect(page.locator('.console-toast')).to_contain_text('退款已确认', timeout=8000)
                check('refund reached the fixture provider exactly once', len(refunds) == 1 and refunds[0][0] == paid_number, str(refunds))

                # ---- users table and merchant API dialog
                page.goto(ORIGIN + '/dashboard?tab=users'); page.wait_for_load_state('networkidle')
                user_rows = page.locator('.user-table tbody tr'); admin_row = user_rows.filter(has_text='管理员').first; merchant_row = user_rows.filter(has_text='merchant@example.test').first
                slot_text = lambda r: [r.locator('.action-triple > span').nth(i).inner_text().strip() for i in range(3)]
                check('administrator keeps only the middle action; merchant has 审核 | 接口配置 | 停用', slot_text(admin_row) == ['', '接口配置', ''] and slot_text(merchant_row) == ['审核', '接口配置', '停用'])
                middle = [r.locator('.action-triple > span').nth(1).locator('button').bounding_box()['x'] for r in (admin_row, merchant_row)]
                check('actions line up across rows', abs(middle[0] - middle[1]) < 1.5)
                here = page.url; admin_row.get_by_role('button', name='接口配置').click(); dialog = page.get_by_role('dialog', name='商户接口配置'); expect(dialog).to_be_visible()
                dialog.get_by_role('tab', name='V2 · RSA').click(); expect(dialog.get_by_role('button', name='生成 RSA 密钥对')).to_be_visible(); dialog.get_by_role('tab', name='V1 · MD5').click()
                check('merchant API opens in a dialog with V1/V2 switch, no page change', page.url == here)
                dialog.get_by_role('button', name='关闭接口配置').click()
                page.goto(ORIGIN + '/dashboard?tab=account'); page.wait_for_load_state('networkidle'); page.get_by_role('button', name='接口配置').click(); expect(page.get_by_role('dialog', name='商户接口配置')).to_be_visible()
                page.get_by_role('button', name='关闭接口配置').click()
                page.set_viewport_size({'width': 390, 'height': 844}); page.goto(ORIGIN + '/dashboard?tab=orders'); page.wait_for_load_state('networkidle')
                page.locator('.orders-table tbody tr').first.get_by_role('button', name='详情').click(); expect(page.locator('.order-dialog[open]')).to_be_visible()
                narrow = page.locator('.order-modal').bounding_box()
                check('phone: order dialog fits the screen, no sideways overflow', 0 <= narrow['x'] and narrow['x'] + narrow['width'] <= 390 and page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                page.screenshot(path=str(evidence / 'order-dialog-phone.png'))
                check('no browser errors', not errors, str(errors[:4]))
                browser.close()
        finally:
            for item in patches: item.stop()
            server.shutdown(); server.server_close()
    (evidence / 'result.json').write_text(json.dumps({'result': 'passed' if not check.failed else 'failed', 'failed': check.failed, 'external_services': 'fixtures only; no real payment, refund or network'}, ensure_ascii=False, indent=2))
    print(f"\n{'PASS' if not check.failed else 'FAIL'}: {len(check.failed)} failed")
    return 1 if check.failed else 0


if __name__ == '__main__': sys.exit(run())
