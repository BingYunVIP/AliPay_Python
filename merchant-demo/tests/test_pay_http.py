import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import initialize
from pay_http import request_json


class PayHttpTests(unittest.TestCase):
    def mock_client(self, handler):
        real_client = httpx.Client
        transport = httpx.MockTransport(handler)
        def factory(**kwargs):
            self.assertFalse(kwargs["follow_redirects"])
            self.assertFalse(kwargs["trust_env"])
            self.assertTrue(kwargs.get("verify", True))
            return real_client(transport=transport, **kwargs)
        return patch("pay_http.httpx.Client", side_effect=factory)

    def test_uses_httpx_identity_for_public_key_download(self):
        received = []
        def handle(request):
            received.append(request)
            return httpx.Response(200, json={"algorithm": "Ed25519", "public_key": "example"})
        with self.mock_client(handle):
            data, status = request_json("GET", "https://pay.bingyun.vip/api/webhooks/public-key")
        self.assertEqual(status, 200)
        self.assertEqual(data["algorithm"], "Ed25519")
        self.assertTrue(received[0].headers["User-Agent"].startswith("python-httpx/"))
        self.assertNotIn("Authorization", received[0].headers)

    def test_no_redirect_can_forward_api_credentials(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "https://another.example/collect"})
        with self.mock_client(handle):
            data, status = request_json("POST", "https://pay.bingyun.vip/api/v1/payments", payload={"amount": "0.01"}, api_key="byp_test_key")
        self.assertEqual((data["error"], status), ("pay_redirect_rejected", 302))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer byp_test_key")
        self.assertEqual(json.loads(requests[0].content), {"amount": "0.01"})

    def test_html_error_does_not_leak_into_logs(self):
        with self.mock_client(lambda _: httpx.Response(403, text="<html>Cloudflare error 1010</html>")):
            self.assertEqual(request_json("GET", "https://pay.bingyun.vip/health"), ({"error": "pay_http_error"}, 403))

    def test_invalid_and_oversized_json_responses_rejected(self):
        for response in [httpx.Response(200, json=[]), httpx.Response(200, text="invalid"), httpx.Response(200, json={"value": "x" * 70000})]:
            with self.subTest(response=response), self.mock_client(lambda _, result=response: result):
                data, status = request_json("GET", "https://pay.bingyun.vip/api/webhooks/public-key")
                self.assertEqual(status, 502)
                self.assertIn("error", data)

    def test_timeout_is_reported_without_credentials(self):
        def handle(_):
            raise httpx.ConnectTimeout("unreachable")
        with self.mock_client(handle):
            self.assertEqual(request_json("GET", "https://pay.bingyun.vip/health"), ({"error": "pay_unreachable"}, 502))

    def test_native_config_defaults_require_explicit_epay_merchant_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(initialize,'ROOT',root),patch.dict(os.environ,{},clear=True),patch('builtins.print'):
                initialize.main()
            env=(root/'.env').read_text()
            self.assertIn('PAY_MERCHANT_ID',env);self.assertIn('PAY_API_VERSION',env)
            self.assertNotIn('PAY_API_KEY',env);self.assertIn('http://127.0.0.1:888',env)
            self.assertEqual((root/'.env').stat().st_mode & 0o777,0o600)

    def test_merchant_does_not_load_parent_pay_env(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            (root / ".env").write_text("DATABASE=/must/not/use/pay.sqlite3\n")
            child = root / "merchant-demo"
            child.mkdir()
            source = Path(__file__).resolve().parents[1] / "app.py"
            shutil.copyfile(source, child / "app.py")
            spec = importlib.util.spec_from_file_location("isolated_merchant", child / "app.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertNotIn("DATABASE", os.environ)


if __name__ == "__main__":
    unittest.main()
