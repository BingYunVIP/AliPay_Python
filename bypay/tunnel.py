"""cloudflared 快速隧道：把本地回调服务映射成公网 https 地址。

背景（为什么需要它）：
    支付宝的**异步通知（notify_url）**是支付宝服务器主动 POST 到你的地址，
    因此该地址**必须公网可达**。本地开发（WSL / 家用宽带 / 无公网 IP）通常做不到。

    本模块用 Cloudflare 的**免费快速隧道（Quick Tunnel）**解决：
        本地 http://127.0.0.1:8899  →  https://xxxx-xxxx.trycloudflare.com
    无需注册账号、无需买域名、无需开放路由器端口。

用法（由「5 回调服务」菜单调用，一般不需要手动跑）：
    t = QuickTunnel()
    ok, url = t.start(8899)     # 启动并拿到公网地址
    t.stop()

注意：
    - 快速隧道是 Cloudflare 提供的免费试用通道，**不保证可用性**，
      地址每次启动都会变，适合自测，不建议用于生产。
    - 生产建议：自有域名 + 命名隧道（cloudflared named tunnel）或反向代理。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import constants, logger

# ---------------------------------------------------------------- 常量
BINARY_NAME = "cloudflared"
INSTALL_PATH = "/usr/local/bin/cloudflared"
DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-amd64"
)

TUNNEL_LOG = constants.DATA_DIR / "tunnel.log"
TUNNEL_STATE = constants.DATA_DIR / "tunnel.json"

_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# 这些环境变量会让 cloudflared 走本机代理；快速隧道需要直连 Cloudflare 边缘
_PROXY_VARS = (
    "http_proxy", "https_proxy", "all_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
)


# ---------------------------------------------------------------- 二进制
def find_binary() -> str | None:
    """查找 cloudflared 可执行文件。"""
    found = shutil.which(BINARY_NAME)
    if found:
        return found
    if Path(INSTALL_PATH).exists():
        return INSTALL_PATH
    return None


def version() -> str | None:
    """返回版本号；未安装返回 None。"""
    binary = find_binary()
    if not binary:
        return None
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10
        )
        text = (out.stdout or out.stderr).strip()
        return text.splitlines()[0] if text else "unknown"
    except (OSError, subprocess.SubprocessError):
        return None


def install_hint() -> str:
    """给出安装指引（供菜单展示）。"""
    return (
        "未检测到 cloudflared（用于把本地回调地址映射到公网）。\n"
        "  安装命令（本工具已内置一键安装，见菜单「安装公网穿透工具」）：\n"
        f"    curl -L -o /tmp/cloudflared {DOWNLOAD_URL}\n"
        "    install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared\n"
        "  说明：Cloudflare 快速隧道免费、免注册，但地址每次启动都会变化。"
    )


def install() -> tuple:
    """下载并安装 cloudflared。返回 (是否成功, 说明)。"""
    if find_binary():
        return True, f"已安装：{version()}"

    try:
        import urllib.request

        logger.info("app", "开始下载 cloudflared", 地址=DOWNLOAD_URL)
        tmp = Path("/tmp/cloudflared_download")
        # 下载需要走代理（与 cloudflared 运行时相反）
        urllib.request.urlretrieve(DOWNLOAD_URL, tmp)
        if not tmp.exists() or tmp.stat().st_size < 5_000_000:
            return False, "下载文件异常（体积过小），请检查网络后重试"

        target = Path(INSTALL_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), str(target))
        os.chmod(target, 0o755)
        logger.info("app", "cloudflared 安装完成", 路径=str(target), 版本=version())
        return True, f"安装成功：{version()}"
    except Exception as exc:  # noqa: BLE001
        logger.error("error", "cloudflared 安装失败", exc=exc)
        return False, f"安装失败：{exc}\n  可手动执行：\n{install_hint()}"


# ---------------------------------------------------------------- 隧道状态
def load_state() -> dict:
    if TUNNEL_STATE.exists():
        try:
            data = json.loads(TUNNEL_STATE.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(**fields) -> None:
    data = load_state()
    data.update(fields)
    TUNNEL_STATE.parent.mkdir(parents=True, exist_ok=True)
    TUNNEL_STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def _clear_state() -> None:
    try:
        TUNNEL_STATE.unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:  # noqa: BLE001
        return False
    # 排除僵尸进程
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fp:
            state = fp.read().split(") ", 1)[-1].split()[0]
        return state != "Z"
    except OSError:
        return False


def _read_url_from_log() -> str:
    if not TUNNEL_LOG.exists():
        return ""
    try:
        text = TUNNEL_LOG.read_text("utf-8", errors="replace")
    except OSError:
        return ""
    matches = _URL_RE.findall(text)
    return matches[-1] if matches else ""


# ---------------------------------------------------------------- 隧道控制
class QuickTunnel:
    """Cloudflare 快速隧道的启停与状态查询。"""

    def __init__(self):
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------ 属性
    @property
    def pid(self) -> int:
        state = load_state()
        pid = int(state.get("pid") or 0)
        if pid and _pid_alive(pid):
            return pid
        return 0

    @property
    def url(self) -> str:
        """返回当前公网地址（优先内存 / 状态文件 / 日志）。"""
        if self._proc and self._proc.poll() is None:
            url = self._read_running_url()
            if url:
                return url
        state = load_state()
        url = state.get("url") or ""
        if url:
            return url
        return _read_url_from_log() if _pid_alive(int(load_state().get("pid") or 0)) else ""

    @property
    def port(self) -> int:
        return int(load_state().get("port") or 0)

    @property
    def is_running(self) -> bool:
        return bool(self.pid)

    def _read_running_url(self) -> str:
        """从当前进程日志尾部找地址。"""
        url = _read_url_from_log()
        if url:
            _save_state(url=url)
        return url

    # ------------------------------------------------ 启动
    def start(self, port: int, wait_seconds: int = 45) -> tuple:
        """启动隧道并等待公网地址。返回 (是否成功, 地址或错误说明)。"""
        binary = find_binary()
        if not binary:
            return False, install_hint()

        if self.is_running:
            url = self.url
            if url:
                return True, url

        # 重新开始：清空日志，便于解析最新地址
        TUNNEL_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            TUNNEL_LOG.write_text("", "utf-8")
        except OSError:
            pass

        # 第一轮：直连（清除代理变量）
        ok, url, msg = self._spawn_and_wait(binary, port, wait_seconds, use_proxy=False)
        if ok:
            return True, url

        # 第二轮：回退使用系统代理
        logger.warn("app", "快速隧道直连失败，改为经代理重试")
        ok, url, msg = self._spawn_and_wait(binary, port, wait_seconds, use_proxy=True)
        if ok:
            return True, url

        return False, (
            f"无法建立公网地址（已重试直连与代理两种方式）。\n"
            f"  最后错误：{msg}\n"
            f"  可查看日志：{TUNNEL_LOG}"
        )

    def _spawn_and_wait(
        self, binary: str, port: int, wait_seconds: int, use_proxy: bool
    ) -> tuple:
        env = dict(os.environ)
        if not use_proxy:
            for key in _PROXY_VARS:
                env.pop(key, None)

        cmd = [
            binary, "tunnel",
            "--no-autoupdate",
            "--protocol", "http2",
            "--url", f"http://127.0.0.1:{port}",
        ]

        log_fp = open(TUNNEL_LOG, "a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd, stdout=log_fp, stderr=subprocess.STDOUT, env=env,
                start_new_session=True,
            )
        except OSError as exc:
            log_fp.close()
            return False, "", f"启动 cloudflared 失败：{exc}"

        self._proc = proc
        _save_state(pid=proc.pid, port=port, url="", started_at=time.strftime("%Y-%m-%d %H:%M:%S"))

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if proc.poll() is not None:
                log_fp.close()
                return False, "", f"cloudflared 进程提前退出（退出码 {proc.returncode}）"
            url = _read_url_from_log()
            if url:
                _save_state(url=url)
                log_fp.close()          # 子进程已持有 fd，父进程及时释放
                logger.info("app", "公网隧道已建立", 端口=port, 公网地址=url)
                return True, url, ""
            time.sleep(1)

        log_fp.close()
        # 超时：清理
        self._terminate(proc)
        return False, "", f"等待 {wait_seconds} 秒仍未获取到公网地址"

    # ------------------------------------------------ 停止
    def stop(self) -> bool:
        pid = self.pid
        if not pid:
            _clear_state()
            return False
        try:
            os.kill(pid, 15)  # SIGTERM
            for _ in range(20):
                if not _pid_alive(pid):
                    break
                time.sleep(0.25)
            else:
                os.kill(pid, 9)  # SIGKILL
        except OSError:
            pass
        finally:
            self._proc = None
            _clear_state()
            logger.info("app", "公网隧道已停止")
        return True

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass

    # ------------------------------------------------ 辅助
    def public_urls(self) -> dict:
        """返回公网 notify / return 地址（无隧道时返回本机地址）。"""
        base = self.url
        if base:
            return {"notify": f"{base}/alipay/notify", "return": f"{base}/alipay/return"}
        return {}

    def tail_log(self, lines: int = 20) -> list:
        if not TUNNEL_LOG.exists():
            return []
        try:
            return TUNNEL_LOG.read_text("utf-8", errors="replace").splitlines()[-lines:]
        except OSError:
            return []


def _no_proxy_opener():
    """构造一个**不读取系统代理**的 opener。

    为什么需要：本机可能设置了 http_proxy / https_proxy（开发代理）。
    探测公网地址时若走了开发代理，会被代理拦截或超时，
    从而误判为「公网不可达」——实际链路是通的。
    """
    import urllib.request

    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------- 公共 DNS 回退
# 当系统 DNS（WSL 的 DNS 代理）解析失败时，用这些公共 DNS 兜底
PUBLIC_DNS_SERVERS = [
    ("223.5.5.5", "阿里 DNS"),
    ("119.29.29.29", "腾讯 DNS"),
    ("114.114.114.114", "114 DNS"),
    ("180.76.76.76", "百度 DNS"),
    ("8.8.8.8", "Google DNS"),
    ("1.1.1.1", "Cloudflare DNS"),
]

# 解析缓存：{host: (ip, 说明, 过期时间)}。避免短时间内重复查询，
# 也缓解 UDP 查询偶发丢包导致的间歇性失败。
_RESOLVE_CACHE: dict = {}
_RESOLVE_TTL = 60.0


def dns_query_a(domain: str, server: str, timeout: float = 5.0) -> list:
    """用原始 UDP 包向指定 DNS 服务器查询 A 记录。返回 IP 列表。"""
    import socket as _socket
    import struct as _struct

    tid = 0x4A42
    header = _struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    qname = b""
    for label in domain.split("."):
        qname += bytes([len(label)]) + label.encode("ascii", "ignore")
    qname += b"\x00"
    packet = header + qname + _struct.pack(">HH", 1, 1)  # A 记录, IN

    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(packet, (server, 53))
            data, _ = s.recvfrom(4096)
    except OSError:
        return []

    if len(data) < 12:
        return []
    ancount = _struct.unpack(">H", data[6:8])[0]
    if not ancount:
        return []

    idx = 12
    while idx < len(data) and data[idx] != 0:
        idx += data[idx] + 1
    idx += 5  # 跳过结束符 + qtype + qclass

    ips = []
    for _ in range(ancount):
        if idx >= len(data):
            break
        if data[idx] & 0xC0 == 0xC0:      # 压缩指针
            idx += 2
        else:
            while idx < len(data) and data[idx] != 0:
                idx += data[idx] + 1
            idx += 1
        if idx + 10 > len(data):
            break
        rtype, _, _, rdlen = _struct.unpack(">HHIH", data[idx:idx + 10])
        idx += 10
        if rtype == 1 and rdlen == 4:
            ips.append(".".join(str(b) for b in data[idx:idx + 4]))
        idx += rdlen
    return ips


def resolve_host(host: str, timeout: float = 5.0, use_cache: bool = True) -> tuple:
    """解析域名。先用系统 DNS；失败则回退公共 DNS（含重试与缓存）。

    返回 (ip 或 "", 说明)。

    为什么需要重试：公共 DNS 走 UDP，实测存在偶发丢包，
    同一域名可能一次成功、下一次失败（间歇性）。
    """
    import socket as _socket
    import time as _time

    if use_cache:
        hit = _RESOLVE_CACHE.get(host)
        if hit and hit[2] > _time.time():
            return hit[0], hit[1]

    # 1) 系统 DNS（重试 2 次）
    for _ in range(2):
        try:
            infos = _socket.getaddrinfo(
                host, 443, _socket.AF_INET, _socket.SOCK_STREAM
            )
            if infos:
                ip, how = infos[0][4][0], "系统 DNS"
                _RESOLVE_CACHE[host] = (ip, how, _time.time() + _RESOLVE_TTL)
                return ip, how
        except OSError:
            pass

    # 2) 公共 DNS 兜底（多服务器 × 重试 2 轮）
    for _round in range(2):
        for server, label in PUBLIC_DNS_SERVERS:
            ips = dns_query_a(host, server, timeout)
            if ips:
                ip, how = ips[0], f"公共 DNS（{label}）"
                _RESOLVE_CACHE[host] = (ip, how, _time.time() + _RESOLVE_TTL)
                return ip, how

    return "", "系统 DNS 与公共 DNS 均解析失败"


def _http_via_public_dns(method: str, url: str, body, timeout: int) -> tuple:
    """公共 DNS 解析出 IP 后直连（保留 SNI 与 Host 头，证书校验正常）。"""
    import socket as _socket
    import ssl as _ssl
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        return 0, "URL 缺少主机名"

    ip, how = resolve_host(host, timeout=5)
    if not ip:
        # 再试一次（UDP 丢包导致的间歇性失败）
        _RESOLVE_CACHE.pop(host, None)
        ip, how = resolve_host(host, timeout=6, use_cache=False)
    if not ip:
        return 0, f"DNS 解析失败（{how}）"

    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        raw = _socket.create_connection((ip, port), timeout=timeout)
        if parsed.scheme == "https":
            ctx = _ssl.create_default_context()
            # server_hostname 用域名 → SNI 正确、证书校验通过
            sock = ctx.wrap_socket(raw, server_hostname=host)
        else:
            sock = raw
    except Exception as exc:  # noqa: BLE001
        return 0, f"连接 {ip}:{port} 失败：{exc}"

    try:
        lines = [
            f"{method} {path} HTTP/1.1",
            f"Host: {host}",
            "User-Agent: ByPay-probe/0.1",
            "Connection: close",
        ]
        if body is not None:
            lines.append("Content-Type: application/x-www-form-urlencoded")
            lines.append(f"Content-Length: {len(body)}")
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
        if body:
            request += body
        sock.sendall(request)

        chunks = []
        total = 0
        while total < 16384:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except Exception as exc:  # noqa: BLE001
        return 0, f"读写失败：{exc}"
    finally:
        try:
            sock.close()
        except OSError:
            pass

    raw_resp = b"".join(chunks)
    if not raw_resp:
        return 0, "无响应内容"

    head_part, _, body_part = raw_resp.partition(b"\r\n\r\n")
    status = 0
    try:
        first_line = head_part.split(b"\r\n", 1)[0].decode("latin-1")
        status = int(first_line.split()[1])
    except (IndexError, ValueError):
        pass

    # 注意：不要把「解析方式」拼进响应体，否则会污染业务内容
    # （如支付宝要求返回纯文本 "success"）。解析信息只写日志。
    logger.debug("app", "公网请求经由公共 DNS 回退", 主机=host, IP=ip, 方式=how)
    return status, body_part.decode("utf-8", "replace")


def http_request(method: str, url: str, data: dict = None, timeout: int = 15) -> tuple:
    """发起 HTTP(S) 请求（绕系统代理；系统 DNS 失败时回退公共 DNS + 直连 IP）。

    返回 (status_code, body 文本)；status_code == 0 表示完全失败。
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    if not url:
        return 0, "地址为空"

    body = None
    headers = {"User-Agent": "ByPay-probe/0.1"}
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    # ---- 常规路径：绕代理 opener（自动使用系统 DNS）
    opener = _no_proxy_opener()
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(8192).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(8192).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        pass

    # ---- 回退路径：公共 DNS 解析 + 直连 IP
    return _http_via_public_dns(method, url, body, timeout)


def probe_public(url: str, timeout: int = 15) -> tuple:
    """从「公网视角」访问给定地址，验证隧道是否真的通了。

    返回 (是否成功, 说明)。
    """
    if not url:
        return False, "地址为空"

    status, body = http_request("GET", url, timeout=timeout)
    if status == 0:
        return False, f"访问失败：{body}"
    return True, f"HTTP {status}，返回：{body.strip()[:80]}"
