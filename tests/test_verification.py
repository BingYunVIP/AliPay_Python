import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from web.app import ROOT, create_app

TOKEN = "YIZThTeg958vnQzp"


class VerificationFileTests(unittest.TestCase):
    """WeChat (and similar platforms) verify a domain by fetching a .txt file from the site root."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        dist = self.root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>pay</html>")
        folder = self.root / "verification"
        folder.mkdir()
        (folder / "MP_verify_AbCdEf123456.txt").write_bytes(b"AbCdEf123456")
        (folder / "WW_verify_second-file.txt").write_bytes(b"second")
        (folder / "settings.json").write_text('{"secret": true}')
        (folder / "big_verification_file.txt").write_bytes(b"x" * 2048)
        outside = self.root / "outside.txt"
        outside.write_text("not for the web")
        (folder / "MP_verify_linked0000.txt").symlink_to(outside)
        (folder / ".hidden_verify_file.txt").write_bytes(b"hidden")
        self.app = create_app({"TESTING": True, "PUBLIC_BASE_URL": "https://pay.example.test", "DATABASE": str(self.root / "pay.sqlite3"),
                               "FRONTEND_DIST": str(dist), "VERIFICATION_DIR": str(folder),
                               "BYPAY_ADMIN_EMAIL": "admin@example.test", "BYPAY_ADMIN_PASSWORD": "Admin-password-123"})
        self.client = self.app.test_client()

    @staticmethod
    def fetch(client, path, method="get"):
        """Read the whole answer and close it, so that no file handle is left behind."""
        with getattr(client, method)(path) as response:
            return SimpleNamespace(status_code=response.status_code, data=response.get_data(), headers=response.headers)

    def test_a_file_in_the_folder_is_served_from_the_site_root_exactly_as_stored(self):
        response = self.fetch(self.client, "/MP_verify_AbCdEf123456.txt")
        self.assertEqual((response.status_code, response.data), (200, b"AbCdEf123456"))
        self.assertTrue(response.headers["Content-Type"].startswith("text/plain"))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.fetch(self.client, "/MP_verify_AbCdEf123456.txt", "head").status_code, 200)
        # other platforms use other names; any plain name in the folder works without a rebuild or restart
        self.assertEqual(self.fetch(self.client, "/WW_verify_second-file.txt").data, b"second")
        (Path(self.app.config["VERIFICATION_DIR"]) / "MP_verify_added_later1.txt").write_bytes(b"later")
        self.assertEqual(self.fetch(self.client, "/MP_verify_added_later1.txt").data, b"later")

    def test_only_plain_text_files_of_a_sensible_size_are_served(self):
        for path in ("/settings.json", "/big_verification_file.txt", "/MP_verify_linked0000.txt", "/.hidden_verify_file.txt", "/MP_verify_nope000000.txt", "/short.txt"):
            with self.subTest(path=path):
                response = self.fetch(self.client, path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(b"secret", response.data)
                self.assertNotIn(b"not for the web", response.data)

    def test_paths_cannot_escape_the_folder(self):
        for path in ("/../outside.txt", "/%2e%2e/outside.txt", "/..%2foutside.txt", "/verification/MP_verify_AbCdEf123456.txt",
                     "/MP_verify_AbCdEf123456.txt/", "/MP_verify_AbCdEf123456.txt/../outside.txt", "/MP_verify_AbCdEf123456.TXT"):
            with self.subTest(path=path):
                self.assertEqual(self.fetch(self.client, path).status_code, 404, path)

    def test_the_rest_of_the_site_is_unchanged(self):
        self.assertEqual(self.fetch(self.client, "/login").data, b"<html>pay</html>")
        self.assertEqual(self.fetch(self.client, "/api/capabilities").status_code, 200)
        self.assertEqual(self.fetch(self.client, "/robots.txt").status_code, 404)  # not in the folder, so not invented

    def test_the_file_shipped_for_the_wechat_js_safe_domain_is_in_the_repository(self):
        shipped = ROOT / "verification" / f"MP_verify_{TOKEN}.txt"
        self.assertEqual(shipped.read_bytes(), TOKEN.encode())  # byte for byte what WeChat issued: no newline, no BOM
        app = create_app({"TESTING": True, "PUBLIC_BASE_URL": "https://pay.example.test", "DATABASE": str(self.root / "other.sqlite3"),
                          "BYPAY_ADMIN_EMAIL": "admin@example.test", "BYPAY_ADMIN_PASSWORD": "Admin-password-123"})
        self.assertEqual(Path(app.config["VERIFICATION_DIR"]), ROOT / "verification")  # the default folder, with no setting needed
        response = self.fetch(app.test_client(), f"/MP_verify_{TOKEN}.txt")
        self.assertEqual((response.status_code, response.data), (200, TOKEN.encode()))


if __name__ == "__main__":
    unittest.main()
