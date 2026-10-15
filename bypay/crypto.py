"""本地加密：用 Fernet（AES-128-CBC + HMAC）保护配置文件。

设计说明（很重要，请知悉其安全边界）：
  - 首次运行会在 data/secret.key 生成一把随机主密钥，权限设为 0600（仅当前用户可读）
  - 配置以 Fernet 加密后写入 data/config.enc
  - 因此：**同一台机器、同一个用户，无需每次重新填写配置**

安全边界（务必了解）：
  - 这能防止「配置文件被随手拷走 / 被其他用户读取 / 被误提交到 git」导致的明文泄露
  - 但**不能**防止「已经拿到你本机文件系统权限的人」——因为密钥就在同一台机器上
  - 若要更强保护，可设置环境变量 BYPAY_MASTER_PASSPHRASE，
    此时主密钥由口令派生（PBKDF2），启动时需要输入口令
"""

import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from . import constants


class SecretBox:
    """负责主密钥的获取与加解密。"""

    def __init__(self, key_path: Path = None):
        self.key_path = Path(key_path or constants.SECRET_KEY_FILE)

    # ---------------------------------------------------------- 主密钥
    def _derive_from_passphrase(self, passphrase: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=200_000,
        )
        return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))

    def _load_or_create_key(self) -> bytes:
        passphrase = os.environ.get("BYPAY_MASTER_PASSPHRASE")

        if passphrase:
            # 口令模式：盐值固定落盘，密钥每次由口令派生
            salt_path = self.key_path.with_suffix(".salt")
            if salt_path.exists():
                salt = salt_path.read_bytes()
            else:
                salt = os.urandom(16)
                salt_path.parent.mkdir(parents=True, exist_ok=True)
                salt_path.write_bytes(salt)
                os.chmod(salt_path, 0o600)
            return self._derive_from_passphrase(passphrase, salt)

        # 默认模式：本机随机主密钥
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()

        key = Fernet.generate_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    # ---------------------------------------------------------- 加解密
    def encrypt(self, plaintext: bytes) -> bytes:
        return Fernet(self._load_or_create_key()).encrypt(plaintext)

    def decrypt(self, token: bytes) -> bytes:
        return Fernet(self._load_or_create_key()).decrypt(token)

    # ---------------------------------------------------------- 工具
    def reset(self) -> None:
        """删除主密钥（下次会重新生成；已加密的配置将无法再解开）。"""
        for path in (self.key_path, self.key_path.with_suffix(".salt")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def describe_mode() -> str:
        if os.environ.get("BYPAY_MASTER_PASSPHRASE"):
            return "口令模式（由 BYPAY_MASTER_PASSPHRASE 派生主密钥）"
        return "本机密钥模式（data/secret.key，权限 0600）"


__all__ = ["SecretBox", "InvalidToken"]
