import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values
from web.runtime import check_ports, load_runtime, prepare, resolve_launch


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_local_default_does_not_inherit_production_domain_or_cookie_policy(self):
        with patch.dict(os.environ, {'BYPAY_RUN_MODE': 'local', 'BYPAY_ENV_FILE': str(self.root / '.env.local'),
                                     'PUBLIC_BASE_URL': 'https://pay.bingyun.vip', 'BYPAY_TRUST_PROXY': '1',
                                     'ALIPAY_ENV': 'production', 'BYPAY_FRONTEND_PORT': '18000', 'BYPAY_BACKEND_PORT': '18001'}, clear=True), \
                patch('web.runtime.local_hosts', return_value=['localhost', '127.0.0.1', 'bypc', '192.168.1.20']):
            cfg = load_runtime()
        self.assertEqual(cfg['PUBLIC_BASE_URL'], 'http://localhost:18000')
        self.assertEqual(cfg['ALIPAY_ENV'], 'sandbox')
        self.assertEqual(cfg['BYPAY_TRUST_PROXY'], '0')
        for origin in ['http://localhost:18000', 'http://127.0.0.1:18000', 'http://bypc:18000', 'http://192.168.1.20:18000']:
            self.assertIn(origin, cfg['BYPAY_BROWSER_ORIGINS'].split(','))

    def test_explicit_forward_origin_is_validated(self):
        env = {'BYPAY_RUN_MODE': 'local', 'BYPAY_ENV_FILE': str(self.root / '.env.local')}
        with patch.dict(os.environ, {**env, 'BYPAY_EXTRA_ORIGINS': 'http://localhost:5173'}, clear=True):
            self.assertIn('http://localhost:5173', load_runtime()['BYPAY_BROWSER_ORIGINS'])
        with patch.dict(os.environ, {**env, 'BYPAY_EXTRA_ORIGINS': 'https://user@evil.test/path'}, clear=True):
            with self.assertRaises(ValueError): load_runtime()

    def test_prepare_is_persistent_and_does_not_touch_production_config(self):
        env_file = self.root / '.env.local'
        production = self.root / '.env'
        production.write_text('PUBLIC_BASE_URL=https://pay.bingyun.vip\n')
        env = {'BYPAY_RUN_MODE': 'local', 'BYPAY_ENV_FILE': str(env_file), 'BYPAY_LOCAL_DATA_DIR': str(self.root / 'local'),
               'BYPAY_LOCAL_ADMIN_EMAIL': 'admin@test.local', 'BYPAY_LOCAL_ADMIN_PASSWORD': 'Native-test-password-123'}
        with patch.dict(os.environ, env, clear=True), patch('builtins.print'):
            prepare()
            first = env_file.read_bytes()
            prepare()
            self.assertEqual(first, env_file.read_bytes())
        self.assertEqual(production.read_text(), 'PUBLIC_BASE_URL=https://pay.bingyun.vip\n')
        values = dotenv_values(env_file)
        self.assertEqual(values['BYPAY_ADMIN_EMAIL'], 'admin@test.local')
        credentials = self.root / 'local/.admin-credentials'
        self.assertTrue(credentials.is_file())
        self.assertEqual(credentials.stat().st_mode & 0o777, 0o600)

    def test_production_uses_https_config_and_rejects_local_mode_mixup(self):
        env_file = self.root / '.env'
        env_file.write_text('PUBLIC_BASE_URL=https://pay.bingyun.vip\nBYPAY_ADMIN_EMAIL=admin@test.local\n')
        with patch.dict(os.environ, {'BYPAY_RUN_MODE': 'production', 'BYPAY_ENV_FILE': str(env_file)}, clear=True):
            cfg = load_runtime()
        self.assertEqual(cfg['PUBLIC_BASE_URL'], 'https://pay.bingyun.vip')
        self.assertEqual(cfg['BYPAY_BROWSER_ORIGINS'], '')
        env_file.write_text('PUBLIC_BASE_URL=http://localhost:8000\n')
        with patch.dict(os.environ, {'BYPAY_RUN_MODE': 'production', 'BYPAY_ENV_FILE': str(env_file)}, clear=True):
            with self.assertRaises(ValueError): load_runtime()

    def test_same_entry_auto_selects_local_or_https_from_configuration(self):
        with patch('web.runtime.ROOT', self.root), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_launch(), ('local', self.root / '.env.local'))
            (self.root / '.env.local').write_text('ALIPAY_ENV=production\n')
            # Real Alipay credentials do not turn localhost into an HTTPS deployment.
            self.assertEqual(resolve_launch()[0], 'local')
            (self.root / '.env').write_text('PUBLIC_BASE_URL=https://pay.bingyun.vip\n')
            self.assertEqual(resolve_launch(), ('production', self.root / '.env'))
            self.assertEqual(load_runtime()['PUBLIC_BASE_URL'], 'https://pay.bingyun.vip')
            (self.root / '.env').write_text('PUBLIC_BASE_URL=http://evil.example.test\n')
            with self.assertRaises(ValueError): resolve_launch()

    def test_explicit_file_auto_selection_and_missing_file_detection(self):
        source = self.root / 'site.env'
        with patch.dict(os.environ, {'BYPAY_ENV_FILE': str(source)}, clear=True):
            with self.assertRaises(ValueError): resolve_launch()
            source.write_text('PUBLIC_BASE_URL=https://pay.bingyun.vip\n')
            self.assertEqual(resolve_launch(), ('production', source))
            source.write_text('PUBLIC_BASE_URL=http://localhost:8000\n')
            self.assertEqual(resolve_launch(), ('local', source))

    def test_port_preflight_rejects_listener_but_allows_immediate_restart(self):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        self.addCleanup(listener.close)
        env = {'BYPAY_RUN_MODE': 'production', 'BYPAY_FRONTEND_PORT': str(port),
               'BYPAY_BACKEND_PORT': str(port), 'BYPAY_BIND_HOST': '127.0.0.1'}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, '已占用'): check_ports()
            with socket.create_connection(('127.0.0.1', port)):
                connection, _ = listener.accept()
                connection.close()
            listener.close()
            check_ports()

    def test_default_browser_port_is_888(self):
        with patch.dict(os.environ, {'BYPAY_RUN_MODE':'local','BYPAY_ENV_FILE':str(self.root/'.env.local')}, clear=True):
            config=load_runtime()
        self.assertEqual(config['PUBLIC_BASE_URL'],'http://localhost:888')
        self.assertIn('http://127.0.0.1:888',config['BYPAY_BROWSER_ORIGINS'])


if __name__ == '__main__':
    unittest.main()
