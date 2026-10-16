"""Prepare local defaults; API credentials are edited and verified in the browser."""
import os
from pathlib import Path
from dotenv import dotenv_values, set_key, unset_key
from configuration import Configuration, ConfigurationError, fetch_public_key, valid_public_key

ROOT = Path(__file__).resolve().parent


def main():
    os.umask(0o077)
    source = ROOT / '.env'
    values = {k:v for k,v in dotenv_values(source, interpolate=False).items() if v is not None} if source.exists() else {}
    if not source.exists(): source.touch(mode=0o600)
    port = os.environ.get('DEMO_PORT', values.get('DEMO_PORT', '8100'))
    defaults = {'PAY_ORIGIN':'http://127.0.0.1:888', 'DEMO_ORIGIN':f'http://127.0.0.1:{port}',
                'PAY_API_KEY':'', 'PAY_PUBLIC_KEY_FILE':str(ROOT/'secrets/bypay-public.pem'),
                'DATABASE':str(ROOT/'data/demo.sqlite3'), 'DEMO_PORT':port, 'DEMO_BIND_HOST':'0.0.0.0'}
    for key,value in defaults.items():
        if key not in values:
            values[key]=os.environ.get(key,value)
            set_key(source,key,values[key])
    for obsolete in ('DEMO_USER','DEMO_PASSWORD'):
        if obsolete in values: unset_key(source,obsolete)
    source.chmod(0o600)
    config = Configuration(source, values, Path(values['DATABASE']).parent/'config.lock')
    current = config.read()
    if not valid_public_key(current.get('PAY_PUBLIC_KEY_PEM')):
        try:
            pem = fetch_public_key(current['PAY_ORIGIN'])
            target = Path(values['PAY_PUBLIC_KEY_FILE'])
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            target.write_text(pem); target.chmod(0o600)
        except ConfigurationError:
            print('回调公钥尚未获取，启动 Pay 后可在网页验证并保存')
    print('Pay API 密钥：'+('已保存，请在网页验证有效性' if values['PAY_API_KEY'] else '未填写，可在网页配置'))
    print('回调验签公钥：'+('已保存' if config.public()['verification_key_configured'] else '待获取'))


if __name__=='__main__':
    try: main()
    except (ValueError,OSError) as exc: raise SystemExit('启动准备失败：'+str(exc))
