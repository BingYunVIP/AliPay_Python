import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values
import configure


class NativeConfigurationTests(unittest.TestCase):
    def test_configure_preserves_key_ownership_and_uses_project_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "input-private.pem"
            public = root / "input-public.pem"
            private.write_text("private fixture")
            public.write_text("public fixture")
            answers = ["pay.bingyun.vip", "admin@example.test", "production", "2026000000000000",
                       "2088000000000000", str(private), str(public), "false"]
            with patch.object(configure, "ROOT", root), patch("configure.ask", side_effect=answers), \
                    patch("configure.getpass.getpass", return_value="Native-config-test-password"), \
                    patch("configure.os.chown") as chown, patch("builtins.print"):
                configure.main()
            values = dotenv_values(root / ".env")
            self.assertEqual(values["DATABASE"], str(root / "data/web.sqlite3"))
            self.assertEqual(values["ALIPAY_PRIVATE_KEY_FILE"], str(root / "secrets/app-private.pem"))
            self.assertEqual(values["ALIPAY_PUBLIC_KEY_FILE"], str(root / "secrets/alipay-public.pem"))
            self.assertEqual(values["BYPAY_LOG_FILE"], str(root / "data/logs/payment.jsonl"))
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)
            self.assertEqual(private.read_text(), "private fixture")
            chown.assert_not_called()

    def test_existing_configuration_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / ".env"
            target.write_text("existing configuration")
            with patch.object(configure, "ROOT", root), self.assertRaises(SystemExit):
                configure.main()
            self.assertEqual(target.read_text(), "existing configuration")


if __name__ == "__main__":
    unittest.main()
