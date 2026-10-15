"""支付链接二维码：生成 + 美化 + 解码校验。

说明：
    支付宝电脑网站支付返回的是一个很长的跳转链接（约 800 字符）。
    本模块可以把它转成二维码图片，并在四周加上标题、金额、订单号等装饰，
    方便用手机支付宝扫码支付。

对外接口：
    make_qr_image(url)            生成纯二维码图片对象
    make_beautified_png(...)      生成美化后的二维码 PNG
    decode_qr(path)               解码二维码（用于自检/测试）
"""

from __future__ import annotations

from pathlib import Path

from . import constants, logger

# 主题色（支付宝蓝）
THEME = (22, 119, 255)
GRAY = (140, 140, 140)
DARK = (40, 40, 40)
ORANGE = (255, 106, 0)

# 中文字体候选（按优先级）
FONT_CANDIDATES = [
    "/root/.local/share/fonts/NotoSansCJKsc-Regular.otf",
    "/root/.local/share/fonts/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def find_font_path() -> str | None:
    """找一个可用的字体文件。"""
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _load_font(size: int):
    from PIL import ImageFont

    path = find_font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _imports():
    try:
        import qrcode
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            f"缺少二维码依赖（{getattr(exc, 'name', exc)}）。\n"
            "  请执行：.venv/bin/pip install -r requirements.txt"
        ) from exc
    return qrcode, Image, ImageDraw


def make_qr_image(url: str, box_size: int = 10, border: int = 3):
    """生成纯二维码图片对象（PIL Image）。"""
    qrcode, _, _ = _imports()

    qr = qrcode.QRCode(
        version=None,                       # 自动选择版本
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def qr_version_info(url: str) -> dict:
    """返回二维码规格信息（版本、模块数），便于判断是否过密。"""
    qrcode, _, _ = _imports()

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    modules = qr.modules_count
    return {
        "version": qr.version,
        "modules": modules,
        "url_length": len(url),
        "density": "偏密（建议放大后再扫）" if modules >= 80 else "正常",
    }


def make_beautified_png(
    url: str,
    out_path: Path,
    amount: str = "",
    out_trade_no: str = "",
    title: str = "ByPay · 冰云支付",
    subtitle: str = "请用支付宝 App 扫码支付",
    box_size: int = 10,
) -> Path:
    """生成带装饰的二维码图片。

    版式：
        ┌───────────────────────────┐
        │      ByPay · 冰云支付      │
        │   ┌───────────────────┐   │
        │   │                   │   │
        │   │     二维码         │   │
        │   │                   │   │
        │   └───────────────────┘   │
        │        ￥0.01             │
        │   订单号 BYP2026...        │
        │   请用支付宝 App 扫码支付   │
        └───────────────────────────┘
    """
    _, Image, ImageDraw = _imports()

    qr_img = make_qr_image(url, box_size=box_size, border=2)
    qw, qh = qr_img.size

    pad = 44
    header_h = 78
    footer_h = 128
    width = qw + pad * 2
    height = qh + pad * 2 + header_h + footer_h

    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)

    # 外边框（圆角）
    draw.rounded_rectangle(
        [(6, 6), (width - 7, height - 7)], radius=22, outline=THEME, width=3
    )

    # 标题
    f_title = _load_font(30)
    tw = draw.textlength(title, font=f_title)
    draw.text(((width - tw) / 2, 30), title, font=f_title, fill=THEME)

    # 标题下方分隔线
    draw.line([(pad, 70), (width - pad, 70)], fill=(230, 235, 245), width=2)

    # 二维码
    qr_x = pad
    qr_y = header_h + pad - 10
    canvas.paste(qr_img, (qr_x, qr_y))

    # 二维码外框
    draw.rectangle(
        [(qr_x - 6, qr_y - 6), (qr_x + qw + 5, qr_y + qh + 5)],
        outline=(225, 232, 245),
        width=2,
    )

    # 金额（醒目）
    y = qr_y + qh + 22
    if amount:
        f_amount = _load_font(40)
        text = f"￥{amount}"
        tw = draw.textlength(text, font=f_amount)
        draw.text(((width - tw) / 2, y), text, font=f_amount, fill=ORANGE)
        y += 52

    # 订单号
    if out_trade_no:
        f_small = _load_font(20)
        text = f"订单号 {out_trade_no}"
        tw = draw.textlength(text, font=f_small)
        draw.text(((width - tw) / 2, y), text, font=f_small, fill=GRAY)
        y += 32

    # 提示
    f_note = _load_font(22)
    tw = draw.textlength(subtitle, font=f_note)
    draw.text(((width - tw) / 2, y), subtitle, font=f_note, fill=DARK)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    logger.info(
        "order", "已生成支付二维码",
        文件=out_path.name, 尺寸=f"{width}x{height}", 二维码模块数=qr_version_info(url)["modules"],
    )
    return out_path


def make_plain_png(url: str, out_path: Path, box_size: int = 10) -> Path:
    """只生成二维码本身（不加装饰）。"""
    img = make_qr_image(url, box_size=box_size)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    logger.info("order", "已生成纯二维码", 文件=out_path.name)
    return out_path


def decode_qr(path: Path) -> list:
    """解码二维码，返回识别到的字符串列表（用于验证二维码是否正确）。"""
    try:
        from pyzbar.pyzbar import decode as _decode
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            f"缺少二维码解码依赖（{getattr(exc, 'name', exc)}）。"
        ) from exc

    with Image.open(path) as img:
        results = _decode(img)
    return [r.data.decode("utf-8", "replace") for r in results]


def qr_dir() -> Path:
    return constants.QRCODE_DIR
