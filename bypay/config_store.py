"""配置读写：加密落盘。

对外只暴露 load / save / reset / 校验 / 脱敏展示。
敏感字段（应用私钥、支付宝公钥）在整份配置被 Fernet 加密后才写入磁盘。
"""

import json
import os
from pathlib import Path

from . import constants, logger, ui
from .crypto import SecretBox


class ConfigError(RuntimeError):
    """配置相关错误。"""


class ConfigStore:
    def __init__(self, path: Path = None):
        self.path = Path(path or constants.CONFIG_FILE)
        self.box = SecretBox()

    # ---------------------------------------------------------- 基础
    def exists(self) -> bool:
        return self.path.exists()

    def _defaults(self) -> dict:
        return {f["key"]: f.get("default", "") for f in constants.CONFIG_FIELDS}

    def load(self) -> dict:
        cfg = self._defaults()
        if not self.path.exists():
            return cfg
        try:
            raw = self.box.decrypt(self.path.read_bytes())
            cfg.update(json.loads(raw.decode("utf-8")))
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(
                "配置文件无法解密（可能主密钥已变更或文件损坏）。\n"
                f"  文件：{self.path}\n"
                f"  原因：{exc}\n"
                "  处理：进入「1 配置 → 5 重置配置」后重新填写。"
            ) from exc
        return cfg

    def save(self, cfg: dict) -> None:
        payload = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
        token = self.box.encrypt(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(token)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        logger.info("config", "配置已加密保存", 文件=self.path.name, 字段数=len(cfg))

    def reset(self) -> None:
        try:
            self.path.unlink()
            logger.warn("config", "配置已被重置")
        except FileNotFoundError:
            pass

    # ---------------------------------------------------------- 校验
    def missing_required(self, cfg: dict) -> list:
        return [k for k in constants.REQUIRED_KEYS if not cfg.get(k)]

    def is_ready(self, cfg: dict) -> bool:
        return not self.missing_required(cfg)

    def summary_lines(self, cfg: dict) -> list:
        """用于展示的 (键, 值) 列表，敏感字段脱敏。"""
        rows = []
        for field in constants.CONFIG_FIELDS:
            key = field["key"]
            value = cfg.get(key, "")
            if field.get("secret"):
                shown = ui.mask(value) if value else ui.gray("(未设置)")
            else:
                shown = value if value else ui.gray("(未设置)")
            rows.append((field["label"], shown))
        return rows

    def env_gateway(self, cfg: dict) -> str:
        """按运行环境返回网关地址（用户未手填时自动兜底）。"""
        if cfg.get("gateway"):
            return cfg["gateway"]
        return constants.GATEWAY_BY_ENV.get(cfg.get("env", "sandbox"), constants.GATEWAY_SANDBOX)
