import tempfile
import unittest
from pathlib import Path

from web.app import create_app


class BrowserOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def make_app(self, origin):
        return create_app({
            "TESTING": True,
            "PUBLIC_BASE_URL": origin,
            "DATABASE": str(Path(self.temp.name) / "pay.sqlite3"),
            "BYPAY_ADMIN_EMAIL": "origin-test@example.test",
            "BYPAY_ADMIN_PASSWORD": "Origin-test-password-123",
        })

    def login(self, client, origin, **options):
        return client.post("/api/auth/login", headers={"Origin": origin},
                           json={"email": "origin-test@example.test", "password": "Origin-test-password-123"}, **options)

    def test_localhost_and_loopback_addresses_can_log_in(self):
        app = self.make_app("http://localhost:8000")
        for origin in ("http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"):
            with self.subTest(origin=origin):
                client = app.test_client()
                response = self.login(client, origin, base_url=origin)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["user"]["role"], "admin")
                me = client.get("/api/auth/me", base_url=origin)
                self.assertEqual(me.status_code, 200)
                missing_csrf = client.post("/api/keys", base_url=origin, headers={"Origin": origin})
                self.assertEqual(missing_csrf.status_code, 403)
                logout = client.post("/api/auth/logout", base_url=origin,
                                     headers={"Origin": origin, "X-CSRF-Token": response.json["csrf_token"]})
                self.assertEqual(logout.status_code, 200)

    def test_loopback_configuration_accepts_localhost_alias(self):
        app = self.make_app("http://127.0.0.1:8000")
        self.assertEqual(self.login(app.test_client(), "http://localhost:8000").status_code, 200)

    def test_other_hosts_ports_and_schemes_are_rejected(self):
        app = self.make_app("http://localhost:8000")
        for origin in ("http://localhost:5173", "http://127.0.0.1:8001", "https://localhost:8000",
                       "http://localhost.evil.test:8000", "https://evil.test", "null"):
            with self.subTest(origin=origin):
                response = self.login(app.test_client(), origin)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json["error"], "invalid_origin")

    def test_production_origin_stays_strict(self):
        app = self.make_app("https://pay.example.test")
        for origin in ("http://localhost:8000", "http://127.0.0.1:8000", "http://pay.example.test", "https://test.example.test"):
            with self.subTest(origin=origin):
                self.assertEqual(self.login(app.test_client(), origin, base_url="https://pay.example.test").status_code, 403)
        response = self.login(app.test_client(), "https://pay.example.test", base_url="https://pay.example.test")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Secure", response.headers["Set-Cookie"])

    def test_local_launcher_can_allow_its_lan_hostname_and_vite_port(self):
        app = self.make_app("http://localhost:8000")
        app.config.update(BYPAY_RUN_MODE="local", BYPAY_BROWSER_ORIGINS="http://bypc:8000,http://192.168.1.20:8000,http://localhost:5173")
        app = create_app(dict(app.config))
        for origin in ("http://bypc:8000", "http://192.168.1.20:8000", "http://localhost:5173"):
            self.assertEqual(self.login(app.test_client(), origin).status_code, 200)
        self.assertEqual(self.login(app.test_client(), "https://evil.test").status_code, 403)

    def test_local_flag_cannot_relax_a_production_https_origin(self):
        app = self.make_app("https://pay.example.test")
        app.config.update(BYPAY_RUN_MODE="local", BYPAY_BROWSER_ORIGINS="http://localhost:8000")
        app = create_app(dict(app.config))
        self.assertEqual(self.login(app.test_client(), "http://localhost:8000").status_code, 403)


if __name__ == "__main__":
    unittest.main()
