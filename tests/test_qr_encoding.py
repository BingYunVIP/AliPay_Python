"""Real SVG encoding/decoding with static text; no gateway or payment claims."""
import io
import unittest
import cairosvg
import zxingcpp
from PIL import Image
from web.qrcode import payment_qr_svg

class EncodingTests(unittest.TestCase):
    def assert_decodes(self,value,channel,asset=''):
        svg=payment_qr_svg(value,channel,asset)
        for size in (240,320):
            with Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg.encode(),output_width=size,output_height=size))) as image:
                result=zxingcpp.read_barcode(image.convert('RGB'))
                self.assertIsNotNone(result);self.assertEqual(result.text,value)
        return svg
    def test_alipay_style(self):self.assert_decodes('https://example.com/qr/encoding-check','alipay')
    def test_wechat_style(self):self.assert_decodes('https://example.com/qr/encoding-check','wechat')
    def test_evm_address_usdt(self):self.assert_decodes('0x'+'1234567890abcdef'*2+'12345678','crypto','USDT')
    def test_evm_address_usdc(self):self.assert_decodes('0x'+'abcdef1234567890'*2+'12345678','crypto','USDC')
    def test_solana_address(self):self.assert_decodes('So11111111111111111111111111111111111111112','crypto','USDT')
    def test_dense_uri_remains_unobscured(self):
        value='https://example.com/?payload='+'a'*700
        svg=payment_qr_svg(value,'alipay')
        self.assertNotIn('rx="1.3"',svg)
        with Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg.encode(),output_width=720,output_height=720))) as image:
            self.assertEqual(zxingcpp.read_barcode(image.convert('RGB')).text,value)

if __name__=='__main__':unittest.main()
