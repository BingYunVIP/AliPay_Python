"""Read-only scanners for ByPay's supported Crypto networks.

Adapters emit a single normalized transfer observation. The worker commits a
cursor only after a complete bounded batch succeeds; final payment is applied
only after canonical inclusion, exact recipient/asset/amount and confirmations
are rechecked.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from contextlib import contextmanager
from decimal import Decimal
from urllib.parse import quote

import httpx

from .payment_terms import matches
from .crypto import ASSET_BY_CODE, CryptoGateway, SOLANA_MAX_TRANSACTION_VERSION, _base58_decode, rpc_http_endpoint

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _tron_payload(address: str) -> bytes:
    raw = _base58_decode(address)
    if len(raw) != 25 or raw[0] != 0x41 or hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] != raw[21:]:
        raise ValueError("invalid_tron_address")
    return raw[:21]


def _base58_encode(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = alphabet[remainder] + encoded
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + encoded


def _tron_address_from_topic(value: str) -> str:
    body = bytes.fromhex(value.removeprefix("0x")[-40:])
    payload = b"\x41" + body
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return _base58_encode(payload + checksum)


def _evm_address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


def _topic_address(value: str) -> str:
    return "0x" + value.removeprefix("0x")[-40:].lower()


def _json_get(url, *, params=None, headers=None, timeout=10):
    response = httpx.get(url, params=params, headers=headers or {}, timeout=timeout, follow_redirects=False, trust_env=False)
    response.raise_for_status()
    return response.json()


def _json_post(url, payload, *, headers=None, timeout=10):
    response = httpx.post(url, json=payload, headers=headers or {}, timeout=timeout, follow_redirects=False, trust_env=False)
    response.raise_for_status()
    return response.json()


def _amount(raw: int, decimals: int) -> Decimal:
    return Decimal(raw).scaleb(-decimals)


def _event_index(spec, index) -> str:
    return f"{spec.code}:{index}"


def _solana_token_transfers(tx,spec):
    """Return exact parsed SPL transfer instructions to their token owners."""
    meta=tx.get('meta') or {};transaction=tx.get('transaction') or {};message=transaction.get('message') or {}
    keys=message.get('accountKeys') or [];pubkeys=[]
    for item in keys:pubkeys.append(item.get('pubkey','') if isinstance(item,dict) else str(item))
    token_accounts={}
    for row in [*(meta.get('preTokenBalances') or []),*(meta.get('postTokenBalances') or [])]:
        if int(row.get('uiTokenAmount',{}).get('decimals',-1))!=spec.decimals:
            if row.get('mint')==spec.contract:raise RuntimeError('crypto_decimals_mismatch')
            continue
        index=int(row.get('accountIndex',-1))
        if index>=0 and index<len(pubkeys):token_accounts[pubkeys[index]]=(row.get('owner',''),row.get('mint',''))
    instructions=list(message.get('instructions') or [])
    for group in meta.get('innerInstructions') or []:instructions.extend(group.get('instructions') or [])
    results=[]
    for index,instruction in enumerate(instructions):
        parsed=instruction.get('parsed') or {};kind=parsed.get('type','');info=parsed.get('info') or {}
        if kind not in {'transfer','transferChecked'}:continue
        program=instruction.get('program','')
        if program not in {'spl-token','spl-token-2022'}:continue
        destination=info.get('destination','');source=info.get('source','')
        if kind=='transferChecked':
            mint=info.get('mint','');amount_info=info.get('tokenAmount') or {}
            if int(amount_info.get('decimals',-1))!=spec.decimals:continue
            raw=int(amount_info.get('amount','0'))
        else:
            destination_meta=token_accounts.get(destination,('', ''))
            source_meta=token_accounts.get(source,('', ''))
            mint=destination_meta[1] if destination_meta[1]==spec.contract else source_meta[1]
            raw=int(info.get('amount','0'))
        if mint!=spec.contract or raw<=0:continue
        owner=token_accounts.get(destination,('',mint))[0]
        source_owner=token_accounts.get(source,('',mint))[0]
        if owner:results.append({'instruction_index':index,'destination':owner,'source':source_owner,'amount':_amount(raw,spec.decimals)})
    return results


class CryptoScanner:
    def __init__(self, store, channels, networks, trace):
        self.store, self.channels, self.networks, self.trace = store, channels, networks, trace
        self.gateway = CryptoGateway(store, networks)
        self.offset = 0
        self._lease = None

    @contextmanager
    def _leased(self, identity, version):
        name = f'crypto-scan:{identity}:{version}'
        token, now = secrets.token_hex(16), int(time.time())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO task_runs(name) VALUES (?)', (name,))
            claimed = db.execute('UPDATE task_runs SET status=?,locked_until=?,started_at=? WHERE name=? AND locked_until<=?',
                                 (token, now+120, now, name, now)).rowcount
        if not claimed:
            yield False
            return
        self._lease = (name, token)
        try:
            yield True
        finally:
            with self.store.connect() as db:
                db.execute("UPDATE task_runs SET locked_until=0,finished_at=?,status='idle' WHERE name=? AND status=?",
                           (int(time.time()), name, token))
            self._lease = None

    def _renew(self):
        if not self._lease:return
        now = int(time.time())
        with self.store.connect() as db:
            if not db.execute('UPDATE task_runs SET locked_until=? WHERE name=? AND status=? AND locked_until>?',
                              (now+120, *self._lease, now)).rowcount:
                raise RuntimeError('crypto_scan_lease_lost')

    def tick(self):
        # Scan immutable order configurations even when a card is disabled or
        # edited. Disabling only stops new orders; receipts must still settle.
        with self.store.connect() as db:
            rows=[dict(row) for row in db.execute("SELECT * FROM orders WHERE channel='crypto' AND status IN ('CREATED','CONFIRMING') AND paid_at IS NULL AND (status='CONFIRMING' OR expires_at+json_extract(payment_payload,'$.lookback_hours')*3600>?) ORDER BY created_at",(int(time.time()),))]
        groups={}
        for order in rows:
            key=(order['payment_config_id'],order['channel_version'])
            groups.setdefault(key,[]).append(order)
        jobs=list(groups.items())
        if not jobs:return False
        count=min(4,len(jobs));worked=False
        selected=[jobs[(self.offset+index)%len(jobs)] for index in range(count)]
        self.offset=(self.offset+count)%len(jobs)
        for (identity,version),orders in selected:
            try:
                cfg=self.channels.payment_config(orders[0])
                cfg['wallet_addresses']=list(dict.fromkeys(order['crypto_address'] for order in orders))
                cfg['scan_from']=min(json.loads(order['payment_payload']).get('scan_from',0) for order in orders)
                net=self.networks.get(cfg['network'])
                if not net['rpc_url'] or not net['revision'] or net['revision']!=net['check_version']:continue
                spec=ASSET_BY_CODE[f"{cfg['network']}_{cfg['asset']}"]
                with self._leased(identity,version) as claimed:
                    if claimed:worked=self._scan(spec,cfg,identity,net) or worked
            except Exception as exc:
                self.trace('crypto.scan','crypto_scan_failed',{'config_id':identity,'exception_type':type(exc).__name__},accepted=False)
        return worked

    def _cursor(self, key):
        with self.store.connect() as db:
            row = db.execute("SELECT height FROM crypto_block_cursors WHERE network=?", (key,)).fetchone()
        return row["height"] if row else None

    def _save_cursor(self, key, height, rewind=False):
        self._renew()
        with self.store.connect() as db:
            value = 'excluded.height' if rewind else 'MAX(crypto_block_cursors.height,excluded.height)'
            db.execute("INSERT INTO crypto_block_cursors(network,height,updated_at) VALUES (?,?,?) "
                       "ON CONFLICT(network) DO UPDATE SET height=" + value + ",updated_at=excluded.updated_at",
                       (key, int(height), int(time.time())))

    def _scan(self, spec, cfg, config_id, net):
        method = {"evm": self._scan_evm, "tron": self._scan_tron, "aptos": self._scan_aptos, "solana": self._scan_solana}[spec.address_kind]
        worked = method(spec, cfg, config_id, net)
        self._confirm(spec, config_id, net)
        return worked

    def _pending(self, config_id):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM orders WHERE channel='crypto' AND payment_config_id=? "
                "AND status IN ('CREATED','CONFIRMING') AND paid_at IS NULL", (config_id,))]

    def _match(self, spec, config_id, observation):
        self._renew()
        amount = format(observation["amount"], "f")
        for order in self._pending(config_id):
            payload = json.loads(order.get("payment_payload") or "{}")
            if payload.get("asset_code") != spec.code:
                continue
            expected = str(payload.get("address", ""))
            actual = str(observation["address"])
            if spec.address_kind == "evm":
                expected, actual = expected.lower(), actual.lower()
            if expected != actual or not matches(amount,payload.get("crypto_amount","0"),payload.get("match_mode","exact")):
                continue
            accepted = self.store.apply_crypto_observation({
                **observation, "out_trade_no": order["out_trade_no"], "network": spec.network,
                "asset": spec.asset, "amount": amount,
            }, int(payload.get("confirmations", 12)))
            if accepted:
                self.trace("crypto.confirm", "crypto_transfer_observed", {
                    "txid": observation["txid"], "asset": spec.code, "block": observation["block_height"]},
                    order["out_trade_no"], order["user_id"])
                return True
        return False

    def _scan_evm(self, spec, cfg, config_id, net):
        rpc, api_key = net["rpc_url"], net.get("api_key", "")
        latest = int(CryptoGateway._rpc(rpc, "eth_blockNumber", [], api_key), 16)
        key = f"{spec.code}:{config_id}:{cfg.get('channel_version',0)}"
        cursor = self._cursor(key)
        start = max(0, cfg.get("scan_from", latest - self.store.crypto_policy.read()['block_max_gap'])) if cursor is None else max(0, cursor - 2)
        end = min(latest, start + min(200,self.store.crypto_policy.read()['block_max_gap']) - 1)
        if start > end:
            return False
        worked = False
        topics = {_evm_address_topic(address): address for address in cfg["wallet_addresses"]}
        blocks = {}
        for address_topic, address in topics.items():
            self._renew()
            logs = CryptoGateway._rpc(rpc, "eth_getLogs", [{
                "fromBlock": hex(start), "toBlock": hex(end), "address": spec.contract,
                "topics": [TRANSFER_TOPIC, None, address_topic],
            }], api_key)
            for log in logs:
                event_topics = log.get("topics") or []
                if len(event_topics) < 3 or ("0x" + event_topics[0].removeprefix("0x")).lower() != TRANSFER_TOPIC:
                    raise RuntimeError("rpc_transfer_log_invalid")
                height = int(log["blockNumber"], 16)
                if height not in blocks:
                    blocks[height] = CryptoGateway._rpc(rpc, "eth_getBlockByNumber", [hex(height), False], api_key)
                block = blocks[height]
                if not block or not log.get("blockHash") or block.get("hash", "").lower() != log["blockHash"].lower():
                    raise RuntimeError("rpc_noncanonical_log")
                observation = {
                    "txid": log["transactionHash"], "log_index": _event_index(spec, int(log["logIndex"], 16)),
                    "block_height": height, "block_hash": log["blockHash"],
                    "timestamp": int(block["timestamp"], 16),
                    "amount": _amount(int(log.get("data", "0x0"), 16), spec.decimals),
                    "address": address, "from_address": _topic_address(event_topics[1]),
                }
                worked = self._match(spec, config_id, observation) or worked
        self._save_cursor(key, end)
        return worked

    def _scan_tron(self, spec, cfg, config_id, net):
        rpc = rpc_http_endpoint("TRON", net["rpc_url"])
        headers = {"TRON-PRO-API-KEY": net["api_key"]} if net.get("api_key") else {}
        head = _json_post(rpc + "/wallet/getnowblock", {}, headers=headers)
        latest = int(head["block_header"]["raw_data"]["number"])
        key = f"{spec.code}:{config_id}:{cfg.get('channel_version',0)}"
        cursor = self._cursor(key)
        start = max(0, cfg.get("scan_from", latest - 200)) if cursor is None else max(0, cursor - 2)
        end = min(latest, start + min(10,self.store.crypto_policy.read()['block_max_gap']) - 1)
        if start > end:
            return False
        contract = _tron_payload(spec.contract).hex().upper()
        wallets = {_tron_payload(address).hex().upper(): address for address in cfg["wallet_addresses"]}
        worked = False
        for height in range(start, end + 1):
            self._renew()
            block = _json_post(rpc + "/wallet/getblockbynum", {"num": height, "visible": True}, headers=headers)
            if int(block.get("block_header", {}).get("raw_data", {}).get("number", -1)) != height:
                raise RuntimeError("rpc_block_height_mismatch")
            block_hash = str(block.get("blockID", ""))
            stamp = int(block["block_header"]["raw_data"]["timestamp"]) // 1000
            for tx in block.get("transactions", []):
                self._renew()
                txid = tx.get("txID", "")
                if not txid:
                    raise RuntimeError("rpc_transaction_missing_hash")
                info = _json_post(rpc + "/wallet/gettransactioninfobyid", {"value": txid}, headers=headers)
                if not info:raise RuntimeError("rpc_transaction_missing")
                if int(info.get("blockNumber", -1)) != height:
                    raise RuntimeError("rpc_transaction_block_mismatch")
                if info.get("receipt", {}).get("result") != "SUCCESS":
                    continue
                for index, log in enumerate(info.get("log", [])):
                    log_address = str(log.get("address", "")).removeprefix("0x").upper()
                    if log_address not in {contract, contract[-40:]}:
                        continue
                    topics = log.get("topics") or []
                    if len(topics) < 3 or ("0x" + topics[0].removeprefix("0x")).lower() != TRANSFER_TOPIC:
                        continue
                    recipient_hex = "41" + topics[2][-40:].upper()
                    recipient = wallets.get(recipient_hex)
                    if not recipient:
                        continue
                    observation = {
                        "txid": txid, "log_index": _event_index(spec, index), "block_height": height,
                        "block_hash": block_hash, "timestamp": stamp,
                        "amount": _amount(int(log.get("data", "0x0"), 16), spec.decimals),
                        "address": recipient, "from_address": _tron_address_from_topic(topics[1]),
                    }
                    worked = self._match(spec, config_id, observation) or worked
        self._save_cursor(key, end)
        return worked

    def _aptos_store_resource(self, rpc, address, resource, version, api_key):
        url = rpc.rstrip("/") + "/v1/accounts/" + address + "/resource/" + quote(resource, safe="")
        return _json_get(url, params={"ledger_version": version},
                         headers={"Authorization": "Bearer " + api_key} if api_key else {}, timeout=10).get("data", {})

    def _scan_aptos(self, spec, cfg, config_id, net):
        rpc, api_key = net["rpc_url"].rstrip("/"), net.get("api_key", "")
        headers = {"Authorization": "Bearer " + api_key} if api_key else {}
        ledger = _json_get(rpc + "/v1", headers=headers, timeout=10)
        latest = int(ledger["ledger_version"])
        key = f"{spec.code}:{config_id}:{cfg.get('channel_version',0)}"
        cursor = self._cursor(key)
        start = max(0, cfg.get("scan_from", latest - 1000)) if cursor is None else max(0, cursor - 2)
        limit = min(100, self.store.crypto_policy.read()['block_max_gap'], latest - start + 1)
        if limit <= 0:
            return False
        items = _json_get(rpc + "/v1/transactions", params={"start": start, "limit": limit}, headers=headers, timeout=15)
        owner_by_store, metadata_by_store = {}, {}
        for tx in items:
            self._renew()
            for change in tx.get("changes", []):
                data = change.get("data", {})
                resource = data.get("type", "")
                address = str(change.get("address", "")).lower()
                fields = data.get("data", {})
                if resource == "0x1::object::ObjectCore":
                    owner_by_store[address] = str(fields.get("owner", "")).lower()
                elif resource == "0x1::fungible_asset::FungibleStore":
                    metadata = fields.get("metadata", "")
                    metadata_by_store[address] = str(metadata.get("inner", "") if isinstance(metadata, dict) else metadata).lower()
        worked = False
        token_metadata = spec.contract.lower().removeprefix("0x").zfill(64)
        for tx in items:
            if tx.get("success") is not True:
                continue
            self._renew()
            txid = tx.get("hash", "")
            version = int(tx.get("version", 0))
            timestamp = int(tx.get("timestamp", "0")) // 1_000_000
            if not txid or not timestamp:
                raise RuntimeError("aptos_transaction_metadata_missing")
            for index, event in enumerate(tx.get("events", [])):
                event_type = event.get("type", "")
                if event_type not in {"0x1::fungible_asset::Deposit","0x1::fungible_asset::DepositEvent"}:
                    continue
                data = event.get("data", {})
                store_address = str(data.get("store", "")).lower()
                if not store_address:
                    raise RuntimeError("aptos_deposit_store_missing")
                metadata = metadata_by_store.get(store_address, "")
                owner = owner_by_store.get(store_address, "")
                if not metadata or not owner:
                    store_resource = self._aptos_store_resource(rpc, store_address, "0x1::fungible_asset::FungibleStore", version, api_key)
                    metadata_value = store_resource.get("metadata", "")
                    metadata = str(metadata_value.get("inner", "") if isinstance(metadata_value, dict) else metadata_value).lower()
                    object_core = self._aptos_store_resource(rpc, store_address, "0x1::object::ObjectCore", version, api_key)
                    owner = str(object_core.get("owner", "")).lower()
                if metadata.removeprefix("0x").zfill(64) != token_metadata:
                    continue
                address = next((item for item in cfg["wallet_addresses"]
                                if item.lower().removeprefix("0x").zfill(64) == owner.removeprefix("0x").zfill(64)), "")
                if not address:
                    continue
                observation = {
                    "txid": txid, "log_index": _event_index(spec, index), "block_height": version,
                    "block_hash": str(version), "timestamp": timestamp,
                    "amount": _amount(int(data.get("amount", "0")), spec.decimals),
                    "address": address, "from_address": "",
                }
                worked = self._match(spec, config_id, observation) or worked
        if not items:raise RuntimeError("aptos_transaction_batch_empty")
        versions=[int(item["version"]) for item in items]
        if versions!=list(range(start,start+len(versions))):raise RuntimeError("aptos_transaction_gap")
        self._save_cursor(key, versions[-1])
        return worked

    def _scan_solana(self, spec, cfg, config_id, net):
        rpc, api_key = net["rpc_url"], net.get("api_key", "")
        latest = int(CryptoGateway._rpc(rpc, "getSlot", [{"commitment": "finalized"}], api_key))
        key = f"{spec.code}:{config_id}:{cfg.get('channel_version',0)}"
        cursor = self._cursor(key)
        start = max(0, cfg.get("scan_from", latest - 120)) if cursor is None else max(0, cursor - 2)
        end = min(latest, start + min(10,self.store.crypto_policy.read()['block_max_gap']) - 1)
        if start > end:
            return False
        worked = False
        slots=CryptoGateway._rpc(rpc,"getBlocks",[start,end,{"commitment":"finalized"}],api_key)
        for slot in slots:
            self._renew()
            block = CryptoGateway._rpc(rpc, "getBlock", [slot, {
                "encoding": "jsonParsed", "maxSupportedTransactionVersion": SOLANA_MAX_TRANSACTION_VERSION,
                "transactionDetails": "full", "rewards": False,"commitment":"finalized",
            }], api_key)
            if not block:raise RuntimeError("solana_block_missing")
            timestamp = block.get("blockTime")
            if timestamp is None and block.get("transactions"):
                raise RuntimeError("solana_block_time_missing")
            block_hash = block.get("blockhash", "")
            for tx in block.get("transactions", []):
                meta = tx.get("meta")
                if meta is None:
                    raise RuntimeError("solana_transaction_meta_missing")
                if meta.get("err") is not None:
                    continue
                signatures = (tx.get("transaction") or {}).get("signatures") or []
                if not signatures:
                    raise RuntimeError("solana_transaction_signature_missing")
                for transfer in _solana_token_transfers(tx,spec):
                    owner=transfer['destination']
                    if owner not in cfg["wallet_addresses"]:continue
                    observation = {
                        "txid": signatures[0], "log_index": _event_index(spec, transfer['instruction_index']),
                        "block_height": slot, "block_hash": block_hash, "timestamp": int(timestamp),
                        "amount": transfer['amount'], "address": owner, "from_address": transfer['source'],
                    }
                    worked = self._match(spec, config_id, observation) or worked
        self._save_cursor(key, end)
        return worked

    def _latest(self, spec, net):
        rpc, api_key = net["rpc_url"], net.get("api_key", "")
        if spec.address_kind == "evm":
            return int(CryptoGateway._rpc(rpc, "eth_blockNumber", [], api_key), 16)
        if spec.address_kind == "aptos":
            return int(_json_get(rpc.rstrip("/") + "/v1", headers={"Authorization": "Bearer " + api_key} if api_key else {})["ledger_version"])
        if spec.address_kind == "solana":
            return int(CryptoGateway._rpc(rpc, "getSlot", [{"commitment": "finalized"}], api_key))
        block = _json_post(rpc_http_endpoint("TRON", rpc).rstrip("/") + "/walletsolidity/getnowblock", {}, headers={"TRON-PRO-API-KEY": api_key} if api_key else {})
        return int(block["block_header"]["raw_data"]["number"])

    def _evm_canonical(self, spec, order, net, observed):
        rpc, api_key = net["rpc_url"], net.get("api_key", "")
        block = CryptoGateway._rpc(rpc, "eth_getBlockByNumber", [hex(order["crypto_block"]), False], api_key)
        receipt = CryptoGateway._rpc(rpc, "eth_getTransactionReceipt", [order["crypto_txid"]], api_key)
        if not block or not receipt:
            return None
        if receipt.get("status", "0x0") in {"0x0", "0x00"}:
            return False
        if block.get("hash", "").lower() != observed["block_hash"].lower() or receipt.get("blockHash", "").lower() != observed["block_hash"].lower():
            return False
        if int(receipt.get("blockNumber", "0x0"), 16) != int(order["crypto_block"]):
            return False
        try: wanted_index = int(observed["log_index"].split(":", 1)[1])
        except (IndexError, ValueError): return False
        payload = json.loads(order["payment_payload"] or "{}")
        target = str(payload.get("address", "")).lower()
        expected_raw = int(Decimal(order["crypto_amount"]) * (Decimal(10) ** spec.decimals))
        for log in receipt.get("logs", []):
            topics = log.get("topics") or []
            if int(log.get("logIndex", "0x0"), 16) != wanted_index or str(log.get("address", "")).lower() != spec.contract.lower():
                continue
            if len(topics) < 3 or topics[0].lower() != TRANSFER_TOPIC or _topic_address(topics[2]) != target:
                continue
            if matches(_amount(int(log.get('data','0x0'),16),spec.decimals),order['crypto_amount'],order['match_mode']):
                return True
        return False

    def _tron_canonical(self, spec, order, net, observed):
        rpc = rpc_http_endpoint("TRON", net["rpc_url"])
        headers = {"TRON-PRO-API-KEY": net["api_key"]} if net.get("api_key") else {}
        info = _json_post(rpc + "/wallet/gettransactioninfobyid", {"value": order["crypto_txid"]}, headers=headers)
        if not info:
            return None
        if info.get("receipt", {}).get("result") != "SUCCESS" or int(info.get("blockNumber", -1)) != int(order["crypto_block"]):
            return False
        block = _json_post(rpc + "/wallet/getblockbynum", {"num": int(order["crypto_block"]), "visible": True}, headers=headers)
        if not block:
            return None
        if str(block.get("blockID", "")).lower() != observed["block_hash"].lower():
            return False
        contract = _tron_payload(spec.contract).hex().upper()
        recipient = _tron_payload(order["crypto_address"]).hex().upper()
        expected = int(Decimal(order["crypto_amount"]) * (Decimal(10) ** spec.decimals))
        for index, log in enumerate(info.get("log", [])):
            if _event_index(spec, index) != observed["log_index"]:
                continue
            address = str(log.get("address", "")).removeprefix("0x").upper()
            topics = log.get("topics") or []
            if address not in {contract, contract[-40:]} or len(topics) < 3 or ("0x" + topics[0].removeprefix("0x")).lower() != TRANSFER_TOPIC:
                continue
            if "41" + topics[2][-40:].upper() == recipient and matches(_amount(int(log.get('data','0x0'),16),spec.decimals),order['crypto_amount'],order['match_mode']):
                return True
        return False

    def _solana_canonical(self, spec, order, net):
        rpc, api_key = net["rpc_url"], net.get("api_key", "")
        tx = CryptoGateway._rpc(rpc, "getTransaction", [order["crypto_txid"], {
            "encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": SOLANA_MAX_TRANSACTION_VERSION,
        }], api_key)
        if not tx:
            return None
        if (tx.get("meta") or {}).get("err") is not None or int(tx.get("slot", -1)) != int(order["crypto_block"]):
            return False
        expected = int(Decimal(order["crypto_amount"]) * (Decimal(10) ** spec.decimals))
        wanted_index=None
        with self.store.connect() as db:
            observation=db.execute('SELECT log_index FROM crypto_observations WHERE network=? AND txid=? AND out_trade_no=?',(order['crypto_network'],order['crypto_txid'],order['out_trade_no'])).fetchone()
        if not observation:return False
        try:wanted_index=int(observation['log_index'].split(':',1)[1])
        except (IndexError,ValueError):return False
        return any(item['instruction_index']==wanted_index and item['destination']==order['crypto_address']
                   and matches(item['amount'],order['crypto_amount'],order['match_mode'])
                   for item in _solana_token_transfers(tx,spec))

    def _aptos_canonical(self, spec, order, net):
        rpc, api_key = net["rpc_url"].rstrip("/"), net.get("api_key", "")
        response=httpx.get(rpc + f"/v1/transactions/by_hash/{order['crypto_txid']}",headers={"Authorization": "Bearer " + api_key} if api_key else {},timeout=10,follow_redirects=False,trust_env=False)
        if response.status_code==404:return None
        response.raise_for_status();tx=response.json()
        if tx.get("success") is not True or int(tx.get("version", -1)) != int(order["crypto_block"]):
            return False
        payload = json.loads(order["payment_payload"] or "{}")
        expected = int(Decimal(order["crypto_amount"]) * (Decimal(10) ** spec.decimals))
        target = payload["address"].lower().removeprefix("0x").zfill(64)
        metadata = spec.contract.lower().removeprefix("0x").zfill(64)
        for event in tx.get("events", []):
            event_type = event.get("type", "")
            if event_type not in {"0x1::fungible_asset::Deposit","0x1::fungible_asset::DepositEvent"}:
                continue
            data = event.get("data", {})
            if not matches(_amount(int(data.get('amount','0')),spec.decimals),order['crypto_amount'],order['match_mode']):
                continue
            store_address = str(data.get("store", "")).lower()
            resources = _json_get(rpc + "/v1/accounts/" + store_address + "/resources",
                                  params={"ledger_version": order["crypto_block"]},
                                  headers={"Authorization": "Bearer " + api_key} if api_key else {})
            core = next((item.get("data", {}) for item in resources if item.get("type") == "0x1::object::ObjectCore"), {})
            store = next((item.get("data", {}) for item in resources if item.get("type") == "0x1::fungible_asset::FungibleStore"), {})
            owner = str(core.get("owner", "")).lower().removeprefix("0x").zfill(64)
            meta = store.get("metadata", "")
            meta = str(meta.get("inner", "") if isinstance(meta, dict) else meta).lower().removeprefix("0x").zfill(64)
            if owner == target and meta == metadata:
                return True
        return False

    def _confirm(self, spec, config_id, net):
        pending = self._pending(config_id)
        if not pending:
            return
        latest = self._latest(spec, net)
        required = int(net["confirmations"])
        for order in pending:
            self._renew()
            if order["status"] != "CONFIRMING" or not order["crypto_txid"]:
                continue
            if order["crypto_network"]!=spec.network or order["crypto_asset"]!=spec.asset:continue
            required=int(json.loads(order["payment_payload"]).get("confirmations",net["confirmations"]))
            depth = max(0, latest - int(order["crypto_block"]) + 1)
            with self.store.connect() as db:
                observed = db.execute("SELECT log_index,block_hash FROM crypto_observations WHERE network=? AND txid=? AND out_trade_no=?",
                                      (order["crypto_network"],order["crypto_txid"], order["out_trade_no"])).fetchone()
            if not observed:
                self.store.rollback_crypto_observation(order["out_trade_no"], order["crypto_txid"])
                continue
            canonical = {
                "evm": lambda: self._evm_canonical(spec, order, net, observed),
                "tron": lambda: self._tron_canonical(spec, order, net, observed),
                "aptos": lambda: self._aptos_canonical(spec, order, net),
                "solana": lambda: self._solana_canonical(spec, order, net),
            }[spec.address_kind]()
            if canonical is None:
                continue
            if not canonical:
                # Solana and Aptos scanners only ingest finalized ledger data;
                # EVM/TRON checks compare the stored receipt against current RPC state.
                self.store.rollback_crypto_observation(order["out_trade_no"], order["crypto_txid"], observed["log_index"])
                key=f"{spec.code}:{config_id}:{order['channel_version']}"
                self._save_cursor(key,max(0,int(order["crypto_block"])-1),rewind=True)
                continue
            self._renew()
            with self.store.connect() as db:
                db.execute("UPDATE orders SET crypto_confirmations=?,provider_status=? WHERE out_trade_no=? AND status='CONFIRMING'",
                           (depth, f"{depth}/{required} confirmations", order["out_trade_no"]))
            if depth >= required and self.store.confirm_crypto_payment(order["out_trade_no"], order["crypto_txid"], order["crypto_block"], depth):
                self.trace("crypto.confirm", "crypto_payment_confirmed", {
                    "txid": order["crypto_txid"], "confirmations": depth}, order["out_trade_no"], order["user_id"])
