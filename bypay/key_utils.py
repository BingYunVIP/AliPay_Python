"""密钥格式归一化（避免因格式差异踩坑）。

背景（重要，这是支付宝 Python SDK 最常见的坑）：
    官方 SDK 底层用 `rsa` 库，对格式有硬性要求：
      - 应用私钥：必须是 **PKCS#1** PEM（-----BEGIN RSA PRIVATE KEY-----）
      - 支付宝公钥：必须是 SubjectPublicKeyInfo（-----BEGIN PUBLIC KEY-----）
    但实际来源五花八门：
      - 支付宝「密钥生成工具」可能给出 PKCS#8（-----BEGIN PRIVATE KEY-----）
      - 开放平台复制出来的「支付宝公钥」通常是**裸 Base64**（没有 PEM 头尾）
      - 手抄时可能带上换行、空格
    若格式不匹配，SDK 会抛出难以理解的错误（如 "Unable to read this file, version 136 != 0"）。

本模块统一做归一化，让「PKCS#8 / PKCS#1 / 裸 Base64」三种输入都能正常工作。
"""

from __future__ import annotations

import base64
import re

from cryptography.hazmat.primitives import serialization


def _clean_b64(text: str) -> str:
    """去掉 PEM 头尾、换行与空白，得到纯 Base64。"""
    lines = [ln.strip() for ln in text.strip().splitlines()]
    body = "".join(ln for ln in lines if ln and not ln.startswith("-----"))
    body = re.sub(r"\s+", "", body)
    pad = (-len(body)) % 4
    return body + "=" * pad


def _looks_like_pem(text: str) -> bool:
    return "-----BEGIN" in text


# ---------------------------------------------------------------- 私钥
def normalize_private_key(raw: str) -> str:
    """把任意形式的应用私钥统一为 PKCS#1 PEM。

    支持输入：
      - PKCS#1 PEM（BEGIN RSA PRIVATE KEY）
      - PKCS#8 PEM（BEGIN PRIVATE KEY）
      - 裸 Base64（PKCS#1 或 PKCS#8 DER）
    输出：PKCS#1 PEM
    """
    if not raw or not raw.strip():
        raise ValueError("应用私钥为空")
    text = raw.strip()

    key = None
    if _looks_like_pem(text):
        key = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
    else:
        der = base64.b64decode(_clean_b64(text))
        try:
            key = serialization.load_der_private_key(der, password=None)
        except Exception:  # noqa: BLE001
            # 兜底：按 PKCS#1 处理（cryptography 对部分 PKCS#1 DER 支持有限）
            key = None
        if key is None:
            import rsa as _rsa

            pem = (
                "-----BEGIN RSA PRIVATE KEY-----\n"
                + _clean_b64(text)
                + "\n-----END RSA PRIVATE KEY-----"
            )
            key = _rsa.PrivateKey.load_pkcs1(pem.encode("utf-8"), format="PEM")

    if hasattr(key, "private_bytes"):
        return key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

    # cryptography 加载失败、由 rsa 库加载的情况
    return key.save_pkcs1("PEM").decode("utf-8")


# ---------------------------------------------------------------- 公钥
def normalize_public_key(raw: str) -> str:
    """把支付宝公钥统一为**裸 Base64**（SubjectPublicKeyInfo）。

    支持输入：
      - 裸 Base64（开放平台复制的默认形式）
      - PEM（BEGIN PUBLIC KEY / BEGIN RSA PUBLIC KEY）
    输出：裸 Base64（SDK 会自动补 PEM 头尾）
    """
    if not raw or not raw.strip():
        raise ValueError("支付宝公钥为空")
    text = raw.strip()

    if _looks_like_pem(text):
        key = serialization.load_pem_public_key(text.encode("utf-8"))
    else:
        der = base64.b64decode(_clean_b64(text))
        try:
            key = serialization.load_der_public_key(der)
        except Exception:  # noqa: BLE001
            import rsa as _rsa

            pub = _rsa.PublicKey.load_pkcs1(der, format="DER")
            key = pub

    if hasattr(key, "public_bytes"):
        der_out = key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(der_out).decode("utf-8")

    # rsa 库对象兜底
    return base64.b64encode(key.save_pkcs1("DER")).decode("utf-8")


# ---------------------------------------------------------------- 自检
def describe_key(raw: str, is_private: bool) -> str:
    """返回人类可读的格式描述，用于配置回显。"""
    if not raw:
        return "未设置"
    text = raw.strip()
    if _looks_like_pem(text):
        first = text.splitlines()[0].strip()
        return f"PEM（{first.strip('-')}）"
    return "裸 Base64"


# ---------------------------------------------------------------- 密钥配对检查
def derive_public_key_b64(private_key_raw: str) -> str:
    """由应用私钥推导出对应的应用公钥（裸 Base64）。"""
    pem = normalize_private_key(private_key_raw).encode("utf-8")
    key = serialization.load_pem_private_key(pem, password=None)
    der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("utf-8")


def check_key_pair(private_key_raw: str, alipay_public_key_raw: str) -> tuple:
    """检查「应用私钥」与「支付宝公钥」是否被填反。

    返回 (状态, 说明)：
        empty   有密钥未填写
        swapped 支付宝公钥 == 应用公钥（**最常见的错误**：把应用公钥填到了支付宝公钥位置）
        ok      两者不同，配对正常
        error   密钥无法解析
    """
    if not private_key_raw or not alipay_public_key_raw:
        return "empty", "有密钥未填写"

    try:
        app_pub = derive_public_key_b64(private_key_raw)
        alipay_pub = normalize_public_key(alipay_public_key_raw)
    except Exception as exc:  # noqa: BLE001
        return "error", f"密钥无法解析：{exc}"

    if app_pub == alipay_pub:
        return "swapped", (
            "「支付宝公钥」填成了「应用公钥」（两者完全相同）\n"
            "  应用公钥是你自己的 → 需要上传给支付宝\n"
            "  支付宝公钥是支付宝的 → 需要从开放平台复制回来\n"
            "  路径：开放平台 → 我的应用 → 应用详情 → 开发设置\n"
            "        → 接口加签方式 → 点击「查看支付宝公钥」复制"
        )

    return "ok", "公钥与「应用公钥」不同，配对正常"
