import unittest
from unittest.mock import patch
from dotenv import dotenv_values
from fixtures import MerchantFixture


class ConfigurationTests(MerchantFixture,unittest.TestCase):
    def setUp(self):self.setup_merchant()

    def test_local_editor_has_merchant_fields_without_returning_secrets(self):
        response=self.client.get('/api/config',base_url='http://127.0.0.1:8100')
        self.assertTrue(response.json['can_manage']);self.assertEqual(response.json['merchant_id'],'1000')
        self.assertNotIn(self.md5,response.get_data(as_text=True));self.assertNotIn(self.private,response.get_data(as_text=True))

    def test_invalid_replacement_keeps_existing_credentials(self):
        before=self.env.read_bytes()
        response=self.post('/api/config',{'md5_key':'invalid-md5-key-0000'})
        self.assertEqual(response.status_code,422);self.assertEqual(self.env.read_bytes(),before)

    def test_v1_and_v2_validation_does_not_create_orders(self):
        for version in ('v1','v2'):
            response=self.post('/api/config/check',{'api_version':version})
            self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.client.get('/api/state').json['orders'],[])
        self.assertEqual(self.client.get('/api/state').json['logs'][0]['detail']['api_version'],'v2')
        self.assertFalse(any(r.url.path in {'/mapi.php','/api/pay/create'} for r in self.requests))

    def test_v2_platform_signature_must_validate_before_saving(self):
        self.bad_response=True;before=self.env.read_bytes()
        response=self.post('/api/config',{'api_version':'v2'})
        self.assertEqual(response.json['error'],'platform_signature_invalid');self.assertEqual(self.env.read_bytes(),before)

    def test_save_takes_effect_without_restart_and_keeps_blank_secrets(self):
        self.assertEqual(self.post('/api/config',{'api_version':'v2','merchant_private_key':''}).status_code,200)
        self.assertEqual(dotenv_values(self.env)['PAY_MERCHANT_PRIVATE_KEY'],self.private)
        self.assertEqual(self.create('v2')['api_version'],'v2')
        self.assertEqual(self.env.stat().st_mode & 0o777,0o600)

    def test_remote_configuration_uses_startup_capability_without_a_login_account(self):
        origin='https://test.bingyun.vip'
        headers={**self.headers,'Origin':origin}
        self.assertEqual(self.client.post('/api/config/check',json={},headers=headers,base_url=origin).status_code,403)
        headers['X-Demo-Setup']='setup-fixture-token'
        self.assertEqual(self.client.post('/api/config/check',json={},headers=headers,base_url=origin).status_code,200)
        headers['Origin']='https://evil.test'
        self.assertEqual(self.client.post('/api/config',json={},headers=headers,base_url=origin).status_code,403)

    def test_loopback_aliases_share_local_cashier_but_other_hosts_and_ports_do_not(self):
        from connection import create,ConnectionError
        values={**self.values,'PAY_ORIGIN':'http://127.0.0.1:888'}
        body={'code':1,'trade_no':'2026101600000001','payurl':'http://localhost:888/cashier/'+'a'*43}
        with patch('connection.request_json',return_value=(body,200)):
            self.assertTrue(create(values,'LOCAL_TEST_01','0.01','test')['pay_url'].startswith('http://localhost:888/'))
        for value in ('http://localhost:889/cashier/','http://evil.example:888/cashier/'):
            with patch('connection.request_json',return_value=({**body,'payurl':value+'a'*43},200)),self.assertRaises(ConnectionError):
                create(values,'LOCAL_TEST_01','0.01','test')

    def test_failed_disk_write_keeps_previous_config(self):
        before=self.env.read_bytes()
        with patch('configuration.os.replace',side_effect=OSError('disk full')):response=self.post('/api/config',{'api_version':'v2'})
        self.assertEqual(response.status_code,500);self.assertEqual(self.env.read_bytes(),before)


if __name__=='__main__':unittest.main()
