"""Locally encoded payment QRs, with quiet zones and channel brand assets."""
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET

import qrcode
from qrcode.exceptions import DataOverflowError

COLORS = {"alipay": "#075fbd", "wechat": "#087c49", "paypal": "#003087", "stripe": "#5145bc"}
ASSETS = Path(__file__).resolve().parent.parent / "frontend/public/brand/payments.svg"


@lru_cache(maxsize=64)
def payment_qr_svg(value, channel="alipay"):
    color = COLORS[channel]
    for correction in (qrcode.constants.ERROR_CORRECT_H, qrcode.constants.ERROR_CORRECT_M):
        qr = qrcode.QRCode(error_correction=correction, border=4)
        qr.add_data(value)
        try:
            qr.make(fit=True)
            break
        except DataOverflowError:
            if correction == qrcode.constants.ERROR_CORRECT_M: raise ValueError("qr_too_large") from None
    matrix = qr.get_matrix()
    size = len(matrix)
    paths = "".join(f"M{x} {y}h1v1h-1z" for y, row in enumerate(matrix) for x, value in enumerate(row) if value)
    # Dense checkout URLs remain fully unobscured; compact QRs use a small logo.
    logo = ""
    if correction == qrcode.constants.ERROR_CORRECT_H and qr.modules_count <= 81:
        width = max(9, int(qr.modules_count * .15) | 1)
        offset = (size - width) / 2
        symbol = ET.parse(ASSETS).find(f".//{{http://www.w3.org/2000/svg}}symbol[@id='{channel}']")
        if symbol is not None:
            paths_logo = "".join(ET.tostring(child, encoding="unicode") for child in symbol)
            logo = f'<rect x="{offset}" y="{offset}" width="{width}" height="{width}" rx="1.3" fill="white"/>'
            logo += f'<svg x="{offset + 1}" y="{offset + 1}" width="{width - 2}" height="{width - 2}" viewBox="0 0 24 24" style="color:{color}" fill="currentColor">{paths_logo}</svg>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{size * 8}" height="{size * 8}" viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" fill="white"/><path d="{paths}" fill="{color}" shape-rendering="crispEdges"/>{logo}</svg>'
