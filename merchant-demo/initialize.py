"""Prepare independent merchant configuration without inventing merchant credentials."""
import os
from pathlib import Path
from dotenv import dotenv_values, set_key, unset_key

ROOT=Path(__file__).resolve().parent


def main():
    os.umask(0o077)
    source=ROOT/'.env'
    values={k:v for k,v in dotenv_values(source,interpolate=False).items() if v is not None} if source.exists() else {}
    if not source.exists():source.touch(mode=0o600)
    port=os.environ.get('DEMO_PORT',values.get('DEMO_PORT','8100'))
    defaults={'PAY_ORIGIN':'http://127.0.0.1:888','DEMO_ORIGIN':f'http://127.0.0.1:{port}',
              'PAY_MERCHANT_ID':'','PAY_API_VERSION':'v1','PAY_MD5_KEY':'','PAY_MERCHANT_PRIVATE_KEY':'','PAY_PLATFORM_PUBLIC_KEY':'',
              'DATABASE':str(ROOT/'data/demo.sqlite3'),'DEMO_PORT':port,'DEMO_BIND_HOST':'0.0.0.0'}
    for key,value in defaults.items():
        if key not in values:values[key]=os.environ.get(key,value);set_key(source,key,values[key])
    for obsolete in ('DEMO_USER','DEMO_PASSWORD'):
        if obsolete in values:unset_key(source,obsolete)
    source.chmod(0o600)
    print('商户 ID：'+(values['PAY_MERCHANT_ID'] or '待在网页填写'))
    print('接口版本：'+values['PAY_API_VERSION'].upper())
    print('商户密钥与平台公钥可在网页配置和验证')


if __name__=='__main__':main()
