"""Actual browser checks against an isolated local database; never proves payment acceptance.

Run after building the current frontend. No provider calls, route interception,
sample paid orders or main database changes are used here.
"""
import argparse
import json
import os
import secrets
import socket
import string
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', default=str(ROOT / 'frontend/dist'))
    parser.add_argument('--output', default=str(ROOT / 'artifacts/current-ui'))
    args = parser.parse_args()
    dist = Path(args.dist).resolve()
    if not (dist / 'index.html').is_file():
        raise SystemExit('Build the current frontend first')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    errors, checks, http_errors = [], [], []
    with tempfile.TemporaryDirectory(prefix='bypay-browser-') as directory:
        env = {**os.environ, 'PUBLIC_BASE_URL': origin, 'DATABASE': directory + '/web.sqlite3',
               'BYPAY_LOG_FILE': directory + '/trace.jsonl', 'BYPAY_ENV_FILE': directory + '/absent.env',
               'BYPAY_FRONTEND_PORT': str(port), 'BYPAY_FRONTEND_DIST': str(dist), 'BYPAY_QR_PREFETCH': '0'}
        with open(output / 'server.log', 'w') as log:
            server = subprocess.Popen([str(ROOT / '.venv/bin/gunicorn'), '--bind', f'127.0.0.1:{port}',
                '--workers', '2', '--threads', '4', '--no-control-socket', 'web.wsgi:app'],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Browser server failed; inspect server.log')
                    try:
                        with urlopen(origin + '/health', timeout=1) as response:
                            if response.status == 200:break
                    except OSError:time.sleep(.1)
                else:raise RuntimeError('Browser server readiness timeout')
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page(viewport={'width': 1440, 'height': 900})
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.goto(origin + '/login', wait_until='networkidle')
                    assert not page.locator('html').evaluate("n=>n.classList.contains('dark')")
                    page.locator('#email').fill('admin@pay.com')
                    page.locator('#password').fill('Admin123')
                    page.locator('button[type=submit],form > button.full-width').first.click()
                    page.wait_for_url('**/admin/security/setup')
                    page.wait_for_load_state('networkidle')
                    assert page.request.get(origin + '/api/admin/channels').status == 403
                    checks.append('Initial credentials blocked by server')
                    page.goto(origin + '/admin/channels', wait_until='networkidle')
                    assert page.url.endswith('/admin/security/setup')
                    page.screenshot(path=str(output / 'initial-security.png'), full_page=True)
                    alphabet = string.ascii_letters + string.digits
                    password = 'Aa1' + ''.join(secrets.choice(alphabet) for _ in range(17))
                    page.locator('#setup-email').fill('browser-owner@example.com')
                    page.locator('#setup-current').fill('Admin123')
                    for rejected in ('lowercase12345','Ab1!Short'):
                        page.locator('#setup-password').fill(rejected)
                        page.locator('#setup-confirm').fill(rejected)
                        assert page.get_by_role('button', name='保存并重新登录').is_disabled()
                    page.locator('#setup-password').fill(password)
                    page.locator('#setup-confirm').fill(password)
                    assert page.get_by_role('button', name='保存并重新登录').is_enabled()
                    assert page.locator('.password-categories .met').count() == 3
                    page.screenshot(path=str(output / 'security-three-categories.png'), full_page=True)
                    checks.append('Password longer than 12 characters with any three categories accepted')
                    page.get_by_role('button', name='保存并重新登录').click()
                    page.wait_for_url('**/login?updated=1')
                    assert page.request.get(origin + '/api/auth/me').status == 401
                    page.locator('#email').fill('browser-owner@example.com')
                    page.locator('#password').fill(password)
                    page.locator('form > button.full-width').click()
                    page.wait_for_url('**/admin/overview')
                    checks.append('Browser setup, session revocation and new login')
                    page.on('response', lambda r: http_errors.append(f'{r.status} {r.url}') if '/api/' in r.url and r.status>=400 else None)
                    for width, height in [(1920, 1080), (1440, 900), (1366, 768), (1024, 768), (390, 844)]:
                        page.set_viewport_size({'width': width, 'height': height})
                        page.goto(origin + '/admin/channels', wait_until='networkidle')
                        assert page.locator('.payment-channel-card').count() == 3
                        assert page.get_by_role('switch').count() == 3
                        assert page.get_by_role('switch', checked=True).count() == 0
                        assert not page.locator('#crypto-network-settings').is_visible()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow {width}'
                        page.screenshot(path=str(output / f'channels-{width}.png'), full_page=True)
                        page.get_by_role('button', name='Crypto 网络设置', exact=True).click()
                        panel = page.locator('#crypto-network-settings')
                        panel.wait_for(state='visible')
                        assert panel.locator('.node-card').count() == 9
                        assert all(panel.locator(f'#rpc-{network}').input_value() for network in
                            ['BSC', 'TRON', 'APTOS', 'ETH', 'PLASMA', 'SOL', 'ARBITRUM', 'POLYGON', 'AVAXC'])
                        page.screenshot(path=str(output / f'nodes-{width}.png'), full_page=True)
                        if width >= 1366:
                            assert panel.locator('.nodes-grid').evaluate('n=>getComputedStyle(n).gridTemplateColumns.split(" ").length') == 3
                            assert panel.locator('.nodes-grid').evaluate('n=>n.scrollHeight<=n.clientHeight+2'), f'Node grid overflow {width}'
                            for card in panel.locator('.node-card').all():
                                inputs = card.locator('input').all()
                                rpc, confirmations, key = [n.bounding_box() for n in inputs]
                                button = card.get_by_role('button', name='保存并检查').bounding_box()
                                assert abs(rpc['y'] + rpc['height']/2 - confirmations['y'] - confirmations['height']/2) < 2
                                assert abs(key['y'] + key['height']/2 - button['y'] - button['height']/2) < 2, {'key':key,'button':button,'css':card.locator('.node-save').evaluate('n=>({margin:getComputedStyle(n).margin,box:getComputedStyle(n).boxSizing,align:getComputedStyle(n.parentElement).alignItems})')}
                        page.screenshot(path=str(output / f'nodes-{width}.png'), full_page=True)
                        panel.get_by_role('button', name='关闭网络设置').click()
                        assert not panel.is_visible()
                        checks.append(f'Channels and node dialog {width}x{height}')
                    page.set_viewport_size({'width': 1440, 'height': 900})
                    for path in ['/admin/orders', '/admin/rates/history', '/admin/rates/settings', '/admin/settings/crypto', '/admin/settings/nodes', '/admin/merchants', '/admin/groups', '/admin/account', '/admin/settlements']:
                        page.goto(origin + path, wait_until='networkidle')
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), path
                        page.screenshot(path=str(output / (path.replace('/', '-').strip('-') + '.png')), full_page=True)
                        checks.append(path)
                    assert page.request.get(origin + '/dashboard?tab=channels').status == 404
                    browser.close()
                    assert not errors, errors
                    assert not http_errors, http_errors
            finally:
                server.terminate()
                try:server.wait(timeout=15)
                except subprocess.TimeoutExpired:server.kill();server.wait()
    result = {'scope':'Isolated local browser; no provider or funds acceptance', 'checks': checks, 'page_errors':errors,'http_errors':http_errors}
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':main()
