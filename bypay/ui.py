"""终端界面辅助：颜色、输入、表格、提示。

无第三方依赖，仅用标准库。若终端不支持 ANSI 颜色会自动降级为纯文本。
"""

import os
import sys

# ---------------------------------------------------------------- 颜色
_USE_COLOR = (
    sys.stdout.isatty()
    and os.environ.get("TERM", "") not in ("", "dumb")
    and os.environ.get("NO_COLOR") is None
)


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def red(t: str) -> str:
    return _c("31", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def blue(t: str) -> str:
    return _c("34", t)


def cyan(t: str) -> str:
    return _c("36", t)


def gray(t: str) -> str:
    return _c("90", t)


def bold(t: str) -> str:
    return _c("1", t)


# ---------------------------------------------------------------- 输出
def clear() -> None:
    os.system("clear" if os.name != "nt" else "cls")


def title(text: str, width: int = 62) -> None:
    print()
    print(cyan("═" * width))
    print(cyan("  " + bold(text)))
    print(cyan("═" * width))


def section(text: str) -> None:
    print()
    print(bold("── " + text + " " + "─" * max(0, 56 - len(text))))


def ok(text: str) -> None:
    print(green("  [√] ") + text)


def warn(text: str) -> None:
    print(yellow("  [!] ") + text)


def error(text: str) -> None:
    print(red("  [×] ") + text)


def info(text: str) -> None:
    print("  " + text)


def hint(text: str) -> None:
    for line in text.splitlines():
        print(gray("      " + line))


# ---------------------------------------------------------------- 输入
def ask(prompt: str, default: str = "", allow_empty: bool = False) -> str:
    """普通输入。default 非空时，直接回车即取默认值。"""
    suffix = f" {gray('[' + default + ']')}" if default else ""
    while True:
        raw = input(f"  {prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default:
            return default
        if allow_empty:
            return ""
        error("此项不能为空，请重新输入")


def _secret_is_complete(text: str) -> bool:
    """判断敏感输入是否可以结束（用于支持多行 PEM 粘贴）。"""
    s = text.strip()
    if not s:
        return True                     # 空行 → 结束（交给上层处理「保留原值」）
    if "-----BEGIN" in s:
        return "-----END" in s          # PEM：必须等到 END 标记才结束
    return True                         # 裸 Base64 / 单行 → 回车即结束


def _read_secret_masked(prompt: str) -> str:
    """逐字符回显 `*` 的敏感输入，支持粘贴多行 PEM 私钥。

    - 输入过程中每个字符显示一个 `*`，能直观看到自己确实输入了
    - 支持退格删除
    - 粘贴多行 PEM 时会自动等到 -----END----- 才结束，避免被截断
    """
    try:
        import termios
        import tty
    except ImportError:                 # 非 POSIX 平台
        import getpass

        return getpass.getpass(prompt)

    if not sys.stdin.isatty():          # 管道 / 重定向输入
        return sys.stdin.readline().strip()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    buf: list = []
    try:
        tty.setraw(fd)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            ch = sys.stdin.read(1)
            if ch == "":
                break
            if ch == "\x03":            # Ctrl+C
                raise KeyboardInterrupt
            if ch == "\x04":            # Ctrl+D
                break
            if ch in ("\r", "\n"):
                text = "".join(buf)
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                if _secret_is_complete(text):
                    return text
                # 多行 PEM 尚未结束 → 换行继续接收
                buf.append("\n")
                sys.stdout.write("      ")
                sys.stdout.flush()
                continue
            if ch in ("\x7f", "\b"):    # 退格
                if buf:
                    buf.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            buf.append(ch)
            sys.stdout.write("*")
            sys.stdout.flush()
            # 粘贴内容没有结尾换行时，也能在 END 标记处自动结束
            joined = "".join(buf)
            if (
                "-----BEGIN" in joined
                and "-----END" in joined
                and joined.rstrip().endswith("-----")
            ):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return joined
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return "".join(buf)


def ask_secret(prompt: str, default_set: bool = False) -> str:
    """敏感输入：逐字符回显 `*`，不回显真实内容。

    - 已存在配置时，直接回车 = 保留原值
    - 支持粘贴多行 PEM 私钥（自动识别 -----END-----）
    """
    tip = "（直接回车 = 保留原值）" if default_set else ""
    while True:
        raw = _read_secret_masked(f"  {prompt}{tip}: ").strip()
        if raw:
            return raw
        if default_set:
            return ""
        error("此项不能为空，请重新输入")


def ask_int(prompt: str, default: int) -> int:
    while True:
        raw = ask(prompt, str(default))
        try:
            value = int(raw)
        except ValueError:
            error("请输入整数")
            continue
        if value <= 0:
            error("必须为正整数")
            continue
        return value


def ask_choice(prompt: str, choices: list, default: str = "") -> str:
    """从给定选项中选一个。"""
    text = "/".join(choices)
    while True:
        raw = ask(f"{prompt} ({text})", default)
        if raw in choices:
            return raw
        error(f"只能填：{text}")


def confirm(prompt: str, default: bool = False) -> bool:
    d = "y/N" if not default else "Y/n"
    raw = input(f"  {prompt} [{d}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "是")


def pause() -> None:
    input(gray("\n  按回车返回…"))


def kv_table(rows: list, key_width: int = 24) -> None:
    """简单的两列表格输出。rows = [(k, v), ...]"""
    for k, v in rows:
        print(f"  {k:<{key_width}} {v}")


def mask(value: str, keep_head: int = 6, keep_tail: int = 4) -> str:
    """敏感信息脱敏。"""
    if not value:
        return gray("(未设置)")
    value = str(value)
    if len(value) <= keep_head + keep_tail:
        return "*" * len(value)
    return f"{value[:keep_head]}{'*' * 8}{value[-keep_tail:]}"
