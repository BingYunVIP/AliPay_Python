"""Run the demo while this foreground process is alive; no background service."""
import os
import signal
import socket
import subprocess
import sys
import time
import ctypes
import secrets
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parent

def main():
    os.umask(0o077)
    values={k:v for k,v in dotenv_values(ROOT/'.env').items() if v is not None}
    port=int(os.environ.get('DEMO_PORT',values.get('DEMO_PORT','8100')))
    host=os.environ.get('DEMO_BIND_HOST',values.get('DEMO_BIND_HOST','0.0.0.0'))
    with socket.socket() as check:
        check.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try: check.bind((host,port))
        except OSError: raise SystemExit(f'端口 {port} 已占用，请先停止旧测试站进程')
    log=Path(values.get('DATABASE',str(ROOT/'data/demo.sqlite3'))).parent/'logs/demo.jsonl'
    address=host
    if host=='0.0.0.0':
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                sock.connect(('8.8.8.8',80));address=sock.getsockname()[0]
        except OSError: address='127.0.0.1'
    print(f'\n冰云支付 · 独立回调测试站\n访问地址：http://{address}:{port}\n本机地址：http://127.0.0.1:{port}\n回调地址：{values.get("DEMO_ORIGIN","http://127.0.0.1:8100").rstrip("/")}/notify\n日志文件：{log}\n商户接口可在网页修改，保存后立即生效\n按 Ctrl+C 停止测试站\n',flush=True)
    setup_token=secrets.token_urlsafe(32)
    link=values.get('DEMO_ORIGIN',f'http://127.0.0.1:{port}').rstrip('/')+'/#setup='+setup_token
    target=Path(values.get('DATABASE',str(ROOT/'data/demo.sqlite3'))).parent
    target.mkdir(parents=True,exist_ok=True,mode=0o700)
    (target/'.setup-link').write_text(link+'\n');(target/'.setup-link').chmod(0o600)
    print('网页配置链接：'+link,flush=True)
    env={**os.environ,'DEMO_SETUP_TOKEN':setup_token,'DEMO_PORT':str(port),'PYTHONUNBUFFERED':'1'}
    parent=os.getpid()
    def parent_guard():
        ctypes.CDLL(None).prctl(1,signal.SIGTERM)
        if os.getppid()!=parent: os.kill(os.getpid(),signal.SIGTERM)
    child=subprocess.Popen([str(ROOT/'.venv/bin/gunicorn'),'--bind',f'{host}:{port}','--workers','1','--threads','4','--umask','0077','--no-control-socket','--capture-output','--error-logfile','-','--log-level','info','wsgi:app'],cwd=ROOT,env=env,start_new_session=True,preexec_fn=parent_guard)
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
        if child.poll() is None:
            try: os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError: pass
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGHUP,stop)
    try:
        while not stopping and child.poll() is None: time.sleep(.2)
        code=0 if stopping else child.returncode
    finally:
        if child.poll() is None: os.killpg(child.pid,signal.SIGTERM)
        try: child.wait(timeout=20)
        except subprocess.TimeoutExpired: os.killpg(child.pid,signal.SIGKILL);child.wait()
    print('测试站已停止',flush=True)
    return code

if __name__=='__main__': raise SystemExit(main())
