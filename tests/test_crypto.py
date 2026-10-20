"""Offline asset/address and quotation checks, never fake RPC or fabricated paid state."""
import unittest
from decimal import Decimal
from web.crypto import ASSETS,asset_for,validate_address,CryptoError,BEPUSDT_RPC_DEFAULTS,rpc_http_endpoint,TRON_HTTP_RPC_DEFAULT,amount_for_cny
from web.payment_terms import matches,overlaps,method_label

class CryptoRegistry(unittest.TestCase):
    def test_seventeen_assets(self):self.assertEqual(len(ASSETS),17)
    def test_no_tron_usdc(self):
        with self.assertRaises(CryptoError):asset_for('TRON','USDC')
    def test_registered_contract_formats(self):
        for asset in ASSETS:
            with self.subTest(asset=asset.code):self.assertTrue(validate_address(asset,asset.contract))
    def test_explicit_bridged_identity(self):
        self.assertIn('Binance',asset_for('BSC','USDC').identity);self.assertIn('USDT0',asset_for('PLASMA','USDT').identity)
    def test_nine_default_networks(self):self.assertEqual(len(BEPUSDT_RPC_DEFAULTS),9)
    def test_tron_adapter_uses_public_http_transport(self):self.assertEqual(rpc_http_endpoint('TRON',BEPUSDT_RPC_DEFAULTS['TRON']),TRON_HTTP_RPC_DEFAULT)
    def test_cny_principal_example(self):self.assertEqual(amount_for_cny('100.00',Decimal('7.00'),18),'14.29')
    def test_fixed_quote_example(self):self.assertEqual(amount_for_cny('42.00',Decimal('6.00'),6),'7.00')
    def test_payment_label_registry(self):self.assertEqual(method_label('crypto','BSC'),'BSC链');self.assertEqual(method_label('alipay'),'支付宝')
    def test_exact_rejects_fee_deduction(self):self.assertFalse(matches('14.28','14.29'))
    def test_prefix_rejects_integer_prefix_collision(self):self.assertFalse(matches('1000','100.00','prefix'))
    def test_adjacent_exact_slots_do_not_overlap(self):self.assertFalse(overlaps('14.29','exact','14.30','exact'))

if __name__=='__main__':unittest.main()
