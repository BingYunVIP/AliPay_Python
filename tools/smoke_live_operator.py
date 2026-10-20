#!/usr/bin/env python3
"""Real server-operator diagnostics; no login impersonation or funds movement."""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ready', action='store_true', help='User confirmed real configurations are saved')
    parser.add_argument('--browser', action='store_true', help='Check the real public UI and login boundary with Chromium')
    parser.add_argument('--report', default=str(ROOT / 'artifacts/live-operator.json'))
    args = parser.parse_args()
    if not args.ready:
        parser.error('须先得到用户已填写真实配置的确认，再使用 --ready')
    from web.app import create_app
    from web.runtime import load_runtime
    from web.crypto import ASSETS, DEFAULT_NETWORKS
    app = create_app(load_runtime())
    ext = app.extensions
    report = {'started_at': int(time.time()), 'scope': 'real saved configuration; server operator; no authenticated browser session', 'checks': []}

    def check(name, action, metadata=None):
        start = time.monotonic()
        try:
            value = action()
            passed = value.get('verified') is True or value.get('synced') is True
            # Explicit allowlist: never record request URLs, headers, PEMs or addresses.
            details = {k: value[k] for k in ('provider_code', 'height', 'head_timestamp', 'chain_id', 'fallback_used', 'rates', 'http_status', 'revision') if k in value}
            row = {'name': name, 'status': 'passed' if passed else 'failed', 'details': details}
        except Exception as exc:
            code = getattr(exc, 'code', '')
            if not code and isinstance(exc, ValueError) and str(exc).startswith(('market_', 'stale_market_', 'rate_sync_')):
                code = str(exc)
            row = {'name': name, 'status': 'failed', 'details': {'error_type': type(exc).__name__, 'code': code}}
        row['duration_seconds'] = round(time.monotonic() - start, 2)
        row.update(metadata or {})
        report['checks'].append(row)
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        target.chmod(0o600)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row

    check('Market sync', ext['pricing'].sync)
    for network in DEFAULT_NETWORKS:
        check('RPC ' + network, lambda network=network: ext['crypto_networks'].check(network))
        time.sleep(1)
    channels = ext['channels']
    configured = [item for item in channels.all() if item['configured'] and item['id'] in ('crypto', 'alipay', 'wechat')]
    configs = [(item, channels.get(item['id'], item['config_id'])[0]) for item in configured]
    for item, cfg in configs:
        if item['id'] != 'crypto':
            check('Channel ' + item['name'], lambda item=item, cfg=cfg: ext['channel_probe'](item['id'], cfg))
    for spec in ASSETS:
        cfg = next((cfg for item, cfg in configs if item['id'] == 'crypto' and cfg['network'] == spec.network), None)
        if cfg is None:
            report['checks'].append({'name': 'Asset ' + spec.code, 'status': 'blocked', 'details': {'reason': 'No real receiving address configured for this network'}})
            continue
        check('Asset ' + spec.code,
              lambda spec=spec, cfg=cfg: ext['crypto_gateway'].check({**cfg, 'asset': spec.asset}, ext['crypto_networks'].get(spec.network)),
              {'scope': 'live token metadata and saved public address; does not prove ownership or payment'})
        time.sleep(0.6)
    def http_permissions():
        with httpx.Client(base_url='http://127.0.0.1:888', trust_env=False, timeout=8) as client:
            home = client.get('/login')
            statuses = [client.get(path).status_code for path in ('/api/admin/channels', '/api/admin/rates/settings', '/api/console/orders')]
        return {'verified': home.status_code == 200 and statuses == [401, 401, 401], 'provider_code': 'local_login_and_anonymous_permissions'}
    check('Live HTTP permissions', http_permissions)
    if args.browser:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(locale='zh-CN')
                errors = []
                page.on('pageerror', lambda exc: errors.append(type(exc).__name__))
                for width, height in ((1440, 900), (1024, 768), (390, 844)):
                    def browser_check(width=width, height=height):
                        page.set_viewport_size({'width': width, 'height': height})
                        response = page.goto('http://127.0.0.1:888/login')
                        page.wait_for_load_state('networkidle')
                        visible = page.locator('#email').is_visible() and page.locator('#password').is_visible()
                        fits = page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        return {'verified': response.status == 200 and visible and fits and not errors,
                                'provider_code': 'real_login_page_no_js_error_or_overflow'}
                    check('Live browser login ' + str(width), browser_check)
                def browser_boundary():
                    page.goto('http://127.0.0.1:888/admin/rates/history')
                    page.wait_for_load_state('networkidle')
                    return {'verified': urlsplit(page.url).path == '/login' and not errors,
                            'provider_code': 'protected_browser_route_requires_login'}
                check('Live browser permission boundary', browser_boundary)
            finally:
                browser.close()
    report['blocked'] = [
        'Public HTTPS notifications and real merchant callback: user has not configured them',
        'Funded Alipay/WeChat/Crypto orders, refund and settlement: user payment and transfer not performed',
        'Authenticated browser acceptance: requires user login, no session impersonation',
    ]
    report['finished_at'] = int(time.time())
    report['read_only_checks_passed'] = bool(report['checks']) and all(r['status'] == 'passed' for r in report['checks'])
    report['full_acceptance_passed'] = False
    target = Path(args.report)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('Report: ' + str(target), flush=True)
    return 0 if report['read_only_checks_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
