"""Ported payment/refund rules from Epay/includes/lib/Order.php::refund_info/refund
and Epay/plugins/alipay/alipay_plugin.php::refund. SQLite reservations and stable
out_request_no additionally protect concurrent requests and uncertain responses.
"""
import time
from decimal import Decimal
from bypay.alipay_client import AlipayClient
from bypay.payment_validation import money


class RefundError(ValueError):
    pass


class Refunds:
    def __init__(self, store, channels, trace, factory=AlipayClient):
        self.store, self.channels, self.trace, self.factory = store, channels, trace, factory

    def get(self, refund_no):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM refunds WHERE refund_no=?", (refund_no,)).fetchone()
        return dict(row) if row else None

    def start(self, number, refund_no, amount, reason, actor):
        amount, now = money(amount), int(time.time())
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM refunds WHERE refund_no=?", (refund_no,)).fetchone()
            order = db.execute("SELECT * FROM orders WHERE out_trade_no=?", (number,)).fetchone()
            if old:
                if old["out_trade_no"] != number or old["amount"] != amount or old["reason"] != reason: raise RefundError("refund_conflict")
                if old["status"] in {"SUCCESS","FAILED"} or old["locked_until"] > now: return dict(old)
            else:
                if not order or order["deleted_at"] or not order["paid_at"] or order["status"] not in {"PAID","PARTIALLY_REFUNDED"}: raise RefundError("order_not_refundable")
                if order["channel"] != "alipay": raise RefundError("refund_channel_unavailable")
                if Decimal(amount) > Decimal(order["amount"]) - Decimal(order["refunded_amount"]): raise RefundError("refund_exceeds_remaining")
                db.execute("INSERT INTO refunds(refund_no,out_trade_no,amount,reason,actor,status,created_at) VALUES (?,?,?,?,?,'PENDING',?)", (refund_no,number,amount,reason,actor,now))
            db.execute("UPDATE orders SET status='REFUNDING',active_refund_no=? WHERE out_trade_no=?", (refund_no,number))
            db.execute("UPDATE refunds SET status='PENDING',locked_until=?,next_query=? WHERE refund_no=?", (now+60,now+30,refund_no))
        self.trace("refund.requested", "refund_requested", {"refund_no": refund_no,"amount":amount}, number,actor)
        record = self.get(refund_no)
        try:
            cfg = self.channels.payment_config(dict(order))
            if cfg["app_id"] != order["app_id"] or cfg["seller_id"] != order["seller_id"]: raise ValueError("payment_mismatch")
            result = self.factory(cfg).refund(number,amount,reason,out_request_no=refund_no,trade_no=order["trade_no"])
            self.trace("refund.response", "refund_response_received", result.get("sdk_response",result),number,actor,result.get("is_success",False))
            if result.get("is_success"):
                self._accept(record,result,query=False)
            else:
                # Only a signed, definitive business rejection releases the reservation.
                uncertain = result.get("code") not in {"40002","40004"} or any(term in result.get("sub_code","").upper() for term in ("SYSTEM", "TIMEOUT", "UNKNOWN"))
                self._finish(record, "UNKNOWN" if uncertain else "FAILED", "refund_provider_error")
        except Exception as exc:
            self._finish(record,"UNKNOWN","refund_unconfirmed")
            self.trace("refund.unconfirmed", "refund_unconfirmed", {"refund_no":refund_no,"exception_type":type(exc).__name__},number,actor,False)
        return self.get(refund_no)

    def _accept(self, record, result, query):
        order = self.store.order(record["out_trade_no"])
        if result.get("out_trade_no") != order["out_trade_no"] or result.get("trade_no") != order["trade_no"]: raise ValueError("refund_mismatch")
        if query and (result.get("out_request_no") != record["refund_no"] or result.get("refund_status") != "REFUND_SUCCESS"): raise ValueError("refund_unconfirmed")
        if money(result.get("refund_amount" if query else "refund_fee", "")) != record["amount"]: raise ValueError("refund_mismatch")
        self._finish(record,"SUCCESS")

    def _finish(self, record, status, error=""):
        now = int(time.time())
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT status FROM refunds WHERE refund_no=?", (record["refund_no"],)).fetchone()
            if current["status"] in {"SUCCESS","FAILED"}: return
            order = db.execute("SELECT * FROM orders WHERE out_trade_no=?", (record["out_trade_no"],)).fetchone()
            total = Decimal(order["refunded_amount"])
            if status == "SUCCESS": total += Decimal(record["amount"])
            if total > Decimal(order["amount"]): raise RefundError("refund_exceeds_remaining")
            order_status = "REFUNDING" if status == "UNKNOWN" else "REFUNDED" if total == Decimal(order["amount"]) else "PARTIALLY_REFUNDED" if total > 0 else "PAID"
            db.execute("UPDATE refunds SET status=?,error=?,confirmed_at=?,locked_until=0,next_query=? WHERE refund_no=?", (status,error,now if status=="SUCCESS" else None,now+60,record["refund_no"]))
            db.execute("UPDATE orders SET status=?,refunded_amount=?,active_refund_no=? WHERE out_trade_no=?", (order_status,format(total,".2f"), record["refund_no"] if status=="UNKNOWN" else None,record["out_trade_no"]))
        self.trace("refund."+status.lower(), "refund_"+status.lower(), {"refund_no":record["refund_no"],"amount":record["amount"],"error":error},record["out_trade_no"],order["user_id"],status=="SUCCESS")

    def sync(self, refund_no):
        record = self.get(refund_no)
        if not record or record["status"] in {"SUCCESS","FAILED"}: return record
        now=int(time.time())
        with self.store.connect() as db:
            claimed = db.execute("UPDATE refunds SET locked_until=? WHERE refund_no=? AND locked_until<=? AND status IN ('PENDING','UNKNOWN')", (now+60,refund_no,now)).rowcount
        if not claimed: return self.get(refund_no)
        try:
            order=self.store.order(record["out_trade_no"])
            result=self.factory(self.channels.payment_config(order)).query_refund(order["out_trade_no"],refund_no)
            self.trace("refund.query", "refund_query_received", result.get("sdk_response",result),order["out_trade_no"],order["user_id"],result.get("is_success",False))
            self._accept(record,result,query=True)
        except Exception:
            self._finish(record,"UNKNOWN","refund_unconfirmed")
        return self.get(refund_no)

    def tick(self):
        now=int(time.time())
        with self.store.connect() as db:
            row=db.execute("SELECT refund_no FROM refunds WHERE status IN ('PENDING','UNKNOWN') AND next_query<=? AND locked_until<=? AND created_at>=? ORDER BY next_query LIMIT 1",(now,now,now-172800)).fetchone()
        if not row: return False
        self.sync(row["refund_no"])
        return True
