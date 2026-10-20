"""Implementation behind ByPay.sh; credentials never enter command-line arguments."""
import argparse
import getpass
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from dotenv import dotenv_values, set_key
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
SERVICE = "bypay.service"


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def manifest_path(): return ROOT / "data/launcher.json"


def manifest():
    path = manifest_path()
    if path.exists(): return json.loads(path.read_text())
    source = ROOT / ".env"
    return {"env_file": str(source), "port": int(os.environ.get("BYPAY_PORT", "888")), "host": "127.0.0.1"}


def configuration():
    state = manifest()
    path = Path(state["env_file"])
    values = {k: v for k, v in dotenv_values(path).items() if v is not None} if path.exists() else {}
    default_data = ROOT / "data"
    values.setdefault("DATABASE", str(default_data / "web.sqlite3"))
    values.setdefault("BYPAY_LOG_FILE", str(default_data / "logs/payment.jsonl"))
    values.setdefault("PUBLIC_BASE_URL", f"http://localhost:{state['port']}")
    return state, path, values


def host_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))  # Route lookup only; no datagram is sent
            return sock.getsockname()[0]
    except OSError: return "localhost"


def known_credentials(values,row):
    return {'account':'admin@pay.com','password':'Admin123'} if row and row['email']=='admin@pay.com' else None

def admin_row(values):
    if not Path(values["DATABASE"]).is_file(): return None
    with closing(sqlite3.connect(values["DATABASE"])) as db:
        db.row_factory = sqlite3.Row
        try: row = db.execute("SELECT id,email,password FROM users WHERE role='admin'").fetchone()
        except sqlite3.OperationalError: return None
    return dict(row) if row else None


def prepare():
    os.umask(0o077)
    state, source, values = configuration()
    if not 1 <= state["port"] <= 65535: raise ValueError("网页端口必须在 1 到 65535 之间")
    if not source.exists():
        source = ROOT / ".env"
        address = "127.0.0.1"
        values.update(PUBLIC_BASE_URL=f"http://{address}:{state['port']}",
                      DATABASE=str(ROOT / "data/web.sqlite3"), BYPAY_LOG_FILE=str(ROOT / "data/logs/payment.jsonl"),
                      ALLOW_REGISTRATION="false", TEST_MAX_AMOUNT="3000.00", BYPAY_CALLBACK_HOSTS="")
        atomic_text(source, "")
        for key, value in values.items(): set_key(source, key, value)
    values['PUBLIC_BASE_URL']=values.get('PUBLIC_BASE_URL','http://127.0.0.1:888')
    values['BYPAY_ALLOW_HTTP']='1' if values['PUBLIC_BASE_URL'].startswith('http://') else '0'
    for key in ('PUBLIC_BASE_URL','BYPAY_ALLOW_HTTP'):set_key(source,key,values[key])
    source.chmod(0o600)
    from .app import create_app
    create_app({**values,'BYPAY_RUN_MODE':'managed','BYPAY_BROWSER_ORIGINS':direct_origins(values['PUBLIC_BASE_URL'],state['port'])})
    state.update(env_file=str(source), host=values.get("BYPAY_BIND_HOST", "127.0.0.1"), port=state["port"], prepared_at=int(time.time()))
    atomic_text(manifest_path(), json.dumps(state, ensure_ascii=False) + "\n")
    print("配置与管理员账户已就绪")


def direct_origins(origin, port):
    if not origin.startswith("http://"): return ""
    from .runtime import browser_origins, local_hosts
    return ",".join(sorted(set(browser_origins(local_hosts(), {port})) | {origin}))


def systemd_running(): return Path("/run/systemd/system").exists()


