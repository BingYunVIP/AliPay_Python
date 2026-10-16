import unittest
from unittest.mock import patch

from web.webhooks import local_callback_origins, send_to_merchant, validate_callback


class LocalCallbackTests(unittest.TestCase):
    def test_local_pay_accepts_only_explicit_demo_notify_endpoints(self):
        allowed=local_callback_origins('http://localhost:888')
        for url in ('http://127.0.0.1:8100/notify','http://localhost:8100/notify','http://[::1]:8100/notify'):
            self.assertEqual(validate_callback(url,set(),allowed).path,'/notify')
        for url in ('http://127.0.0.1:888/notify','http://127.0.0.1:8100/api/config','http://127.0.0.1:8100/notify?x=1','http://127.0.0.1:8100/notify#x','http://169.254.169.254:8100/notify','http://evil.test:8100/notify','http://user@127.0.0.1:8100/notify'):
            with self.subTest(url=url),self.assertRaises(ValueError):validate_callback(url,set(),allowed)

    def test_https_and_public_pay_do_not_enable_local_delivery(self):
        for origin in ('https://pay.bingyun.vip','https://localhost','http://203.0.113.10:888'):
            self.assertEqual(local_callback_origins(origin),set())
            with self.assertRaises(ValueError):validate_callback('http://127.0.0.1:8100/notify',set(),local_callback_origins(origin))

    def test_local_delivery_is_pinned_to_loopback_and_preserves_signed_body(self):
        body=b'{"signed":"body"}';headers={'X-ByPay-Signature':'test-signature'}
        with patch('web.webhooks.http.client.HTTPConnection') as connection,patch('socket.getaddrinfo',side_effect=AssertionError('DNS should not be used')):
            response=connection.return_value.getresponse.return_value
            response.status=200;response.read.return_value=b'success'
            self.assertEqual(send_to_merchant('http://localhost:8100/notify',body,headers,set(),local_origins=local_callback_origins('http://localhost:888')),(True,200))
            connection.assert_called_once_with('127.0.0.1',port=8100,timeout=10)
            connection.return_value.request.assert_called_once_with('POST','/notify',body=body,headers=headers)
            connection.return_value.close.assert_called_once()


if __name__=='__main__':unittest.main()
