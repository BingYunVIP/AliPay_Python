"""How fast the Alipay QR appears: connection reuse while fetching the cashier page, the fetch that starts with the order, and the finished image.

Alipay is played by in-process stand-ins (an httpx mock transport, or a replaced fetch function); nothing here reaches the network.
"""
import gc
import gzip
import itertools
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import test_epay
import web.cashier as cashier
import web.service as service
from bypay import key_utils
from web.app import create_app
from web.cashier import CashierFetcher, Prefetch, Warmer, fetch_cashier_qr, qr_inline

GATEWAY = "https://openapi.alipay.com/gateway.do?method=alipay.trade.page.pay&sign=x"
CASHIER = "https://excashier.alipay.com/standard/auth.htm?payOrderId=2026"
PAGE = '<html><head><title>cashier</title></head><body><form><input type="hidden" name="qrCode" value="https://qr.alipay.com/bax0001"></form></body></html>'


def redirecting(*, cookie=True, page=PAGE, **page_headers):
    """The gateway sends the browser to the cashier, which answers with the page; requests are recorded."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "openapi.alipay.com":
            headers = {"Location": CASHIER}
            if cookie: headers["Set-Cookie"] = "sid=abc123; Domain=.alipay.com; Path=/"
            return httpx.Response(302, headers=headers)
        return httpx.Response(200, content=page.encode() if isinstance(page, str) else page, **({"headers": page_headers} if page_headers else {}))
    handler.seen = seen
    return handler


class FetcherTests(unittest.TestCase):
    def fetcher(self, handler):
        return CashierFetcher(httpx.MockTransport(handler))

    def test_the_chain_is_followed_with_its_cookies_and_the_page_may_be_compressed(self):
        handler = redirecting(page=gzip.compress(PAGE.encode()), **{"Content-Encoding": "gzip", "Content-Type": "text/html; charset=utf-8"})
        stats = {}
        self.assertEqual(self.fetcher(handler).fetch(GATEWAY, stats), "https://qr.alipay.com/bax0001")
        self.assertEqual([request.url.host for request in handler.seen], ["openapi.alipay.com", "excashier.alipay.com"])
        self.assertEqual(handler.seen[1].headers["Cookie"], "sid=abc123")  # what the gateway set is sent to the cashier
        self.assertEqual(handler.seen[0].headers["Accept-Encoding"], "identity")  # Alipay is asked for plain pages, as before; a compressed one is still read
        self.assertEqual((stats["hops"], stats["hosts"]), (2, ["excashier.alipay.com", "openapi.alipay.com"]))
        self.assertGreater(stats["bytes"], 100)

    def test_cookies_never_pass_from_one_order_to_the_next(self):
        handler = redirecting()
        fetcher = self.fetcher(handler)
        fetcher.fetch(GATEWAY)
        fetcher.fetch(GATEWAY)
        first_hops = [request for request in handler.seen if request.url.host == "openapi.alipay.com"]
        self.assertEqual(len(first_hops), 2)
        self.assertTrue(all("Cookie" not in request.headers for request in first_hops))

    def test_connections_belong_to_the_service_not_to_one_fetch(self):
        closed = []

        class Pool(httpx.MockTransport):
            def close(self): closed.append(True)

        handler = redirecting()
        pool = Pool(handler)
        fetcher = CashierFetcher(pool)
        for _ in range(3): fetcher.fetch(GATEWAY)
        self.assertEqual((closed, len(handler.seen)), ([], 6))  # three orders, six requests, the pool is still open
        self.assertIs(fetcher._connections(), pool)

    def test_the_real_pool_opens_on_first_use_with_certificate_checks_and_idle_limits(self):
        fetcher = CashierFetcher()
        self.assertIsNone(fetcher._pool)
        pool = fetcher._connections()
        self.addCleanup(pool.close)
        self.assertIsInstance(pool, httpx.HTTPTransport)
        self.assertIs(fetcher._connections(), pool)
        self.assertEqual((pool._pool._max_keepalive_connections, pool._pool._keepalive_expiry), (6, 45.0))
        self.assertTrue(pool._pool._ssl_context.check_hostname)  # certificates are verified

    def test_only_alipay_cashier_hosts_over_https_are_ever_contacted(self):
        for location in ("https://evil.example/pay", "http://excashier.alipay.com/pay", "https://excashier.alipay.com.evil.test/pay", "https://user@excashier.alipay.com/pay", "https://127.0.0.1/pay"):
            with self.subTest(location=location):
                seen = []

                def handler(request, location=location):
                    seen.append(request.url.host)
                    return httpx.Response(302, headers={"Location": location}) if request.url.host == "openapi.alipay.com" else httpx.Response(200, content=PAGE.encode())
                with self.assertRaisesRegex(ValueError, "cashier_redirect_rejected"): self.fetcher(handler).fetch(GATEWAY)
                self.assertEqual(seen, ["openapi.alipay.com"])  # the forbidden address itself was never requested
        with self.assertRaisesRegex(ValueError, "cashier_redirect_rejected"): self.fetcher(redirecting()).fetch("https://evil.example/gateway.do")

    def test_bad_answers_are_refused(self):
        cases = {"cashier_http_status": httpx.Response(500, content=b"oops"), "cashier_response_too_large": httpx.Response(200, content=b"x" * (cashier.MAX_BYTES + 1)),
                 "cashier_qr_unavailable": httpx.Response(200, content=b"<html>no code here</html>")}
        for expected, answer in cases.items():
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected): self.fetcher(lambda request, answer=answer: answer).fetch(GATEWAY)
        requests = []
        with self.assertRaisesRegex(ValueError, "cashier_too_many_redirects"):
            self.fetcher(lambda request: requests.append(1) or httpx.Response(302, headers={"Location": GATEWAY})).fetch(GATEWAY)
        self.assertEqual(len(requests), cashier.MAX_REQUESTS)  # a loop ends after the first request and six redirects
        broken = []
        with self.assertRaises((ValueError, httpx.RemoteProtocolError)):  # httpx itself refuses an address that is not one
            self.fetcher(lambda request: broken.append(request.url.host) or httpx.Response(302, headers={"Location": "https://excashier.alipay.com:badport/pay"})).fetch(GATEWAY)
        self.assertEqual(set(broken), {"openapi.alipay.com"})

    def test_a_page_that_unpacks_to_too_much_is_refused_while_it_is_read(self):
        bomb = gzip.compress(b"a" * (cashier.MAX_BYTES + 10))
        self.assertLess(len(bomb), 10_000)
        with self.assertRaisesRegex(ValueError, "cashier_response_too_large"):
            self.fetcher(lambda request: httpx.Response(200, content=bomb, headers={"Content-Encoding": "gzip"})).fetch(GATEWAY)

    def test_a_chain_that_takes_too_long_is_cut_off(self):
        ticks = itertools.count(0, 6.0)  # every look at the clock is 6 s later than the one before
        with patch("web.cashier.time.monotonic", side_effect=lambda: next(ticks)):
            with self.assertRaisesRegex(ValueError, "cashier_timeout"): self.fetcher(redirecting()).fetch(GATEWAY)

    def test_every_request_gets_only_what_is_left_of_the_total_time(self):
        granted, normal, ticks = [], redirecting(), itertools.count(0, 3.0)

        def handler(request):
            granted.append(dict(request.extensions["timeout"]))
            return normal(request)
        with patch("web.cashier.time.monotonic", side_effect=lambda: next(ticks)):
            self.fetcher(handler).fetch(GATEWAY)
        self.assertEqual(granted, [{"connect": 5.0, "read": 9.0, "write": 9.0, "pool": 9.0}, {"connect": 5.0, "read": 6.0, "write": 6.0, "pool": 6.0}])

    def test_a_page_that_trickles_in_is_cut_off_by_the_same_deadline(self):
        now = [0.0]

        def trickle():
            for _ in range(10):
                now[0] += 5
                yield b"<html>"
        with patch("web.cashier.time.monotonic", side_effect=lambda: now[0]):
            with self.assertRaisesRegex(ValueError, "cashier_timeout"): self.fetcher(lambda request: httpx.Response(200, content=trickle())).fetch(CASHIER)

    def test_the_size_cap_counts_the_whole_page_not_each_piece_of_it(self):
        pieces = [b"x" * 600_000] * 3  # 1.8 MB, in pieces that are each under the cap
        with self.assertRaisesRegex(ValueError, "cashier_response_too_large"): self.fetcher(lambda request: httpx.Response(200, content=iter(pieces))).fetch(CASHIER)

    def test_the_body_of_a_redirect_is_read_only_a_little(self):
        pulled = []

        def endless():
            for _ in range(10_000):
                pulled.append(1)
                yield b"r" * 8192

        def handler(request):
            if request.url.host == "openapi.alipay.com": return httpx.Response(302, headers={"Location": CASHIER}, content=endless())
            return httpx.Response(200, content=PAGE.encode())
        self.assertEqual(self.fetcher(handler).fetch(GATEWAY), "https://qr.alipay.com/bax0001")
        self.assertLessEqual(len(pulled) * 8192, cashier.REDIRECT_BODY + 8192)

    def test_a_connection_alipay_closed_in_the_meantime_is_replaced_once(self):
        calls = []

        def handler(request):
            calls.append(request.url.host)
            if len(calls) == 1: raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return httpx.Response(200, content=PAGE.encode())
        self.assertEqual(self.fetcher(handler).fetch(CASHIER), "https://qr.alipay.com/bax0001")
        self.assertEqual(len(calls), 2)
        with self.assertRaises(httpx.RemoteProtocolError):
            self.fetcher(lambda request: (_ for _ in ()).throw(httpx.RemoteProtocolError("closed"))).fetch(CASHIER)  # a second failure is not hidden

    def test_the_page_is_read_in_the_charset_it_declares_and_an_unknown_one_does_not_break_it(self):
        page = PAGE.replace("cashier", "收银台")
        gbk = self.fetcher(lambda request: httpx.Response(200, content=page.encode("gbk"), headers={"Content-Type": "text/html; charset=gbk"}))
        self.assertEqual(gbk.fetch(CASHIER), "https://qr.alipay.com/bax0001")
        odd = self.fetcher(lambda request: httpx.Response(200, content=PAGE.encode(), headers={"Content-Type": "text/html; charset=x-nonsense"}))
        self.assertEqual(odd.fetch(CASHIER), "https://qr.alipay.com/bax0001")

    def test_the_module_function_uses_the_shared_fetcher(self):
        handler = redirecting()
        with patch.object(cashier, "_fetcher", CashierFetcher(httpx.MockTransport(handler))):
            self.assertEqual(fetch_cashier_qr(GATEWAY), "https://qr.alipay.com/bax0001")
        self.assertEqual(len(handler.seen), 2)

    def test_the_machine_environment_cannot_change_how_alipay_is_reached(self):
        bogus = {"HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9", "SSL_CERT_FILE": "/nonexistent/ca.pem", "SSL_CERT_DIR": "/nonexistent"}
        with patch.dict("os.environ", bogus):
            pool = CashierFetcher()._connections()  # building it would fail if it read the CA file named by the environment
        self.addCleanup(pool.close)
        self.assertEqual(type(pool._pool).__name__, "ConnectionPool")  # a direct pool, not a proxy pool

    def test_warming_sends_one_tiny_request_to_each_host_the_last_fetches_used_and_nothing_before_any_fetch(self):
        handler = redirecting()
        fetcher = self.fetcher(handler)
        self.assertEqual(fetcher.warm(), 0)
        self.assertEqual(handler.seen, [])  # nothing is known yet, so nothing is sent
        fetcher.fetch(GATEWAY)
        handler.seen.clear()
        self.assertEqual(fetcher.warm(), 2)
        self.assertEqual(sorted((request.method, request.url.host, request.url.path) for request in handler.seen), [("HEAD", "excashier.alipay.com", "/"), ("HEAD", "openapi.alipay.com", "/")])
        self.assertTrue(all("Cookie" not in request.headers and not request.content for request in handler.seen))

    def test_warming_survives_a_host_that_does_not_answer_and_keeps_going(self):
        calls, normal = [], redirecting()

        def handler(request):
            calls.append(request.url.host)
            if request.method == "HEAD" and request.url.host == "openapi.alipay.com": raise httpx.ConnectError("unreachable")
            return normal(request)
        fetcher = self.fetcher(handler)
        fetcher.fetch(GATEWAY)
        calls.clear()
        self.assertEqual(fetcher.warm(), 1)  # the other host was still reached
        self.assertEqual(sorted(calls), ["excashier.alipay.com", "openapi.alipay.com"])

    def test_a_host_outside_the_allow_list_is_never_warmed_even_if_it_somehow_got_remembered(self):
        handler = redirecting()
        fetcher = self.fetcher(handler)
        fetcher._hosts.add("evil.example")
        self.assertEqual(fetcher.warm(), 0)
        self.assertEqual(handler.seen, [])


class PrefetchTests(unittest.TestCase):
    def test_it_is_off_unless_switched_on_and_then_a_job_runs_in_the_background(self):
        self.assertIsNone(Prefetch(False).submit(lambda: 1 / 0))
        ran = []
        on = Prefetch(True)
        future = on.submit(ran.append, "order")
        on.wait(5)
        self.assertEqual((ran, future.done()), (["order"], True))

    def test_a_flood_of_orders_is_not_queued_without_limit(self):
        release, started = threading.Event(), threading.Event()

        def job():
            started.set()
            release.wait(5)
        on = Prefetch(True, workers=1, backlog=2)
        self.assertIsNotNone(on.submit(job))
        self.assertIsNotNone(on.submit(job))
        self.assertIsNone(on.submit(job))  # the third has no room: its cashier will ask Alipay itself
        started.wait(5)
        release.set()
        on.wait(5)
        self.assertIsNotNone(on.submit(lambda: None))  # room again once the earlier jobs are done
        on.wait(5)

    def test_a_failing_job_is_logged_by_type_and_never_raised(self):
        on = Prefetch(True)
        with self.assertLogs("bypay", "WARNING") as logs:
            on.submit(lambda: 1 / 0)
            on.wait(5)
        self.assertEqual(logs.output, ["WARNING:bypay:qr_prefetch_failed type=ZeroDivisionError"])

    def test_after_shutdown_nothing_is_submitted_and_no_room_is_lost(self):
        on = Prefetch(True, backlog=1)
        on._pool.shutdown()
        for _ in range(3): self.assertIsNone(on.submit(lambda: None))


class WarmerTests(unittest.TestCase):
    def test_connections_are_kept_warm_only_in_the_minutes_after_an_order(self):
        now, latest, warmed = [10_000.0], [0], []
        warmer = Warmer(lambda: warmed.append(now[0]), lambda: latest[0], interval=0.01, window=600, clock=lambda: now[0])
        self.assertFalse(warmer.once())  # no website order has ever been made
        latest[0] = 9_900
        self.assertTrue(warmer.once())
        now[0] = 10_500
        self.assertTrue(warmer.once())  # 600 s after the order is still inside the window
        now[0] = 10_501
        self.assertFalse(warmer.once())  # then nothing more is sent to Alipay
        self.assertEqual(len(warmed), 2)

    def test_a_failure_while_warming_is_logged_and_never_raised(self):
        with self.assertLogs("bypay", "WARNING") as logs:
            self.assertFalse(Warmer(lambda: 1 / 0, lambda: time.time()).once())
        self.assertEqual(logs.output, ["WARNING:bypay:qr_warm_failed type=ZeroDivisionError"])
        with self.assertLogs("bypay", "WARNING"): self.assertFalse(Warmer(lambda: None, lambda: 1 / 0).once())

    def test_the_background_thread_keeps_going_until_stopped(self):
        beat, count = threading.Event(), []
        warmer = Warmer(lambda: (count.append(1), beat.set()), lambda: time.time(), interval=0.02).start()
        thread = warmer._thread
        self.assertTrue(beat.wait(5))
        self.assertIs(warmer.start(), warmer)  # starting twice does not start a second thread
        self.assertIs(warmer._thread, thread)
        warmer.stop()
        stopped_at = len(count)
        time.sleep(0.1)
        self.assertEqual((len(count), thread.is_alive()), (stopped_at, False))


class AppCase(unittest.TestCase):
    """An Alipay-configured Pay app per call to build(), with its own database and a logged-in merchant."""

    @classmethod
    def setUpClass(cls):
        cls.alipay_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.app_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public = cls.alipay_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        cls.private = cls.app_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()

    def setUp(self):
        guard = patch("socket.create_connection", side_effect=AssertionError("Unexpected network request"))
        guard.start()
        self.addCleanup(guard.stop)

    def build(self, **config):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        app = create_app({"TESTING": True, "PUBLIC_BASE_URL": "https://pay.example.test", "TEST_MAX_AMOUNT": "10.00", "DATABASE": str(Path(temp.name) / "pay.sqlite3"),
                          "ALIPAY_APP_ID": "2026000000000000", "ALIPAY_SELLER_ID": "2088000000000000", "ALIPAY_PRIVATE_KEY": self.private, "ALIPAY_PUBLIC_KEY": self.public,
                          "BYPAY_ADMIN_EMAIL": "admin@example.test", "BYPAY_ADMIN_PASSWORD": "Test-admin-password-123", "BYPAY_CALLBACK_HOSTS": "test.bingyun.vip", **config})
        self.addCleanup(app.extensions["qr_warmer"].stop)  # runs last: the temporary database is removed before that
        self.addCleanup(app.extensions["qr_prefetch"].wait)
        client = app.test_client()
        response = client.post("/api/auth/register", json={"email": "user@example.test", "password": "Test-user-password-123"})
        self.assertEqual(response.status_code, 200)
        return app, client, {"X-CSRF-Token": response.json["csrf_token"], "Origin": "https://pay.example.test"}

    @staticmethod
    def second_session(app):
        """Another browser of the same merchant: fetches that race each other come from different requests."""
        client = app.test_client()
        response = client.post("/api/auth/login", json={"email": "user@example.test", "password": "Test-user-password-123"})
        return client, {"X-CSRF-Token": response.json["csrf_token"], "Origin": "https://pay.example.test"}

    @staticmethod
    def create(client, headers, number="SPEED_ORDER_0001", **fields):
        return client.post("/api/v1/payments", json={"out_trade_no": number, "amount": "0.01", "subject": "测试商品", "channel": "alipay", "product": "page", **fields}, headers=headers)

    @staticmethod
    def events(app, number, reason):
        with app.extensions["store"].connect() as db:
            rows = db.execute("SELECT details FROM events WHERE out_trade_no=? AND reason=? ORDER BY id", (number, reason)).fetchall()
        return [json.loads(row["details"]) for row in rows]

    @staticmethod
    def asking(value="https://qr.alipay.com/bax-speed", *, hold=None, fail=0):
        calls = []

        def fetch(url, stats=None):
            calls.append(threading.current_thread().name)
            if hold is not None: hold.wait(10)
            if len(calls) <= fail: raise OSError("Alipay is slow today")
            if stats is not None: stats.update(hops=3, hosts=["excashier.alipay.com", "openapi.alipay.com"], bytes=4096, fetch_ms=7)
            return value
        fetch.calls = calls
        return fetch


class QrSpeedTests(AppCase):
    # ------------------------------------------------------------------ the fetch starts with the order
    def test_the_qr_is_fetched_as_the_order_is_created_so_the_cashier_finds_it_ready(self):
        app, client, headers = self.build(BYPAY_QR_PREFETCH="1")
        fetch = self.asking()
        with patch("web.cashier.fetch_cashier_qr", fetch):
            number = self.create(client, headers).json["order"]["out_trade_no"]
            app.extensions["qr_prefetch"].wait()
            self.assertEqual(app.extensions["store"].order(number)["cashier_qr"], "https://qr.alipay.com/bax-speed")
            prepared = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers)
        self.assertEqual((prepared.status_code, prepared.json["order"]["cashier_qr"]), (200, "https://qr.alipay.com/bax-speed"))
        self.assertEqual(len(fetch.calls), 1)  # the cashier asked nothing more of Alipay
        self.assertTrue(fetch.calls[0].startswith("qr-prefetch"))
        done = self.events(app, number, "cashier_qr_ready")
        self.assertEqual(len(done), 1)
        self.assertEqual((done[0]["fetched_by"], done[0]["waited"], done[0]["hops"], done[0]["source"]), ("prefetch", False, 3, "alipay_website_checkout"))
        self.assertIsInstance(done[0]["elapsed_ms"], int)

    def test_a_cashier_that_opens_while_the_fetch_is_still_running_waits_for_it_and_alipay_is_asked_once(self):
        app, client, headers = self.build(BYPAY_QR_PREFETCH="1")
        release, answers = threading.Event(), []
        fetch = self.asking(hold=release)
        with patch("web.cashier.fetch_cashier_qr", fetch):
            number = self.create(client, headers).json["order"]["out_trade_no"]
            other, other_headers = self.second_session(app)
            waiter = threading.Thread(target=lambda: answers.append(other.post(f"/api/v1/payments/{number}/prepare-qr", headers=other_headers, json={})))
            deadline = time.monotonic() + 5
            while not fetch.calls and time.monotonic() < deadline: time.sleep(.01)
            waiter.start()
            time.sleep(.3)
            self.assertEqual(answers, [])  # still waiting: the answer from Alipay has not come yet
            release.set()
            waiter.join(10)
            app.extensions["qr_prefetch"].wait()
        self.assertEqual(len(fetch.calls), 1)
        self.assertEqual((answers[0].status_code, answers[0].json["order"]["cashier_qr"]), (200, "https://qr.alipay.com/bax-speed"))
        self.assertIn("<svg", answers[0].json["qr_svg"])

    def test_two_cashiers_for_one_order_are_one_fetch(self):
        app, client, headers = self.build()  # no prefetch here: the two requests race for the lease themselves
        release, answers = threading.Event(), []
        fetch = self.asking(hold=release)
        with patch("web.cashier.fetch_cashier_qr", fetch):
            number = self.create(client, headers).json["order"]["out_trade_no"]
            sessions = [self.second_session(app) for _ in range(2)]
            threads = [threading.Thread(target=lambda c=c, h=h: answers.append(c.post(f"/api/v1/payments/{number}/prepare-qr", headers=h, json={}))) for c, h in sessions]
            for thread in threads: thread.start()
            time.sleep(.4)
            release.set()
            for thread in threads: thread.join(10)
        self.assertEqual(len(fetch.calls), 1)
        self.assertEqual([answer.status_code for answer in answers], [200, 200])
        self.assertEqual([event["waited"] for event in self.events(app, number, "cashier_qr_ready")], [False])  # one answer was fetched; the other request only waited for it

    def test_a_failed_prefetch_costs_nothing_the_cashier_just_asks_again(self):
        app, client, headers = self.build(BYPAY_QR_PREFETCH="1")
        fetch = self.asking(fail=1)
        with patch("web.cashier.fetch_cashier_qr", fetch), self.assertLogs("bypay", "WARNING"):
            number = self.create(client, headers).json["order"]["out_trade_no"]
            app.extensions["qr_prefetch"].wait()
            self.assertEqual(app.extensions["store"].order(number)["cashier_qr"], "")
            lost = self.events(app, number, "cashier_qr_unavailable")
            self.assertEqual((len(lost), lost[0]["fetched_by"], lost[0]["exception_type"]), (1, "prefetch", "OSError"))
            self.assertFalse(app.extensions["store"].qr_busy(number))  # the failed attempt let go of the order
            prepared = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
        self.assertEqual((prepared.status_code, prepared.json["order"]["cashier_qr"]), (200, "https://qr.alipay.com/bax-speed"))
        self.assertEqual(len(fetch.calls), 2)

    def test_a_cashier_whose_request_fails_is_told_so_and_the_order_can_be_asked_again(self):
        app, client, headers = self.build()
        fetch = self.asking(fail=1)
        with patch("web.cashier.fetch_cashier_qr", fetch):
            number = self.create(client, headers).json["order"]["out_trade_no"]
            first = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
            second = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
        self.assertEqual((first.status_code, first.json["error"]), (502, "cashier_qr_unavailable"))
        self.assertEqual(second.status_code, 200)

    def test_a_lease_held_by_a_stuck_process_is_waited_out_and_then_taken_over(self):
        app, client, headers = self.build(QR_WAIT_SECONDS="6")
        fetch = self.asking()
        with patch("web.cashier.fetch_cashier_qr", fetch):
            number = self.create(client, headers).json["order"]["out_trade_no"]
            self.assertTrue(app.extensions["store"].claim_qr(number, seconds=1))  # another worker started and never finished
            started = time.monotonic()
            prepared = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
        self.assertEqual(prepared.status_code, 200)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(len(fetch.calls), 1)

    def test_a_lease_that_outlasts_the_wait_gives_a_clear_error_instead_of_hanging(self):
        app, client, headers = self.build(QR_WAIT_SECONDS="0.5")
        fetch = self.asking()
        with patch("web.cashier.fetch_cashier_qr", fetch):
            number = self.create(client, headers).json["order"]["out_trade_no"]
            self.assertTrue(app.extensions["store"].claim_qr(number, seconds=30))
            started = time.monotonic()
            prepared = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
        self.assertEqual((prepared.status_code, prepared.json["error"]), (502, "cashier_qr_unavailable"))
        self.assertLess(time.monotonic() - started, 3)  # it waited about as long as it was allowed to, not for the whole lease
        self.assertEqual(fetch.calls, [])

    def test_only_the_holder_of_a_lease_can_let_it_go(self):
        app, client, headers = self.build()
        store = app.extensions["store"]
        number = self.create(client, headers).json["order"]["out_trade_no"]
        lease = store.claim_qr(number)
        self.assertTrue(lease)
        self.assertIsNone(store.claim_qr(number))  # held
        store.release_qr(number, "somebody-else")
        self.assertTrue(store.qr_busy(number))
        store.release_qr(number, lease)
        self.assertFalse(store.qr_busy(number))
        self.assertTrue(store.claim_qr(number))

    def test_refresh_asks_alipay_again_for_a_new_code_and_a_plain_prepare_does_not(self):
        app, client, headers = self.build()
        values = iter(["https://qr.alipay.com/bax-first", "https://qr.alipay.com/bax-second"])
        fetch = lambda url, stats=None: next(values)
        store = app.extensions["store"]
        with patch("web.cashier.fetch_cashier_qr", fetch), patch.object(app.extensions["reconciler"], "sync", lambda number, why=None: {"order": store.order(number), "verification": {}}):
            number = self.create(client, headers).json["order"]["out_trade_no"]
            first = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
            again = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
            refreshed = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={"refresh": True})
        self.assertEqual([r.json["order"]["cashier_qr"] for r in (first, again, refreshed)], ["https://qr.alipay.com/bax-first", "https://qr.alipay.com/bax-first", "https://qr.alipay.com/bax-second"])
        self.assertNotEqual(first.json["qr_svg"], refreshed.json["qr_svg"])  # the image follows the new code

    def test_only_website_orders_are_prefetched_and_only_when_the_service_asks_for_it(self):
        app, client, headers = self.build(BYPAY_QR_PREFETCH="1")
        fetch = self.asking()
        with patch("web.cashier.fetch_cashier_qr", fetch):
            self.assertEqual(self.create(client, headers, "WAP_ORDER_0001", product="wap").status_code, 201)
            self.assertEqual(self.create(client, headers, "APP_ORDER_0001", product="app").status_code, 201)
            app.extensions["qr_prefetch"].wait()
        self.assertEqual(fetch.calls, [])
        for value, expected in (("1", True), ("true", True), ("ON", True), ("0", False), ("false", False), ("", False)):
            with self.subTest(value=value):
                self.assertEqual(self.build(BYPAY_QR_PREFETCH=value)[0].extensions["qr_prefetch"].enabled, expected)
        off, offered, off_headers = self.build()
        with patch("web.cashier.fetch_cashier_qr", fetch):
            self.assertEqual(self.create(offered, off_headers, "OFF_ORDER_0001").status_code, 201)
            off.extensions["qr_prefetch"].wait()
        self.assertEqual(fetch.calls, [])  # tests and tools never reach Alipay behind their back

    def test_an_order_that_stopped_waiting_before_the_job_runs_is_not_fetched_for(self):
        app, client, headers = self.build()
        prefetch = Prefetch(True, workers=1)
        app.extensions["qr_prefetch"] = prefetch
        self.addCleanup(prefetch.wait)
        gate, fetch = threading.Event(), self.asking()
        with patch("web.cashier.fetch_cashier_qr", fetch):
            prefetch.submit(gate.wait, 5)  # the only worker is busy, so the order's job has to queue
            number = self.create(client, headers).json["order"]["out_trade_no"]
            with app.extensions["store"].connect() as db: db.execute("UPDATE orders SET status='CLOSED' WHERE out_trade_no=?", (number,))
            gate.set()
            prefetch.wait(5)
        self.assertEqual(fetch.calls, [])
        number = "SPEED_ORDER_0002"
        with patch("web.cashier.fetch_cashier_qr", fetch):
            gate.clear()
            prefetch.submit(gate.wait, 5)
            self.create(client, headers, number)
            with app.extensions["store"].connect() as db: db.execute("UPDATE orders SET expires_at=? WHERE out_trade_no=?", (int(time.time()) - 5, number))
            gate.set()
            prefetch.wait(5)
        self.assertEqual(fetch.calls, [])

    # ------------------------------------------------------------------ the finished image comes with the answer
    def test_the_prepare_answer_carries_the_same_image_the_image_address_serves(self):
        app, client, headers = self.build()
        with patch("web.cashier.fetch_cashier_qr", self.asking()):
            number = self.create(client, headers).json["order"]["out_trade_no"]
            prepared = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
            again = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
        served = client.get(f"/api/v1/payments/{number}/qr.svg")
        self.assertEqual(prepared.json["qr_svg"].encode(), served.data)
        self.assertEqual(again.json["qr_svg"], prepared.json["qr_svg"])
        self.assertIn('style="color:#075fbd"', prepared.json["qr_svg"])  # the Alipay brand colour and logo are part of the image
        with app.extensions["store"].connect() as db: db.execute("UPDATE orders SET expires_at=? WHERE out_trade_no=?", (int(time.time()) - 5, number))
        expired = client.post(f"/api/v1/payments/{number}/prepare-qr", headers=headers, json={})
        self.assertEqual(expired.status_code, 200)
        self.assertIsNone(expired.json["qr_svg"])  # nothing to scan once the order is over

    def test_the_image_is_only_ever_inlined_for_a_live_alipay_website_order(self):
        live = {"channel": "alipay", "product": "page", "cashier_qr": "https://qr.alipay.com/bax", "paid_at": None, "status": "CREATED", "expires_at": 2_000_000_000}
        self.assertIn("<svg", qr_inline(live, now=1_000_000_000))
        for change in ({"channel": "wechat"}, {"product": "wap"}, {"cashier_qr": ""}, {"paid_at": 1}, {"status": "CLOSED"}, {"expires_at": 900_000_000}):
            with self.subTest(change=change):
                self.assertIsNone(qr_inline({**live, **change}, now=1_000_000_000))

    def test_the_store_knows_when_the_last_website_order_was_made(self):
        app, client, headers = self.build()
        store = app.extensions["store"]
        self.assertEqual((store.latest_page_order(), store.latest_page_order("wechat")), (0, 0))
        self.create(client, headers, "WAP_ORDER_0001", product="wap")
        self.assertEqual(store.latest_page_order(), 0)  # a phone order is not a website order
        self.create(client, headers)
        self.assertAlmostEqual(store.latest_page_order(), time.time(), delta=5)
        self.assertEqual(store.latest_page_order("wechat"), 0)

    def test_the_warm_up_starts_with_the_service_setting_and_not_otherwise(self):
        app, _, _ = self.build(BYPAY_QR_PREFETCH="1")
        self.assertTrue(app.extensions["qr_warmer"]._thread.is_alive())
        quiet, _, _ = self.build()
        self.assertIsNone(quiet.extensions["qr_warmer"]._thread)  # tests, tools and the payment worker never start it


class CashierEndpointTests(unittest.TestCase):
    """The merchant-facing cashier (what the iframe on test.bingyun.vip talks to), on the fixtures of the Epay tests."""

    @classmethod
    def setUpClass(cls): test_epay.EpayTests.setUpClass.__func__(cls)
    setUp = test_epay.EpayTests.setUp
    fields = test_epay.EpayTests.fields
    create = test_epay.EpayTests.create
    paid = test_epay.EpayTests.paid

    def token(self, response=None):
        response = response or self.create()
        return response.json["payurl"].rsplit("/", 1)[-1], response.json["trade_no"]

    def test_the_first_look_carries_the_image_when_it_is_ready_and_polling_does_not(self):
        token, _ = self.token()
        before = self.client.get(f"/api/cashier/{token}?qr=1").json
        self.assertEqual((before["qr_ready"], before["qr_svg"]), (False, None))
        with patch("web.cashier.fetch_cashier_qr", return_value="https://qr.alipay.com/fixture") as fetch:
            prepared = self.client.post(f"/api/cashier/{token}/prepare", json={}, headers={"Origin": "https://pay.example.test"}).json
            first = self.client.get(f"/api/cashier/{token}?qr=1").json
            poll = self.client.get(f"/api/cashier/{token}").json
            fetch.assert_called_once()
        self.assertTrue(prepared["qr_ready"])
        self.assertIn("<svg", prepared["qr_svg"])
        self.assertEqual(first["qr_svg"], prepared["qr_svg"])
        self.assertNotIn("qr_svg", poll)  # the 2.5 s status poll stays small
        self.assertEqual(self.client.get(f"/api/cashier/{token}/qr.svg").data.decode(), prepared["qr_svg"])  # same image as the address that older pages use

    def test_a_paid_order_gives_out_no_image(self):
        token, number = self.token()
        with patch("web.cashier.fetch_cashier_qr", return_value="https://qr.alipay.com/fixture"):
            self.client.post(f"/api/cashier/{token}/prepare", json={}, headers={"Origin": "https://pay.example.test"})
        self.paid(number)
        state = self.client.get(f"/api/cashier/{token}?qr=1").json
        self.assertEqual((state["paid"], state["qr_svg"]), (True, None))

    def test_a_merchant_order_is_fetched_for_at_creation_and_the_cashier_finds_it_ready(self):
        prefetch = Prefetch(True)
        self.app.extensions["qr_prefetch"] = prefetch
        self.addCleanup(prefetch.wait)
        with patch("web.cashier.fetch_cashier_qr", return_value="https://qr.alipay.com/early") as fetch:
            response = self.create()
            prefetch.wait()
            token, _ = self.token(response)
            state = self.client.get(f"/api/cashier/{token}?qr=1").json
            fetch.assert_called_once()  # at creation, by the prefetch; the cashier asked nothing
        self.assertEqual((state["qr_ready"], "<svg" in state["qr_svg"]), (True, True))


class ServiceTests(unittest.TestCase):
    def test_the_service_gives_the_web_processes_prefetch_and_the_payment_worker_none(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        started = []

        class Child:
            def __init__(self, command, **options):
                started.append((command, options["env"]))
                self.pid, self.calls = 4000 + len(started), 0

            def poll(self):
                self.calls += 1
                return 0 if self.calls > 1 else None

            def wait(self, timeout=None): return 0

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        state = {"env_file": str(Path(folder.name) / ".env"), "port": port, "host": "127.0.0.1"}
        configured = (state, Path(state["env_file"]), {"DATABASE": str(Path(folder.name) / "data/web.sqlite3")})

        def run(**environment):
            started.clear()
            with patch.object(service, "configuration", return_value=configured), patch.object(service.subprocess, "Popen", Child), patch.object(service, "atomic_text"), \
                    patch.object(service.signal, "signal"), patch("builtins.print"), patch.dict("os.environ", environment):
                if not environment: os.environ.pop("BYPAY_QR_PREFETCH", None)
                with warnings.catch_warnings(), self.assertRaises(SystemExit):
                    warnings.simplefilter("ignore", ResourceWarning)  # the service holds its lock file open for its whole life, as it exits right after
                    try: service.main()
                    finally: gc.collect()
            return {("web" if "gunicorn" in command[0] else "worker"): env["BYPAY_QR_PREFETCH"] for command, env in started}
        self.assertEqual(run(), {"web": "1", "worker": "0"})
        self.assertEqual(run(BYPAY_QR_PREFETCH="0"), {"web": "0", "worker": "0"})  # the owner can switch it off


class KeyCacheTests(unittest.TestCase):
    def test_the_private_key_is_prepared_once_and_a_bad_key_is_never_remembered(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        key_utils.normalize_private_key.cache_clear()
        with patch.object(key_utils, "load_private_key", wraps=key_utils.load_private_key) as load:
            first = key_utils.normalize_private_key(pem)
            for _ in range(5): self.assertEqual(key_utils.normalize_private_key(pem), first)
            self.assertEqual(load.call_count, 1)
            for _ in range(2):
                with self.assertRaises(key_utils.KeyProblem): key_utils.normalize_private_key("not a key")
            self.assertEqual(load.call_count, 3)  # asked again every time
        self.assertTrue(first.startswith("-----BEGIN RSA PRIVATE KEY-----"))


if __name__ == "__main__":
    unittest.main()