def unit_workdir(unit):
    if not systemd_running(): return ""
    try:
        return subprocess.run(["systemctl", "show", unit, "--property=WorkingDirectory", "--value"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired): return ""


def unit_text():
    existing = unit_workdir(SERVICE)
    if existing and Path(existing).resolve() != ROOT.resolve(): raise ValueError("已有 bypay 服务使用其他目录，未覆盖该服务")
    if any(c in str(ROOT) for c in ("\n", "\r", "\\")): raise ValueError("项目路径包含系统服务不支持的字符")
    def quote(path): return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
    return f'''[Unit]
Description=BingYun Pay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={os.getuid()}
WorkingDirectory={str(ROOT).replace('%','%%')}
ExecStart={quote(ROOT / '.venv/bin/python')} -m web.service
Restart=on-failure
RestartSec=3
KillMode=control-group
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
'''


def project_processes():
    result = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit() or int(path.name) == os.getpid(): continue
        try:
            if (path / "cwd").resolve() != ROOT.resolve(): continue
            args = (path / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
            server = "web.wsgi:app" in args or any(args[i:i+2] == ["-m", name] for name in ("web.worker", "web.service") for i in range(len(args)-1))
            vite = any(arg.endswith("/vite") for arg in args) and "frontend" in args
            if server or vite: result.append(int(path.name))
        except (OSError, UnicodeError): pass
    return result


def stop():
    foreign = unit_workdir(SERVICE)
    if foreign and Path(foreign).resolve() != ROOT.resolve(): raise ValueError("已有 bypay 服务使用其他目录，未停止或覆盖")
    prefix = [] if os.getuid() == 0 else ["sudo"]
    for name in (SERVICE, "bypay-web.service", "bypay-callbacks.service"):
        directory = unit_workdir(name)
        if directory and Path(directory).resolve() == ROOT.resolve():
            subprocess.run([*prefix, "systemctl", "disable", "--now", name], check=True, timeout=40)
    processes = project_processes()
    for pid in processes:
        try: os.kill(pid, signal.SIGTERM)
        except ProcessLookupError: pass
    deadline = time.monotonic() + 20
    while project_processes() and time.monotonic() < deadline: time.sleep(.1)
    if project_processes(): raise ValueError("旧进程尚未退出，请稍后重试部署")


def health(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response: return response.status == 200
    except Exception: return False


def wait_ready():
    state, _, values = configuration()
    port = state["port"]
    marker = Path(values["DATABASE"]).parent / "service.json"
    for _ in range(30):
        try:
            running = json.loads(marker.read_text())
            owned = running.get("port") == port and running.get("pid") in project_processes()
            if owned and health(port): return
        except (OSError, ValueError):
            pass
        time.sleep(.5)
    raise ValueError("网页未能正常启动，请在菜单 4 查看日志")


def status(show_credentials=False):
    state, source, values = configuration()
    processes = project_processes()
    print("部署状态：" + ("已部署" if manifest_path().exists() else "待初始化" if Path(values["DATABASE"]).exists() else "尚未部署"))
    active = "未创建"
    if unit_workdir(SERVICE) == str(ROOT):
        active = subprocess.run(["systemctl", "is-active", SERVICE], capture_output=True, text=True).stdout.strip()
    print("系统服务：bypay · " + {"active":"运行中", "inactive":"已停止", "failed":"启动失败"}.get(active, active))
    print("网页状态：" + ("运行中" if health(state["port"]) else "未运行"))
    print("工作进程：" + ("已启动" if processes else "未启动"))
    print("网页地址：" + values["PUBLIC_BASE_URL"].rstrip("/") + "/login")
    print(f"本机地址：http://127.0.0.1:{state['port']}/login")
    if show_credentials:
        row = admin_row(values)
        if row:
            saved = known_credentials(values, row)
            print("管理账号：" + row["email"])
            print("管理密码：" + (saved["password"] if saved else "已有密码无法读取明文，可在菜单 2 修改"))


def change_credentials():
    _, source, values = configuration()
    row = admin_row(values)
    if not row: raise ValueError("尚未部署，请先选择 1")
    account = input(f"新账号 [{row['email']}]：").strip().lower() or row["email"]
    password = getpass.getpass("新密码（至少12位，大小写/数字/符号至少三类，无空白）：")
    from .security import validate_admin_identity
    validate_admin_identity(account,password)
    if password and password != getpass.getpass("再次输入新密码："): raise ValueError("两次密码不一致，未修改")
    try:
        with closing(sqlite3.connect(values["DATABASE"], timeout=15)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE users SET email=?,password=?,must_change_credentials=0 WHERE id=? AND role='admin'", (account, generate_password_hash(password) if password else row["password"], row["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    except sqlite3.IntegrityError: raise ValueError("该账号已被其他用户使用，未修改") from None
    # Account identity is read from the database
    # Passwords are stored only as hashes in the database
    source.chmod(0o600)
    print("账号信息已更新，请重新登录网页")


UPDATE_BACKUPS_KEPT = 3


def update_ready():
    """A code update restarts the system service made by this project, so it needs a deployment that has one."""
    if not manifest_path().exists(): raise ValueError("尚未部署，请先选择 1")
    if not systemd_running(): raise ValueError("代码更新需要 systemd 系统服务，当前环境未运行 systemd")
    if unit_workdir(SERVICE) != str(ROOT): raise ValueError("尚未创建 bypay 系统服务，请先选择 1 完成部署")


def backup():
    """Snapshot the data before an update: a consistent copy of the database, the keys and the settings file. Only the newest few are kept."""
    os.umask(0o077)
    state, source, values = configuration()
    database = Path(values["DATABASE"])
    if not database.is_file():
        print("尚无数据库，不需要备份"); return
    data = database.parent
    folder = data / "backups" / ("update-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as live, closing(sqlite3.connect(folder / "web.sqlite3")) as copy:
        live.backup(copy)  # online and consistent, whatever the service is doing meanwhile
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("备份文件校验未通过，原数据未受影响")
        try: counts = [copy.execute(sql).fetchone()[0] for sql in ("SELECT COUNT(*) FROM users", "SELECT COUNT(*) FROM orders", "SELECT COUNT(*) FROM orders WHERE paid_at IS NOT NULL")]
        except sqlite3.OperationalError: counts = None
    for item in sorted(data.iterdir()):
        if item.is_file() and not item.is_symlink() and item.suffix in {".key", ".pem"}: shutil.copy2(item, folder / item.name)
    if source.is_file(): shutil.copy2(source, folder / source.name.lstrip("."))
    if manifest_path().is_file(): shutil.copy2(manifest_path(), folder / "launcher.json")
    for item in folder.iterdir(): item.chmod(0o600)
    # older update backups go; folders made by hand under backups/ are never touched
    made = sorted(item for item in (data / "backups").iterdir() if item.is_dir() and re.fullmatch(r"update-\d{8}T\d{6}Z", item.name))
    for old in made[:-UPDATE_BACKUPS_KEPT]: shutil.rmtree(old)
    print(f"数据已备份到：{folder}")
    if counts: print(f"备份内容：{counts[0]} 个用户、{counts[1]} 笔订单（其中 {counts[2]} 笔已支付），以及密钥和配置文件")


def logs():
    _, _, values = configuration()
    paths = [Path(values["DATABASE"]).parent / "logs/server.log", Path(values["BYPAY_LOG_FILE"])]
    has_files = any(path.is_file() for path in paths)
    journal = unit_workdir(SERVICE) == str(ROOT)
    if journal: subprocess.run(["journalctl", "-u", SERVICE, "-n", "30", "--no-pager"], check=False)
    if not has_files and not journal: print("暂无日志，请先部署并运行"); return
    print("正在显示最新日志，按 Ctrl+C 返回主菜单")
    child = subprocess.Popen(["tail", "-n", "60", "-F", *map(str,paths)] if has_files else ["journalctl", "-u", SERVICE, "-f", "--no-pager"])
    try: child.wait()
    except KeyboardInterrupt:
        child.terminate(); child.wait()


def foreground():
    child = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "web.service"], cwd=ROOT)
    try:
        wait_ready()
        status(show_credentials=True)
        print("前台运行中，按 Ctrl+C 停止服务并返回主菜单", flush=True)
        if child.wait(): raise ValueError("运行失败，请查看菜单 4 的日志")
    except KeyboardInterrupt:
        child.terminate(); child.wait(timeout=25)
        print("服务已停止")
    finally:
        if child.poll() is None: child.terminate(); child.wait(timeout=25)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare","unit","status","credentials","logs","foreground","stop","wait","backup","ready"))
    parser.add_argument("--credentials", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "unit": print(unit_text(), end="")
        elif args.action == "status": status(args.credentials)
        else: {"prepare":prepare,"credentials":change_credentials,"logs":logs,"foreground":foreground,"stop":stop,"wait":wait_ready,"backup":backup,"ready":update_ready}[args.action]()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print("操作未完成：" + str(exc), file=sys.stderr); raise SystemExit(1)
    except (KeyboardInterrupt, EOFError) as exc:
        print("\n操作已取消"); raise SystemExit(130 if isinstance(exc, KeyboardInterrupt) else 1)  # a cancelled step is never reported as done


if __name__ == "__main__": main()
