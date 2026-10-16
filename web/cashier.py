"""Port of Epay/plugins/alipay/alipay_plugin.php::qrcodepc.

Open the already signed website checkout, then extract its qrCode hidden input.
This keeps website payment as the product, with no alipay.trade.precreate call.
"""
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, build_opener


def allowed_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in {"openapi.alipay.com", "openapi-sandbox.dl.alipaydev.com", "unitradeprod.alipay.com", "excashier.alipay.com", "cashier.alipay.com", "excashier-sandbox.dl.alipaydev.com"} or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("cashier_redirect_rejected")
    return value


class AlipayRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class CashierHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.qr = ""

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "qrCode" and values.get("type") == "hidden":
            self.qr = values.get("value", "")


def extract_cashier_qr(html):
    parser = CashierHTML()
    parser.feed(html)
    parsed = urlsplit(parser.qr)
    if parsed.scheme != "https" or parsed.hostname != "qr.alipay.com" or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.fragment or len(parser.qr) > 512:
        raise ValueError("cashier_qr_unavailable")
    return parser.qr


def fetch_cashier_qr(pay_url):
    allowed_url(pay_url)
    opener = build_opener(ProxyHandler({}), AlipayRedirects(), HTTPCookieProcessor(CookieJar()))
    request = Request(pay_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    with opener.open(request, timeout=12) as response:
        allowed_url(response.url)
        body = response.read(1_500_001)
        if len(body) > 1_500_000: raise ValueError("cashier_response_too_large")
        return extract_cashier_qr(body.decode(response.headers.get_content_charset() or "utf-8", "replace"))
