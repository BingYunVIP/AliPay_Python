"""全局常量与配置字段定义。

集中定义：
  - 项目路径
  - 默认运行参数
  - 支付宝配置字段（含「在哪里获取」的真实指导，供引导式配置使用）
"""

from pathlib import Path

# ---------------------------------------------------------------- 路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_FILE = DATA_DIR / "config.enc"        # 加密后的配置
SECRET_KEY_FILE = DATA_DIR / "secret.key"    # 本机主密钥（0600 权限）
SETTINGS_FILE = DATA_DIR / "settings.json"   # 运行设置（明文，不含敏感信息）
ORDERS_FILE = DATA_DIR / "orders.json"       # 测试订单记录
LOG_DIR = DATA_DIR / "logs"
QRCODE_DIR = DATA_DIR / "qrcodes"          # 生成的支付二维码
TUNNEL_LOG = DATA_DIR / "tunnel.log"       # cloudflared 隧道日志
TUNNEL_STATE = DATA_DIR / "tunnel.json"    # 隧道状态（pid / 公网地址）
NOTIFY_RECORDS = DATA_DIR / "notify_records.jsonl"   # 回调记录（落盘）

APP_NAME = "ByPay · 支付宝测试工具"
APP_VERSION = "0.1.0"

# ---------------------------------------------------------------- 网关
GATEWAY_PRODUCTION = "https://openapi.alipay.com/gateway.do"
GATEWAY_SANDBOX = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"

GATEWAY_BY_ENV = {
    "production": GATEWAY_PRODUCTION,
    "sandbox": GATEWAY_SANDBOX,
}

# ---------------------------------------------------------------- 默认运行设置
DEFAULT_SETTINGS = {
    "order_timeout": 900,      # 订单有效期（秒），默认 15 分钟
    "poll_interval": 15,       # 回调 / 订单状态检测间隔（秒），默认 15 秒
    "test_amount": "0.01",     # 测试金额（元）
    "notify_port": 8899,       # 本地回调服务端口
    "auto_start_tunnel": False,  # 是否在启动回调服务时自动拉起公网隧道
    "debug_skip_verify": False,  # 是否跳过回调验签（仅本地自测用，生产必须 False）
    "pay_method": "page",      # 支付方式（page=电脑网站支付）
    "log_level": "INFO",
}

# 支付方式（当前仅启用网页支付，其余为后续扩展预留）
PAY_METHODS = {
    "page": "电脑网站支付（alipay.trade.page.pay）",
    "wap": "手机网站支付（alipay.trade.wap.pay）",
    "face": "当面付-扫码（alipay.trade.precreate）",
    "app": "APP 支付（alipay.trade.app.pay）",
}

# ---------------------------------------------------------------- 配置字段定义
# secret=True 的字段会在落盘时被加密，在展示时被脱敏
CONFIG_FIELDS = [
    {
        "key": "env",
        "label": "运行环境",
        "required": True,
        "secret": False,
        "default": "sandbox",
        "choices": ["sandbox", "production"],
        "help": "sandbox = 沙箱环境（强烈建议先用沙箱跑通）；production = 正式环境",
        "where": (
            "沙箱：无需申请，登录支付宝开放平台 → 控制台 → 「沙箱环境」即可看到沙箱 APPID\n"
            "正式：需在开放平台创建应用，并签约「电脑网站支付」"
        ),
    },
    {
        "key": "app_id",
        "label": "应用 APPID",
        "required": True,
        "secret": False,
        "help": "应用的唯一标识，纯数字（一般 16 位以上）",
        "where": (
            "正式：开放平台 → 控制台 → 我的应用 → 点进应用 → 「应用信息」中的 APPID\n"
            "沙箱：开放平台 → 控制台 → 沙箱环境 → 页面顶部显示的「沙箱 APPID」"
        ),
    },
    {
        "key": "seller_id",
        "label": "收款方支付宝 UID",
        "required": True,
        "secret": False,
        "help": "收款支付宝账户的 UID（2088 开头的 16 位数字），回调用于核对收款方",
        "where": "支付宝开放平台 → 账户中心 → 商户信息 → 支付宝账户 UID",
    },
    {
        "key": "app_private_key",
        "label": "应用私钥（RSA2）",
        "required": True,
        "secret": True,
        "help": "你自己持有的 RSA2 私钥（PKCS#8 / PEM），用于对请求签名",
        "where": (
            "方式一（推荐）：用本工具「1 配置 → 4 生成 RSA2 密钥对」自动生成\n"
            "方式二：用支付宝官方「密钥生成工具」生成，复制其中的「应用私钥」\n"
            "⚠️ 一定要用「应用私钥」，不要误填「应用公钥」或「支付宝公钥」"
        ),
    },
    {
        "key": "alipay_public_key",
        "label": "支付宝公钥",
        "required": True,
        "secret": True,
        "help": "支付宝方的公钥，用于验证支付宝返回数据与异步通知的签名",
        "where": (
            "开放平台 → 我的应用 → 应用详情 → 「开发设置」→ 接口加签方式\n"
            "→ 点击「查看支付宝公钥」并复制（一串 Base64 文本，不含 PEM 头尾）\n"
            "⚠️ 沙箱的支付宝公钥与正式环境不同，切勿混用"
        ),
    },
    {
        "key": "gateway",
        "label": "网关地址",
        "required": True,
        "secret": False,
        "default": GATEWAY_SANDBOX,
        "help": "支付宝开放平台接口网关地址",
        "where": (
            f"正式：{GATEWAY_PRODUCTION}\n"
            f"沙箱：{GATEWAY_SANDBOX}\n"
            "选择运行环境后会自动填好，一般无需手动修改"
        ),
    },
    {
        "key": "notify_url",
        "label": "异步通知地址 notify_url",
        "required": False,
        "secret": False,
        "help": "支付成功后，支付宝服务器「后台悄悄」POST 到该地址（必须公网可达）",
        "where": (
            "填写你服务器的公网地址，例如 https://你的域名/alipay/notify\n"
            "⚠️ 本地无公网地址时可留空：本工具会用「主动查询订单」方式检测支付结果"
        ),
    },
    {
        "key": "return_url",
        "label": "同步跳转地址 return_url",
        "required": False,
        "secret": False,
        "help": "用户付款完成后浏览器跳回的地址（仅用于展示，不可作为到账依据）",
        "where": (
            "填写你的页面地址，例如 https://你的域名/pay/return\n"
            "本地测试可留空，或填 http://localhost:8899/"
        ),
    },
    {
        "key": "sign_type",
        "label": "签名类型",
        "required": True,
        "secret": False,
        "default": "RSA2",
        "help": "固定 RSA2（推荐）",
        "where": "无需修改",
    },
    {
        "key": "charset",
        "label": "字符集",
        "required": True,
        "secret": False,
        "default": "utf-8",
        "help": "固定 utf-8",
        "where": "无需修改",
    },
]

FIELD_MAP = {f["key"]: f for f in CONFIG_FIELDS}
SECRET_KEYS = {f["key"] for f in CONFIG_FIELDS if f.get("secret")}
REQUIRED_KEYS = {f["key"] for f in CONFIG_FIELDS if f.get("required")}
