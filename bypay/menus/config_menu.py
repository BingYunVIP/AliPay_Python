"""「1 配置」菜单：引导式填写支付宝参数，加密保存。"""

from __future__ import annotations

from .. import constants, logger, ui
from ..config_store import ConfigError, ConfigStore


class ConfigMenu:
    def __init__(self, store: ConfigStore):
        self.store = store

    # ------------------------------------------------------------ 主循环
    def run(self) -> None:
        while True:
            ui.clear()
            ui.title("1 · 配置")
            ui.info("支付宝开放平台参数，填写后加密保存在本机，无需每次重复填写。")
            print()
            print("  1. 引导式填写全部配置   （推荐首次使用）")
            print("  2. 查看当前配置         （敏感项自动脱敏）")
            print("  3. 修改单项配置")
            print("  4. 生成 RSA2 密钥对     （没有密钥时用这个）")
            print("  5. 重置配置             （清空全部配置）")
            print("  0. 返回上级")
            print()
            choice = input("  请选择: ").strip()

            if choice == "1":
                self._guided_setup()
            elif choice == "2":
                self._show()
            elif choice == "3":
                self._edit_one()
            elif choice == "4":
                self._gen_keypair()
            elif choice == "5":
                self._reset()
            elif choice == "0":
                return
            else:
                ui.error("无效选项")

    # ------------------------------------------------------------ 1 引导式
    def _guided_setup(self) -> None:
        ui.clear()
        ui.title("1.1 · 引导式填写配置")
        ui.info("下面逐项填写。每一项都会说明「填什么」和「去哪里获取」。")
        ui.hint("敏感项（私钥/公钥）输入时以 * 号回显，真实内容不会显示。")
        ui.hint("粘贴多行 PEM 私钥时会自动识别 -----END----- 结束，无需手动处理。")
        ui.hint("已填过的敏感项可直接回车保留原值。")
        ui.pause()

        try:
            cfg = self.store.load()
        except ConfigError as exc:
            ui.error(str(exc))
            ui.pause()
            return

        for field in constants.CONFIG_FIELDS:
            key = field["key"]
            print()
            print(ui.cyan("  ┌─ " + field["label"] + " " + "─" * 30))
            ui.info(f"  说明：{field['help']}")
            print(ui.gray("  获取位置："))
            ui.hint(field.get("where", "—"))

            existing = cfg.get(key, "")

            if field.get("choices"):
                value = ui.ask_choice(
                    f"  请输入「{field['label']}」",
                    field["choices"],
                    existing or field.get("default", ""),
                )
            elif field.get("secret"):
                value = ui.ask_secret(f"  请输入「{field['label']}」", default_set=bool(existing))
                if not value:
                    value = existing
            else:
                value = ui.ask(
                    f"  请输入「{field['label']}」",
                    existing or field.get("default", ""),
                    allow_empty=not field.get("required"),
                )

            cfg[key] = value

            # 选择环境后自动补网关
            if key == "env" and value in constants.GATEWAY_BY_ENV:
                cfg["gateway"] = constants.GATEWAY_BY_ENV[value]
                ui.info(f"  → 网关已自动设为：{cfg['gateway']}")

        # 保存
        print()
        self.store.save(cfg)
        ui.ok(f"配置已加密保存到：{constants.CONFIG_FILE}")
        ui.hint(f"加密方式：{self.store.box.describe_mode()}")

        self._check_key_pair(cfg)

        missing = self.store.missing_required(cfg)
        if missing:
            labels = "、".join(constants.FIELD_MAP[k]["label"] for k in missing)
            ui.warn(f"仍有必填项未填写：{labels}")
        else:
            ui.ok("必填项已齐全，可以进入「3 测试支付」了")
        ui.pause()

    # ------------------------------------------------------------ 2 查看
    def _show(self) -> None:
        ui.clear()
        ui.title("1.2 · 当前配置（敏感项已脱敏）")
        try:
            cfg = self.store.load()
        except ConfigError as exc:
            ui.error(str(exc))
            ui.pause()
            return

        if not self.store.exists():
            ui.warn("尚未创建配置文件")
            ui.pause()
            return

        print()
        ui.kv_table(self.store.summary_lines(cfg), key_width=26)
        print()
        ui.hint(f"配置文件：{constants.CONFIG_FILE}")
        ui.hint(f"加密方式：{self.store.box.describe_mode()}")

        missing = self.store.missing_required(cfg)
        if missing:
            labels = "、".join(constants.FIELD_MAP[k]["label"] for k in missing)
            ui.warn(f"缺失必填项：{labels}")
        ui.pause()

    # ------------------------------------------------------------ 3 改单项
    def _edit_one(self) -> None:
        ui.clear()
        ui.title("1.3 · 修改单项配置")
        try:
            cfg = self.store.load()
        except ConfigError as exc:
            ui.error(str(exc))
            ui.pause()
            return

        print()
        ui.hint("带 * 的为必填项；输入敏感项时会以 * 号回显")
        for idx, field in enumerate(constants.CONFIG_FIELDS, start=1):
            current = cfg.get(field["key"], "")
            shown = ui.mask(current) if field.get("secret") else (current or ui.gray("(未设置)"))
            mark = "*" if field.get("required") else " "
            print(f"  {idx:>2}.{mark} {field['label']:<22} {shown}")
        print("   0. 返回")

        raw = input("\n  请选择要修改的项: ").strip()
        if raw == "0" or not raw.isdigit():
            return
        idx = int(raw)
        if not (1 <= idx <= len(constants.CONFIG_FIELDS)):
            ui.error("序号超出范围")
            ui.pause()
            return

        field = constants.CONFIG_FIELDS[idx - 1]
        key = field["key"]
        print()
        ui.info(f"说明：{field['help']}")
        ui.hint(field.get("where", "—"))

        existing = cfg.get(key, "")
        if field.get("choices"):
            value = ui.ask_choice(f"  新值", field["choices"], existing)
        elif field.get("secret"):
            value = ui.ask_secret("  新值", default_set=bool(existing)) or existing
        else:
            value = ui.ask("  新值", existing, allow_empty=not field.get("required"))

        cfg[key] = value
        if key == "env" and value in constants.GATEWAY_BY_ENV:
            cfg["gateway"] = constants.GATEWAY_BY_ENV[value]

        self.store.save(cfg)
        ui.ok("已保存")

        if key in ("app_private_key", "alipay_public_key"):
            self._check_key_pair(cfg)
        ui.pause()

    # ------------------------------------------------------------ 密钥配对校验
    def _check_key_pair(self, cfg: dict) -> None:
        """即时检查「支付宝公钥」是否被误填为「应用公钥」（最常见错误）。"""
        from ..key_utils import check_key_pair

        status, detail = check_key_pair(
            cfg.get("app_private_key", ""), cfg.get("alipay_public_key", "")
        )
        if status == "swapped":
            print()
            ui.error("检测到密钥填反：")
            ui.hint(detail)

    # ------------------------------------------------------------ 4 生成密钥
    def _gen_keypair(self) -> None:
        ui.clear()
        ui.title("1.4 · 生成 RSA2 密钥对")
        ui.info("将为你生成一对 RSA2（2048 位）密钥：")
        ui.hint("应用私钥 → 自动保存到配置（加密），用于给请求签名")
        ui.hint("应用公钥 → 需要你复制到支付宝开放平台，用于让支付宝验证你的请求")
        print()
        if not ui.confirm("确认生成新的密钥对？", default=True):
            return

        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:
            ui.error("缺少 cryptography 库，请执行：.venv/bin/pip install cryptography")
            ui.pause()
            return

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        # 私钥必须是 PKCS#1（-----BEGIN RSA PRIVATE KEY-----）
        # 这是支付宝官方 Python SDK 底层 rsa 库的硬性要求
        private_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        public_pem = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

        try:
            cfg = self.store.load()
        except ConfigError:
            cfg = {}
        cfg["app_private_key"] = private_pem
        self.store.save(cfg)
        logger.info("config", "已生成 RSA2 密钥对", 位数=2048, 格式="PKCS#1")

        # 支付宝控制台需要粘贴「裸 Base64」形式的公钥
        import base64

        public_b64 = base64.b64encode(
            key.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode()

        print()
        ui.ok("应用私钥已生成并加密保存到配置")
        print()
        print(ui.yellow("  ┌─ 请复制下面这段「应用公钥」到支付宝开放平台 ────────────────"))
        print()
        print(public_b64)
        print()
        print(ui.yellow("  └───────────────────────────────────────────────────────────"))
        print()
        ui.hint("操作路径：开放平台 → 我的应用 → 应用详情 → 开发设置 → 接口加签方式")
        ui.hint("          → 选择「公钥」模式 → 粘贴上面的「应用公钥」→ 保存")
        ui.hint("保存后，再把平台生成的「支付宝公钥」填回本工具的配置里（第 4 项）")
        print()
        ui.hint("（下面是同一把公钥的 PEM 形式，一般用不到，仅备查）")
        ui.hint(public_pem.strip())
        ui.pause()

    # ------------------------------------------------------------ 5 重置
    def _reset(self) -> None:
        ui.clear()
        ui.title("1.5 · 重置配置")
        ui.warn("将删除加密配置文件（不含主密钥，密钥保留）")
        if not ui.confirm("确认重置？", default=False):
            return
        self.store.reset()
        ui.ok("已重置，请重新配置")
        ui.pause()
