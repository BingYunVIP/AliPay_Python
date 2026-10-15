"""「6 环境自检」菜单：检查 SDK、配置与网络连通性。"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

from .. import constants, ui
from ..alipay_client import self_check
from ..config_store import ConfigError, ConfigStore


class SelfCheckMenu:
    def __init__(self, cfg_store: ConfigStore):
        self.cfg_store = cfg_store

    def run(self) -> None:
        ui.clear()
        ui.title("6 · 环境自检")
        print()

        passed = 0
        failed = 0

        # 1. 配置
        ui.section("配置检查")
        try:
            cfg = self.cfg_store.load()
        except ConfigError as exc:
            ui.error(str(exc))
            cfg = {}

        if not self.cfg_store.exists():
            ui.warn("尚未创建配置文件（可继续，但无法发起支付）")

        for ok_flag, desc in self_check(cfg):
            if ok_flag:
                ui.ok(desc)
                passed += 1
            else:
                ui.error(desc)
                failed += 1

        # 1.5 密钥配对（最易出错，单独提示）
        ui.section("密钥配对检查")
        from ..key_utils import check_key_pair

        status, detail = check_key_pair(
            cfg.get("app_private_key", ""), cfg.get("alipay_public_key", "")
        )
        if status == "ok":
            ui.ok(detail)
            passed += 1
        elif status == "swapped":
            ui.error("检测到密钥填反：")
            ui.hint(detail)
            failed += 1
        elif status == "error":
            ui.error(detail)
            failed += 1
        else:
            ui.warn(detail)

        # 2. 网络连通性
        ui.section("网络连通性")
        gateway = cfg.get("gateway") or constants.GATEWAY_BY_ENV.get(
            cfg.get("env", "sandbox"), constants.GATEWAY_SANDBOX
        )
        host = urlparse(gateway).hostname or ""
        if host:
            if self._check_host(host):
                ui.ok(f"可连通支付宝网关：{host}")
                passed += 1
            else:
                ui.error(f"无法连通支付宝网关：{host}（请检查网络/代理）")
                failed += 1

        # 3. 本地文件
        ui.section("本地文件")
        for label, path in [
            ("配置文件", constants.CONFIG_FILE),
            ("主密钥", constants.SECRET_KEY_FILE),
            ("订单记录", constants.ORDERS_FILE),
        ]:
            if path.exists():
                ui.ok(f"{label}：{path}")
                passed += 1
            else:
                ui.info(f"{label}：{ui.gray('尚未生成')}")

        # 4. 加密方式
        ui.section("安全")
        from ..crypto import SecretBox

        ui.info(f"加密方式：{SecretBox.describe_mode()}")

        print()
        print(ui.bold(f"  检查完成：通过 {passed} 项，失败 {failed} 项"))
        if failed:
            ui.warn("存在失败项，请按上面的提示处理")
        ui.pause()

    @staticmethod
    def _check_host(host: str, port: int = 443, timeout: float = 5.0) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False
