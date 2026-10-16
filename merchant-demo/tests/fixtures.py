import base64
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import set_key

from app import create_app
from epay_protocol import public_pem, signed, verify


class MerchantFixture:
    def setup_merchant(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.platform=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        self.merchant=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        self.md5='abcdef0123456789'*2;self.accepted_md5=self.md5
        self.private=self.merchant.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
        self.values={'PAY_ORIGIN':'https://pay.bingyun.vip','DEMO_ORIGIN':'https://test.bingyun.vip','PAY_MERCHANT_ID':'1000','PAY_API_VERSION':'v1','PAY_MD5_KEY':self.md5,'PAY_MERCHANT_PRIVATE_KEY':self.private,'PAY_PLATFORM_PUBLIC_KEY':public_pem(self.platform)}
        self.env=self.root/'.env';self.env.touch(mode=0o600)
        for k,v in self.values.items():set_key(self.env,k,v)
        self.settings={**self.values,'TESTING':True,'CONFIG_FILE':str(self.env),'DATABASE':str(self.root/'demo.sqlite3'),'DEMO_PORT':'8100','DEMO_SETUP_TOKEN':'setup-fixture-token'}
        self.app=create_app(self.settings);self.client=self.app.test_client();self.config=self.app.extensions['configuration']
        self.headers={'Origin':'http://127.0.0.1:8100','X-Demo-Request':'1'}
        self.requests=[];self.paid=False;self.wrong_amount=False;self.bad_response=False
        real_client=httpx.Client
        def handle(request):
            self.requests.append(request)
            params={k:v[0] for k,v in parse_qs(request.content.decode(),keep_blank_values=True).items()}
            version='v2' if request.url.path.startswith('/api/') else 'v1'
            valid=str(params.get('pid'))=='1000' and (verify(params,self.merchant.public_key(),'RSA',timestamp=True) if version=='v2' else params.get('key')==self.accepted_md5 or verify(params,self.accepted_md5))
            if not valid: result={'code':-3,'error':'invalid_signature','msg':'签名校验失败'}
            elif request.url.path in {'/api/merchant/info','/api.php'} and params.get('act','query')=='query':result={'code':0 if version=='v2' else 1,'pid':1000,'status':1}
            elif request.url.path in {'/mapi.php','/api/pay/create'}:
                result={'code':0 if version=='v2' else 1,'trade_no':'2026101600000012345'}
                result.update({'pay_type':'jump','pay_info':'https://pay.bingyun.vip/cashier/'+'a'*43} if version=='v2' else {'payurl':'https://pay.bingyun.vip/cashier/'+'a'*43})
            else:
                result={'code':0 if version=='v2' else 1,'pid':1000,'trade_no':'2026101600000012345','out_trade_no':params['out_trade_no'],'type':'alipay','money':'0.02' if self.wrong_amount else '0.01','status':1 if self.paid else 0}
            if version=='v2':result=signed(result,self.platform,'RSA');result['sign']='invalid' if self.bad_response else result['sign']
            return httpx.Response(200,json=result)
        transport=httpx.MockTransport(handle);mock=patch('pay_http.httpx.Client',side_effect=lambda **kw:real_client(transport=transport,**kw));mock.start();self.addCleanup(mock.stop)

    def post(self,path,payload,**kwargs):return self.client.post(path,json=payload,headers=self.headers,base_url='http://127.0.0.1:8100',**kwargs)

    def create(self,version='v1'):
        if version=='v2':self.assertEqual(self.post('/api/config',{'api_version':'v2'}).status_code,200)
        response=self.post('/api/orders',{'amount':'0.01'})
        self.assertEqual(response.status_code,201,response.json)
        return response.json['order']

    def callback(self,order,**changes):
        fields={'pid':'1000','trade_no':order['platform_no'],'out_trade_no':order['number'],'type':'alipay','name':'独立商户支付测试','money':'0.01','trade_status':'TRADE_SUCCESS',**changes}
        return signed(fields,self.platform if order['api_version']=='v2' else self.md5,'RSA' if order['api_version']=='v2' else 'MD5')
