"""Port of Epay/plugins/alipay/alipay_plugin.php::qrcodepc.

Open the already signed website checkout, then extract its qrCode hidden input.
This keeps website payment as the product, with no alipay.trade.precreate call.

Getting that QR takes a few sequential requests to Alipay (the gateway, then the cashier page). To make it appear sooner:
- the connections to Alipay are kept open and shared by every fetch, so a request that finds one open costs a round trip
  instead of a TLS handshake plus a round trip (about a third of the cold time);
- the fetch starts as soon as the order exists (Prefetch), while the browser is still loading the cashier;
- for a few minutes after an order, the connections are kept warm (Warmer), so the next order finds them open.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as wait_for
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

MAX_BYTES = 1_500_000
TOTAL_SECONDS = 12  # for the whole chain: every request gets only what is left of it, and so does the reading of the page
MAX_REQUESTS = 7  # the first request and up to six redirects
REDIRECT_BODY = 65_536  # what is read of a redirect's own body (they are empty or tiny)
REDIRECTS = {301, 302, 303, 307, 308}
log = logging.getLogger("bypay")


def allowed_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in {"openapi.alipay.com", "openapi-sandbox.dl.alipaydev.com", "unitradeprod.alipay.com", "excashier.alipay.com", "cashier.alipay.com", "excashier-sandbox.dl.alipaydev.com"} or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("cashier_redirect_rejected")
    return value


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


class _Shared(httpx.BaseTransport):
    """Every fetch gets its own client, so cookies never pass from one order to another, on connections all fetches share."""

    def __init__(self, pool):
        self.pool = pool

    def handle_request(self, request):
        return self.pool.handle_request(request)

    def close(self):
        pass  # the pool outlives every single fetch


class CashierFetcher:
    def __init__(self, transport=None):
        self._pool = transport  # tests hand in a mock transport; the service opens the real pool on first use
        self._lock = threading.Lock()
        self._hosts = set()  # Alipay hosts the last fetches went through: what the warmer keeps open

    def _connections(self):
        with self._lock:
            if self._pool is None:
                self._pool = httpx.HTTPTransport(trust_env=False, limits=httpx.Limits(max_connections=8, max_keepalive_connections=6, keepalive_expiry=45.0))
            return self._pool

    def fetch(self, pay_url, stats=None):
        allowed_url(pay_url)
        started = time.monotonic()
        try:
            return self._fetch(pay_url, stats, started)
        except (httpx.RemoteProtocolError, httpx.ReadError):
            return self._fetch(pay_url, stats, started)  # a kept-open connection that Alipay had closed meanwhile: ask again, on a new one

    def _fetch(self, pay_url, stats, started):
        def left():
            remaining = TOTAL_SECONDS - (time.monotonic() - started)
            if remaining <= 0: raise ValueError("cashier_timeout")
            return remaining

        hosts, url = [], pay_url
        # redirects are followed here, one request at a time, so that every address is checked before anything is sent to it
        with httpx.Client(transport=_Shared(self._connections()), follow_redirects=False, trust_env=False,
                          headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html", "Accept-Encoding": "identity"}) as client:
            for _ in range(MAX_REQUESTS):
                allowed_url(url)
                budget = left()
                hosts.append(urlsplit(url).hostname)
                with client.stream("GET", url, timeout=httpx.Timeout(min(10.0, budget), connect=min(5.0, budget))) as response:
                    if response.status_code in REDIRECTS and response.headers.get("Location"):
                        drained = 0
                        try:  # the body is not needed; read a little of it, without unpacking, so that the connection can be used again
                            for chunk in response.iter_raw():
                                drained += len(chunk)
                                if drained > REDIRECT_BODY: break
                        except httpx.StreamConsumed: pass  # already in memory: nothing to drain
                        try: url = str(response.url.join(response.headers["Location"]))
                        except httpx.InvalidURL: raise ValueError("cashier_redirect_rejected") from None
                        continue
                    if response.status_code != 200: raise ValueError("cashier_http_status")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES: raise ValueError("cashier_response_too_large")
                        left()  # a page that trickles in is cut off by the same deadline
                    charset = response.charset_encoding or "utf-8"
                break
            else:
                raise ValueError("cashier_too_many_redirects")
        try: html = bytes(body).decode(charset, "replace")
        except LookupError: html = bytes(body).decode("utf-8", "replace")
        value = extract_cashier_qr(html)
        with self._lock: self._hosts.update(hosts)
        if stats is not None: stats.update(hops=len(hosts), hosts=sorted(set(hosts)), bytes=len(body), fetch_ms=int((time.monotonic() - started) * 1000))
        return value

    def warm(self):
        """One tiny request per Alipay host of the last fetches, so that their connections stay open (what comes back does not matter)."""
        with self._lock: hosts = sorted(self._hosts)
        opened = 0
        with httpx.Client(transport=_Shared(self._connections()), follow_redirects=False, trust_env=False, timeout=httpx.Timeout(5.0), headers={"User-Agent": "Mozilla/5.0"}) as client:
            for host in hosts:
                try:
                    allowed_url(f"https://{host}/")
                    client.head(f"https://{host}/").close()
                    opened += 1
                except (httpx.HTTPError, ValueError):
                    pass
        return opened


_fetcher = CashierFetcher()


def fetch_cashier_qr(pay_url, stats=None):
    return _fetcher.fetch(pay_url, stats)


def qr_inline(order, now=None):
    """The styled QR itself, so that the browser needs no second request to show it. Only for a live Alipay website order."""
    now = int(time.time()) if now is None else now
    if order["channel"] != "alipay" or order["product"] != "page" or not order["cashier_qr"] or order["paid_at"] or order["status"] != "CREATED" or order["expires_at"] <= now:
        return None
    from .qrcode import payment_qr_svg
    try: return payment_qr_svg(order["cashier_qr"], "alipay")
    except ValueError: return None


class Prefetch:
    """Starts an order's cashier fetch as soon as the order exists, so the QR is usually ready, or nearly, when the cashier opens.

    Off unless the service turns it on (BYPAY_QR_PREFETCH): tests and tools must never reach Alipay behind their back.
    """

    def __init__(self, enabled, workers=4, backlog=32):
        self.enabled = bool(enabled)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="qr-prefetch") if self.enabled else None
        self._room = threading.BoundedSemaphore(backlog)
        self._open, self._lock = set(), threading.Lock()

    def submit(self, job, *args):
        if not self.enabled or not self._room.acquire(blocking=False): return None  # a flood of orders: each cashier then asks for itself
        try: future = self._pool.submit(self._run, job, args)
        except RuntimeError:  # the interpreter is shutting down
            self._room.release()
            return None
        with self._lock: self._open.add(future)
        future.add_done_callback(self._done)
        return future

    @staticmethod
    def _run(job, args):
        try: job(*args)
        except Exception as exc:  # noqa: BLE001 - Alipay failures are already in the event log, and the cashier asks again
            log.warning("qr_prefetch_failed type=%s", type(exc).__name__)

    def _done(self, future):
        self._room.release()
        with self._lock: self._open.discard(future)

    def wait(self, timeout=15):
        with self._lock: pending = list(self._open)
        wait_for(pending, timeout=timeout)


class Warmer:
    """For a few minutes after an order, keeps the connections to Alipay open, so that the next order does not pay for new handshakes.

    `latest` returns when the last website order was made (0 if never); outside the window nothing is sent to Alipay at all.
    """

    def __init__(self, warm, latest, interval=25.0, window=600.0, clock=time.time):
        self._warm, self._latest, self.interval, self.window, self._clock = warm, latest, interval, window, clock
        self._stop, self._thread = threading.Event(), None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="qr-warm", daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None: self._thread.join(5)

    def _run(self):
        while not self._stop.wait(self.interval): self.once()

    def once(self):
        try:
            latest = self._latest() or 0
            if latest and self._clock() - latest <= self.window:
                self._warm()
                return True
        except Exception as exc:  # noqa: BLE001 - keeping warm is a convenience; it must never take anything down
            log.warning("qr_warm_failed type=%s", type(exc).__name__)
        return False
