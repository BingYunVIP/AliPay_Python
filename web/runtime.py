"""Explicit current runtime configuration. No previous-version discovery."""
import argparse
import os
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parent.parent

def local_hosts():return ['localhost','127.0.0.1','::1']

def browser_origins(hosts,ports):
    return sorted({f"http://{'['+host+']' if ':' in host else host}:{port}" for host in hosts for port in ports})

def resolve_launch():
    mode=os.environ.get('BYPAY_RUN_MODE','managed')
    if mode not in {'managed','local','production'}:raise ValueError('invalid_runtime_mode')
    return mode,Path(os.environ.get('BYPAY_ENV_FILE',str(ROOT/'.env'))).resolve()

def env_path():return resolve_launch()[1]

def load_runtime():
    mode,source=resolve_launch()
    values={key:value for key,value in dotenv_values(source).items() if value is not None} if source.is_file() else {}
    values.update({key:value for key,value in os.environ.items() if key.startswith('BYPAY_') or key in {'PUBLIC_BASE_URL','DATABASE','TEST_MAX_AMOUNT','ALLOW_REGISTRATION'}})
    port=int(values.get('BYPAY_FRONTEND_PORT','888'))
    values.setdefault('PUBLIC_BASE_URL',f'http://127.0.0.1:{port}')
    values.setdefault('DATABASE',str(ROOT/'data/web.sqlite3'))
    values.setdefault('BYPAY_LOG_FILE',str(ROOT/'data/logs/payment.jsonl'))
    values.setdefault('ALLOW_REGISTRATION','false')
    values.setdefault('BYPAY_CALLBACK_HOSTS','')
    values['BYPAY_RUN_MODE']=mode
    if mode=='production' and not values['PUBLIC_BASE_URL'].startswith('https://'):raise ValueError('production_https_required')
    values['BYPAY_BROWSER_ORIGINS']=','.join(browser_origins(local_hosts(),{port})) if values['PUBLIC_BASE_URL'].startswith('http://') else ''
    return values

def prepare():
    from .management import prepare as initialize
    initialize()

def check_ports():
    import socket
    port=int(os.environ.get('BYPAY_FRONTEND_PORT','888'))
    with socket.socket() as sock:
        try:sock.bind(('127.0.0.1',port))
        except OSError:raise ValueError('port_in_use') from None

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','check-ports'])
    args=parser.parse_args()
    prepare() if args.command=='prepare' else check_ports()

if __name__=='__main__':main()
