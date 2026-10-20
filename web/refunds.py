"""Ported payment/refund rules from Epay/includes/lib/Order.php::refund_info/refund
and Epay/plugins/alipay/alipay_plugin.php::refund (Alipay) and plugins/wxpayn/wxpayn_plugin.php::refund (WeChat Pay).
SQLite reservations and stable out_request_no / out_refund_no additionally protect concurrent requests and uncertain responses.
"""
import time
from decimal import Decimal
import re
import json
from bypay.alipay_client import AlipayClient
from bypay.payment_validation import money
from bypay.wechat_client import REFUND_REFUSALS, WechatClient, WechatError
from .platform_rules import cents
from .crypto import ASSET_BY_CODE, amount_for_cny, validate_address
from .crypto_receipts import verify_transfer


class RefundError(ValueError):
    pass


class Refunds:
    def __init__(self, store, channels, trace, factory=AlipayClient, wechat_factory=WechatClient):
        self.store, self.channels, self.trace, self.factory, self.wechat_factory = store, channels, trace, factory, wechat_factory

    def get(self, refund_no):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM refunds WHERE refund_no=?", (refund_no,)).fetchone()
        return dict(row) if row else None

    def start(self, number, refund_no, amount, reason, actor, refund_to=''):
        amount, now = money(amount), int(time.time())
        order=self.store.order(number)
        if order and order['channel']=='crypto':return self._request_crypto(order,refund_no,amount,reason,actor,now,refund_to)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM refunds WHERE refund_no=?", (refund_no,)).fetchone()
            order = db.execute("SELECT * FROM orders WHERE out_trade_no=?", (number,)).fetchone()
            if old:
                if old["out_trade_no"] != number or old["amount"] != amount or old["reason"] != reason: raise RefundError("refund_conflict")
                if old["status"] in {"SUCCESS","FAILED"} or old["locked_until"] > now: return dict(old)
            else:
                if not order or order["deleted_at"] or not order["paid_at"] or order["status"] not in {"PAID","PARTIALLY_REFUNDED"}: raise RefundError("order_not_refundable")
                if hasattr(self.store,'operations') and db.execute('SELECT 1 FROM platform_bills WHERE number=?',(number,)).fetchone():raise RefundError('platform_bill_refund_requires_review')
                if order["channel"] not in {"alipay", "wechat"}: raise RefundError("refund_channel_unavailable")
                if Decimal(amount) > Decimal(order["amount"]) - Decimal(order["refunded_amount"]): raise RefundError("refund_exceeds_remaining")
                if hasattr(self.store,'operations'):
                    policy=self.store.operations.rules.read(db)
                    owner=db.execute('SELECT role FROM users WHERE id=?',(order['user_id'],)).fetchone()
                    if owner and owner['role']!='admin' and policy['deposit_enabled'] and self.store.operations.balance(db,order['user_id'],'deposit')<cents(policy['deposit_minimum']):
                        raise RefundError('deposit_required')
                    try:self.store.operations.reserve_refund(db,order,refund_no,amount)
                    except ValueError as exc:raise RefundError(str(exc)) from None
                db.execute("INSERT INTO refunds(refund_no,out_trade_no,amount,reason,actor,status,created_at) VALUES (?,?,?,?,?,'PENDING',?)", (refund_no,number,amount,reason,actor,now))
            db.execute("UPDATE orders SET status='REFUNDING',active_refund_no=? WHERE out_trade_no=?", (refund_no,number))
            db.execute("UPDATE refunds SET status='PENDING',locked_until=?,next_query=? WHERE refund_no=?", (now+60,now+30,refund_no))
        self.trace("refund.requested", "refund_requested", {"refund_no": refund_no,"amount":amount}, number,actor)
        record = self.get(refund_no)
        try:
            cfg = self.channels.payment_config(dict(order))
            if cfg["app_id"] != order["app_id"] or cfg["seller_id"] != order["seller_id"]: raise ValueError("payment_mismatch")
            if order["channel"] == "wechat":
                result = self.wechat_factory(cfg).refund(number, refund_no, cents(amount), cents(order["amount"]), reason, transaction_id=order["trade_no"] or "")
                self.trace("refund.response", "wechat_refund_response", result, number, actor, result["status"] == "SUCCESS")
                self._settle_wechat(record, order, result)
                return self.get(refund_no)
            result = self.factory(cfg).refund(number,amount,reason,out_request_no=refund_no,trade_no=order["trade_no"])
            self.trace("refund.response", "refund_response_received", result.get("sdk_response",result),number,actor,result.get("is_success",False))
            if result.get("is_success"):
                self._accept(record,result,query=False)
            else:
                # Only a signed, definitive business rejection releases the reservation.
                uncertain = result.get("code") not in {"40002","40004"} or any(term in result.get("sub_code","").upper() for term in ("SYSTEM", "TIMEOUT", "UNKNOWN"))
                self._finish(record, "UNKNOWN" if uncertain else "FAILED", "refund_provider_error")
        except WechatError as exc:
            # only WeChat Pay's own refusal releases the reservation; a bare 4xx from a proxy or a timeout may still have been processed
            definitive = 400 <= exc.status < 500 and exc.provider_code in REFUND_REFUSALS
            self._finish(record, "FAILED" if definitive else "UNKNOWN", exc.code if definitive else "refund_unconfirmed")
            self.trace("refund.rejected" if definitive else "refund.unconfirmed", "refund_provider_error" if definitive else "refund_unconfirmed", {"refund_no": refund_no, "provider_code": exc.provider_code, "status": exc.status}, number, actor, False)
        except Exception as exc:
            self._finish(record,"UNKNOWN","refund_unconfirmed")
            self.trace("refund.unconfirmed", "refund_unconfirmed", {"refund_no":refund_no,"exception_type":type(exc).__name__},number,actor,False)
        return self.get(refund_no)

    def _request_crypto(self,order,refund_no,amount,reason,actor,now,refund_to):
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',refund_no) or not isinstance(reason,str) or not reason.strip() or len(reason)>128:
            raise RefundError('invalid_refund_reason')
        with self.store.connect() as db:prior=db.execute('SELECT * FROM refunds WHERE refund_no=?',(refund_no,)).fetchone()
        if prior:
            if prior['out_trade_no']!=order['out_trade_no'] or prior['amount']!=amount or prior['reason']!=reason.strip() or prior['crypto_refund_to']!=refund_to:raise RefundError('refund_conflict')
            return dict(prior)
        if not order or order['deleted_at'] or not order['paid_at'] or order['status'] not in {'PAID','PARTIALLY_REFUNDED'}:
            raise RefundError('order_not_refundable')
        if Decimal(amount)>Decimal(order['amount'])-Decimal(order['refunded_amount']):raise RefundError('refund_exceeds_remaining')
        spec=ASSET_BY_CODE.get(f"{order['crypto_network']}_{order['crypto_asset']}")
        if not spec:raise RefundError('crypto_refund_unavailable')
        if not isinstance(refund_to,str) or not validate_address(spec,refund_to):raise RefundError('invalid_crypto_refund_address')
        try:
            # Cumulative proportional rounding makes a sequence of partial
            # refunds return exactly the original token quantity at 100%.
            quantum=Decimal('0.01')
            before=Decimal(order['crypto_amount'])*Decimal(order['refunded_amount'])/Decimal(order['amount'])
            after=Decimal(order['crypto_amount'])*(Decimal(order['refunded_amount'])+Decimal(amount))/Decimal(order['amount'])
            from decimal import ROUND_HALF_UP
            crypto_amount=format(after.quantize(quantum,rounding=ROUND_HALF_UP)-before.quantize(quantum,rounding=ROUND_HALF_UP),'f')
        except Exception:raise RefundError('crypto_refund_quote_unavailable') from None
        if Decimal(crypto_amount)<=0:raise RefundError('crypto_refund_amount_too_small')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT * FROM refunds WHERE refund_no=?',(refund_no,)).fetchone()
            if prior:
                if prior['out_trade_no']!=order['out_trade_no'] or prior['amount']!=amount or prior['reason']!=reason.strip() or prior['crypto_refund_to']!=refund_to:raise RefundError('refund_conflict')
                return dict(prior)
            current=db.execute('SELECT * FROM orders WHERE out_trade_no=?',(order['out_trade_no'],)).fetchone()
            if not current or current['status'] not in {'PAID','PARTIALLY_REFUNDED'}:raise RefundError('refund_in_progress')
            if current['refunded_amount']!=order['refunded_amount']:raise RefundError('refund_conflict')
            observed=db.execute('SELECT amount FROM crypto_observations WHERE network=? AND out_trade_no=?',(current['crypto_network'],current['out_trade_no'])).fetchone()
            completed=db.execute("SELECT crypto_amount FROM refunds WHERE out_trade_no=? AND status='SUCCESS'",(current['out_trade_no'],)).fetchall()
            ceiling=min(Decimal(current['crypto_amount']),Decimal(observed['amount'])) if observed else Decimal(current['crypto_amount'])
            if sum((Decimal(item['crypto_amount']) for item in completed),Decimal('0'))+Decimal(crypto_amount)>ceiling:raise RefundError('crypto_refund_exceeds_received')
            if hasattr(self.store,'operations'):
                policy=self.store.operations.rules.read(db)
                owner=db.execute('SELECT role FROM users WHERE id=?',(current['user_id'],)).fetchone()
                if owner and owner['role']!='admin' and policy['deposit_enabled'] and self.store.operations.balance(db,current['user_id'],'deposit')<cents(policy['deposit_minimum']):
                    raise RefundError('deposit_required')
                try:self.store.operations.reserve_refund(db,dict(current),refund_no,amount)
                except ValueError as exc:raise RefundError(str(exc)) from None
            db.execute("INSERT INTO refunds(refund_no,out_trade_no,amount,reason,actor,status,created_at,crypto_network,crypto_asset,crypto_amount,crypto_refund_to) VALUES (?,?,?,?,?,'MANUAL_PENDING',?,?,?,?,?)",
                       (refund_no,order['out_trade_no'],amount,reason.strip(),actor,now,order['crypto_network'],order['crypto_asset'],crypto_amount,refund_to))
            db.execute("UPDATE orders SET status='REFUNDING',active_refund_no=?,provider_status='manual_crypto_refund_pending' WHERE out_trade_no=?",(refund_no,order['out_trade_no']))
        self.trace('refund.requested','crypto_refund_transfer_required',{'refund_no':refund_no,'network':order['crypto_network'],'asset':order['crypto_asset'],'crypto_amount':crypto_amount},order['out_trade_no'],actor)
        return self.get(refund_no)

    def record_crypto_transfer(self,number,refund_no,payload,actor):
        if not isinstance(payload,dict) or set(payload)!={'txid','transfer_sent'} or payload.get('transfer_sent') is not True:
            raise RefundError('invalid_crypto_refund_evidence')
        txid=payload['txid']
        def pending(db):
            order=db.execute('SELECT * FROM orders WHERE out_trade_no=?',(number,)).fetchone()
            record=db.execute('SELECT * FROM refunds WHERE refund_no=? AND out_trade_no=?',(refund_no,number)).fetchone()
            if not order or not record:raise RefundError('refund_not_found')
            if record['status']=='SUCCESS':
                if record['crypto_txid']==txid:return dict(order),dict(record)
                raise RefundError('refund_conflict')
            if record['status']!='MANUAL_PENDING' or order['status']!='REFUNDING' or order['active_refund_no']!=refund_no:
                raise RefundError('refund_not_manual_pending')
            return dict(order),dict(record)
        with self.store.connect() as db:order,record=pending(db)
        if record['status']=='SUCCESS':return record
        spec=ASSET_BY_CODE.get(f"{order['crypto_network']}_{order['crypto_asset']}")
        if not spec:raise RefundError('crypto_refund_network_mismatch')
        # Read the real chain without holding SQLite's global writer lock.
        try:evidence=verify_transfer(self.store.crypto_networks,spec,txid,record['crypto_refund_to'],record['crypto_amount'])
        except Exception as exc:raise RefundError(str(exc) or 'crypto_refund_unverified') from None
        if evidence is None:raise RefundError('crypto_refund_tx_not_found')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');current_order,current=pending(db)
            if current['status']=='SUCCESS':return current
            if any(current[key]!=record[key] for key in ('amount','crypto_refund_to','crypto_amount')) or any(current_order[key]!=order[key] for key in ('crypto_network','crypto_asset','refunded_amount')):
                raise RefundError('refund_conflict')
            refund_to=record['crypto_refund_to']
            try:db.execute("UPDATE refunds SET status='SUCCESS',crypto_txid=?,manual_reference=?,confirmed_at=?,locked_until=0,next_query=0 WHERE refund_no=? AND status='MANUAL_PENDING'",(txid,f"crypto:{spec.network}:{spec.asset}:{record['crypto_amount']}:{refund_to}:{txid}",int(time.time()),refund_no))
            except Exception as exc:
                if 'UNIQUE' in str(exc):raise RefundError('crypto_refund_txid_used') from None
                raise
            updated=dict(db.execute('SELECT * FROM refunds WHERE refund_no=?',(refund_no,)).fetchone())
            self.store.operations.finish_refund(db,order,updated,'SUCCESS')
            total=Decimal(order['refunded_amount'])+Decimal(record['amount'])
            if total>Decimal(order['amount']):raise RefundError('refund_exceeds_remaining')
            order_status='REFUNDED' if total==Decimal(order['amount']) else 'PARTIALLY_REFUNDED'
            db.execute('UPDATE orders SET status=?,refunded_amount=?,active_refund_no=NULL,provider_status=? WHERE out_trade_no=?',(order_status,format(total,'.2f'),f"manual_crypto_refund:{txid}",number))
        self.trace('refund.success','crypto_refund_evidence_recorded',{'refund_no':refund_no,'txid':txid,'network':spec.network,'asset':spec.asset,'crypto_amount':record['crypto_amount'],'refund_to':refund_to},number,actor)
        return self.get(refund_no)

    def cancel_crypto_reservation(self,number,refund_no,actor):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            order=db.execute('SELECT * FROM orders WHERE out_trade_no=?',(number,)).fetchone()
            record=db.execute('SELECT * FROM refunds WHERE refund_no=? AND out_trade_no=?',(refund_no,number)).fetchone()
            if not order or not record:raise RefundError('refund_not_found')
            if record['status']=='FAILED':return dict(record)
            if record['status']!='MANUAL_PENDING' or order['status']!='REFUNDING' or order['active_refund_no']!=refund_no:
                raise RefundError('refund_not_manual_pending')
            updated={**dict(record),'error':'manual_transfer_not_sent'}
            self.store.operations.finish_refund(db,dict(order),updated,'FAILED')
            db.execute("UPDATE refunds SET status='FAILED',error='manual_transfer_not_sent',locked_until=0,next_query=0 WHERE refund_no=?",(refund_no,))
            status='PARTIALLY_REFUNDED' if Decimal(order['refunded_amount'])>0 else 'PAID'
            db.execute("UPDATE orders SET status=?,active_refund_no=NULL,provider_status='manual_crypto_refund_cancelled' WHERE out_trade_no=?",(status,number))
        self.trace('refund.failed','crypto_refund_reservation_cancelled',{'refund_no':refund_no},number,actor,False)
        return self.get(refund_no)

    def _settle_wechat(self, record, order, result):
        """WeChat Pay: SUCCESS confirms; PROCESSING and ABNORMAL stay reserved and are queried or handled by hand; CLOSED ends it."""
        status = result.get("status")
        if status == "SUCCESS":
            if result.get("out_refund_no") != record["refund_no"] or result.get("amount_refund") != cents(record["amount"]) or (order["trade_no"] and result.get("transaction_id") != order["trade_no"]):
                raise ValueError("refund_mismatch")
            self._finish(record, "SUCCESS")
        elif status == "CLOSED":
            self._finish(record, "FAILED", "refund_provider_error")
        else:
            self._finish(record, "UNKNOWN", "refund_processing" if status == "PROCESSING" else "refund_abnormal")

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
            if hasattr(self.store,'operations'):self.store.operations.finish_refund(db,order,record,status)
            total = Decimal(order["refunded_amount"])
            if status == "SUCCESS": total += Decimal(record["amount"])
            if total > Decimal(order["amount"]): raise RefundError("refund_exceeds_remaining")
            order_status = "REFUNDING" if status == "UNKNOWN" else "REFUNDED" if total == Decimal(order["amount"]) else "PARTIALLY_REFUNDED" if total > 0 else "PAID"
            db.execute("UPDATE refunds SET status=?,error=?,confirmed_at=?,locked_until=0,next_query=? WHERE refund_no=?", (status,error,now if status=="SUCCESS" else None,now+60,record["refund_no"]))
            db.execute("UPDATE orders SET status=?,refunded_amount=?,active_refund_no=? WHERE out_trade_no=?", (order_status,format(total,".2f"), record["refund_no"] if status=="UNKNOWN" else None,record["out_trade_no"]))
        self.trace("refund."+status.lower(), "refund_"+status.lower(), {"refund_no":record["refund_no"],"amount":record["amount"],"error":error},record["out_trade_no"],order["user_id"],status=="SUCCESS")

    def sync(self, refund_no):
        record = self.get(refund_no)
        if not record or record["status"] in {"SUCCESS","FAILED","MANUAL_PENDING"}: return record
        now=int(time.time())
        with self.store.connect() as db:
            claimed = db.execute("UPDATE refunds SET locked_until=? WHERE refund_no=? AND locked_until<=? AND status IN ('PENDING','UNKNOWN')", (now+60,refund_no,now)).rowcount
        if not claimed: return self.get(refund_no)
        try:
            order=self.store.order(record["out_trade_no"])
            if order["channel"] == "wechat":
                result=self.wechat_factory(self.channels.payment_config(order)).query_refund(refund_no)
                self.trace("refund.query", "wechat_refund_query_received", result, order["out_trade_no"], order["user_id"], result["status"] == "SUCCESS")
                self._settle_wechat(record, order, result)
                return self.get(refund_no)
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
