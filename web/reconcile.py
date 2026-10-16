"""The same verified Alipay reconciliation for the worker, return page and console."""
import sqlite3

from bypay.alipay_client import AlipayClient, AlipayError
from .channels import ChannelError


class Reconciler:
    def __init__(self, store, channels, trace, client_factory=AlipayClient):
        self.store, self.channels, self.trace = store, channels, trace
        self.client_factory = client_factory

    def snapshot(self, number):
        order = self.store.order(number)
        check = self.store.payment_check(number) or {}
        state = {"PAID": "verified", "CLOSED": "closed", "REFUNDING":"refunding", "PARTIALLY_REFUNDED":"partially_refunded", "REFUNDED":"refunded"}.get(order["status"], check.get("state", "scheduled"))
        return {"order": order, "verification": {"state": state,
                "error": check.get("error", "") if order["status"] == "CREATED" else "",
                "checked_at": check.get("checked_at"), "next_check_at": check.get("next_check")}}

    def sync(self, number, source="manual"):
        job = self.store.claim_payment_check(number)
        if job:
            self._execute(job, source)
        return self.snapshot(number)

    def tick(self):
        job = self.store.claim_payment_check()
        if not job:
            return False
        self._execute(job, "automatic")
        return True

    def _execute(self, job, source):
        number = job["out_trade_no"]
        order = self.store.order(number)
        state, error = "error", "payment_provider_error"

        def log(stage, reason, details=None, accepted=True):
            self.trace(stage, reason, {"source": source, **(details or {})}, number, order["user_id"], accepted)

        try:
            cfg = self.channels.payment_config(order)
            if not all(cfg.get(k) for k in ("app_id", "seller_id", "app_private_key", "alipay_public_key")):
                error = "payment_not_configured"
                log("query.rejected", error, accepted=False)
                return
            if cfg["app_id"] != order["app_id"] or cfg["seller_id"] != order["seller_id"]:
                raise ValueError("order_merchant_mismatch")
            log("query.started", "query_requested", {"method": "alipay.trade.query", "attempt": job["attempts"]})
            # execute() in the official SDK rejects missing or invalid response signatures.
            result = self.client_factory(cfg).query(out_trade_no=number)
            log("query.response", "sdk_response_received", result.get("sdk_response", result), result.get("is_success", False) or result.get("sub_code") == "ACQ.TRADE_NOT_EXIST")
            if not result.get("is_success"):
                if result.get("sub_code") == "ACQ.TRADE_NOT_EXIST":
                    state, error = "awaiting_checkout", ""
                    log("query.waiting", "awaiting_checkout")
                else:
                    error = {"isv.invalid-signature": "payment_request_signature_invalid",
                             "isv.app-id-invalid": "payment_app_id_invalid"}.get(result.get("sub_code"), "payment_provider_error")
                    log("query.rejected", error, {"code": result.get("code"), "sub_code": result.get("sub_code")}, False)
                return
            # Bind the signed response to the requested order BEFORE any write.
            # Never let a response for a different order update that other order.
            if result.get("out_trade_no") != number:
                raise ValueError("query_order_mismatch")
            for key in ("app_id", "seller_id"):
                if result.get(key) and result[key] != cfg[key]:
                    raise ValueError("merchant_mismatch")
            self.store.apply_notification({**result, "app_id": cfg["app_id"], "seller_id": cfg["seller_id"]}, cfg["app_id"], cfg["seller_id"])
            state = {"PAID": "verified", "CLOSED": "closed"}.get(self.store.order(number)["status"], "waiting")
            error = ""
            log("query.accepted", "query_verified_and_saved", {"trade_status": result["trade_status"],
                "total_amount": result["total_amount"], "trade_no": result["trade_no"], "status": self.store.order(number)["status"]})
        except AlipayError as exc:
            error = getattr(exc, "code", "payment_provider_error")
            log("query.rejected", error, {"exception_type": type(exc).__name__}, False)
        except ChannelError:
            error = "channel_config_locked"
            log("query.rejected", error, accepted=False)
        except (ValueError, sqlite3.IntegrityError) as exc:
            error = "payment_mismatch"
            reason = str(exc) if isinstance(exc, ValueError) else "trade_conflict"
            log("query.rejected", error, {"validation": reason}, False)
        finally:
            self.store.finish_payment_check(job, state, error)
