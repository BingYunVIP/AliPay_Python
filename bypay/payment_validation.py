"""Shared payment checks. A signature alone is not proof that our order was paid."""

import re
from decimal import Decimal


def money(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,8}(?:\.\d{1,2})?", value):
        raise ValueError("invalid_amount")
    amount = Decimal(value)
    if amount <= 0:
        raise ValueError("invalid_amount")
    return format(amount, ".2f")


def validate_notification(params: dict, order: dict | None, app_id: str, seller_id: str) -> None:
    if not app_id or not seller_id:
        raise ValueError("merchant_not_configured")
    if not order or params.get("out_trade_no") != order["out_trade_no"]:
        raise ValueError("unknown_order")
    if params.get("app_id") != app_id or params.get("seller_id") != seller_id:
        raise ValueError("merchant_mismatch")
    if money(params.get("total_amount", "")) != money(order["amount"]):
        raise ValueError("amount_mismatch")
    trade_no = params.get("trade_no", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", trade_no):
        raise ValueError("invalid_trade_no")
    if order.get("trade_no") and order["trade_no"] != trade_no:
        raise ValueError("trade_mismatch")
    if params.get("trade_status") not in {"WAIT_BUYER_PAY", "TRADE_SUCCESS", "TRADE_FINISHED", "TRADE_CLOSED"}:
        raise ValueError("invalid_trade_status")
