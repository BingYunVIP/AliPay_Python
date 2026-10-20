"""Self-contained Crypto payment primitives.

The module intentionally keeps the provider boundary inside ByPay.  It does not
start or call BEpusdt.  Network adapters can be extended without a plugin
loader; every supported asset is declared in this file and checked before use.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import os
from functools import lru_cache
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote, urlsplit
from cryptography.fernet import InvalidToken

import httpx


@dataclass(frozen=True)
class AssetSpec:
    code: str
    network: str
    asset: str
    decimals: int
    contract: str
    explorer: str
    address_kind: str
    enabled: bool = True
    note: str = ""
    issuer: str = ""
    identity: str = ""
    source: str = ""


# TRON_USDC is deliberately absent. Entries identify the actual contract or
# Aptos metadata object, including bridged representations with familiar names.
ASSETS = (
    AssetSpec("BSC_USDT", "BSC", "USDT", 18, "0x55d398326f99059ff775485246999027b3197955", "https://bscscan.com/tx/{}", "evm", issuer="Binance-Peg", identity="Binance-Peg BSC-USD (USDT)", source="https://bscscan.com/token/0x55d398326f99059ff775485246999027b3197955"),
    AssetSpec("BSC_USDC", "BSC", "USDC", 18, "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", "https://bscscan.com/tx/{}", "evm", issuer="Binance-Peg", identity="Binance-Peg USD Coin (USDC); not Circle-native USDC", source="https://bscscan.com/token/0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"),
    AssetSpec("TRON_USDT", "TRON", "USDT", 6, "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "https://tronscan.org/#/transaction/{}", "tron", issuer="Tether", identity="Tether USD₮ TRC-20", source="https://tronscan.org/#/token20/TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"),
    AssetSpec("APTOS_USDT", "APTOS", "USDT", 6, "0x357b0b74bc833e95a115ad22604854d6b0fca151cecd94111770e5d6ffc9dc2b", "https://explorer.aptoslabs.com/txn/{}", "aptos", issuer="Tether", identity="Tether USDt Aptos Fungible Asset", source="https://explorer.aptoslabs.com/account/0x357b0b74bc833e95a115ad22604854d6b0fca151cecd94111770e5d6ffc9dc2b/resources?network=mainnet"),
    AssetSpec("APTOS_USDC", "APTOS", "USDC", 6, "0xbae207659db88bea0cbead6da0ed00aac12edcdda169e591cd41c94180b46f3b", "https://explorer.aptoslabs.com/txn/{}", "aptos", issuer="Circle", identity="Circle USDC Aptos Fungible Asset", source="https://explorer.aptoslabs.com/account/0xbae207659db88bea0cbead6da0ed00aac12edcdda169e591cd41c94180b46f3b/resources?network=mainnet"),
    AssetSpec("ETH_USDT", "ETH", "USDT", 6, "0xdac17f958d2ee523a2206206994597c13d831ec7", "https://etherscan.io/tx/{}", "evm", issuer="Tether", identity="Tether USD₮ ERC-20", source="https://etherscan.io/token/0xdac17f958d2ee523a2206206994597c13d831ec7"),
    AssetSpec("ETH_USDC", "ETH", "USDC", 6, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "https://etherscan.io/tx/{}", "evm", issuer="Circle", identity="Circle native USDC ERC-20", source="https://etherscan.io/token/0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
    AssetSpec("PLASMA_USDT", "PLASMA", "USDT", 6, "0xb8ce59fc3717ada4c02eadf9682a9e934f625ebb", "https://plasmascan.to/tx/{}", "evm", issuer="USDT0", identity="USDT0 bridged USDT representation", source="https://plasmascan.to/address/0xb8ce59fc3717ada4c02eadf9682a9e934f625ebb"),
    AssetSpec("PLASMA_USDC", "PLASMA", "USDC", 6, "0x2d661C89D812261039AF9764eceaAee884f5F67F", "https://plasmascan.to/tx/{}", "evm", issuer="Circle", identity="Circle native USDC on Plasma", source="https://plasmascan.to/address/0x2d661C89D812261039AF9764eceaAee884f5F67F"),
    AssetSpec("SOL_USDT", "SOL", "USDT", 6, "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "https://solscan.io/tx/{}", "solana", issuer="Tether", identity="Tether USD₮ SPL token", source="https://solscan.io/token/Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"),
    AssetSpec("SOL_USDC", "SOL", "USDC", 6, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "https://solscan.io/tx/{}", "solana", issuer="Circle", identity="Circle native USDC SPL token", source="https://solscan.io/token/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"),
    AssetSpec("ARBITRUM_USDT", "ARBITRUM", "USDT", 6, "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9", "https://arbiscan.io/tx/{}", "evm", issuer="Tether / USDT0", identity="Tether USDT on Arbitrum One", source="https://arbiscan.io/token/0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"),
    AssetSpec("ARBITRUM_USDC", "ARBITRUM", "USDC", 6, "0xaf88d065e77c8cc2239327c5edb3a432268e5831", "https://arbiscan.io/tx/{}", "evm", issuer="Circle", identity="Circle native USDC on Arbitrum One", source="https://arbiscan.io/token/0xaf88d065e77c8cc2239327c5edb3a432268e5831"),
    AssetSpec("POLYGON_USDT", "POLYGON", "USDT", 6, "0xc2132d05d31c914a87c6611c10748aeb04b58e8f", "https://polygonscan.com/tx/{}", "evm", issuer="Tether / USDT0", identity="Tether USD₮ Polygon PoS", source="https://polygonscan.com/token/0xc2132d05d31c914a87c6611c10748aeb04b58e8f"),
    AssetSpec("POLYGON_USDC", "POLYGON", "USDC", 6, "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359", "https://polygonscan.com/tx/{}", "evm", issuer="Circle", identity="Circle native USDC on Polygon PoS", source="https://polygonscan.com/token/0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"),
    AssetSpec("AVAXC_USDT", "AVAXC", "USDT", 6, "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7", "https://snowtrace.io/tx/{}", "evm", issuer="Tether", identity="Tether USD₮ Avalanche C-Chain", source="https://snowtrace.io/token/0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7"),
    AssetSpec("AVAXC_USDC", "AVAXC", "USDC", 6, "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", "https://snowtrace.io/tx/{}", "evm", issuer="Circle", identity="Circle native USDC on Avalanche C-Chain", source="https://snowtrace.io/token/0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"),
)
ASSET_BY_CODE = {item.code: item for item in ASSETS}
NETWORKS = tuple(dict.fromkeys(item.network for item in ASSETS))
ASSET_NAMES = ("USDT", "USDC")


class CryptoError(ValueError):
    def __init__(self, code: str, field: str = "", detail: str = ""):
        super().__init__(code)
        self.code, self.field, self.detail = code, field, detail



class RpcFailure(CryptoError):
    """Safe RPC diagnostics: never reflect upstream bodies, URLs or API keys."""
    def __init__(self, code, *, method="", http_status=None, rpc_code=None, exception_type=None):
        super().__init__(code,"rpc_url")
        self.method,self.http_status,self.rpc_code,self.exception_type=method,http_status,rpc_code,exception_type


def rpc_failure(exc):
    if isinstance(exc,CryptoError):
        result={"provider_code":exc.code,"field":exc.field}
        if isinstance(exc,RpcFailure):
            for name in ("method","http_status","rpc_code","exception_type"):
                if getattr(exc,name) is not None and getattr(exc,name)!="":result[name]=getattr(exc,name)
        return result
    if isinstance(exc,httpx.HTTPStatusError):
        status=exc.response.status_code
        subscription=status==403 and "requires a paid subscription" in exc.response.text.lower()
        code="crypto_rpc_subscription_required" if subscription else "crypto_rpc_access_denied" if status in (401,403) else "crypto_rpc_rate_limited" if status==429 else "crypto_rpc_http_error"
        return {"provider_code":code,"field":"rpc_url","http_status":status}
    if isinstance(exc,httpx.TimeoutException):code="crypto_rpc_timeout"
    elif isinstance(exc,httpx.RequestError):code="crypto_rpc_unreachable"
    elif isinstance(exc,(ValueError,TypeError,KeyError)):code="crypto_rpc_invalid_response"
    else:return {"provider_code":"rpc_probe_failed","field":"rpc_url","exception_type":type(exc).__name__}
    return {"provider_code":code,"field":"rpc_url"}


def asset_for(network: str, asset: str) -> AssetSpec:
    code = f"{str(network).strip().upper()}_{str(asset).strip().upper()}"
    item = ASSET_BY_CODE.get(code)
    if not item:
        raise CryptoError("crypto_asset_unsupported", "asset")
    return item


def asset_registry():
    return [{"code":item.code,"network":item.network,"asset":item.asset,"decimals":item.decimals,
             "contract":item.contract,"explorer":item.explorer,"address_kind":item.address_kind,
             "enabled":item.enabled,"note":item.note,"issuer":item.issuer,"identity":item.identity,
             "source":item.source} for item in ASSETS]


def _addresses(value) -> list[str]:
    if isinstance(value, list): values = value
    elif isinstance(value, str):
        text = value.strip()
        if not text: values = []
        else:
            try: values = json.loads(text) if text.startswith("[") else text.splitlines()
            except json.JSONDecodeError as exc: raise CryptoError("crypto_wallet_list_invalid", "wallet_addresses") from exc
    else: raise CryptoError("crypto_wallet_list_invalid", "wallet_addresses")
    if not isinstance(values,list):raise CryptoError("crypto_wallet_list_invalid","wallet_addresses")
    result=[]
    for value in values:
        if not isinstance(value,str) or not value.strip() or len(value.strip())>256:
            raise CryptoError("crypto_wallet_address_invalid", "wallet_addresses")
        address=value.strip()
        if not address: raise CryptoError("crypto_wallet_address_invalid", "wallet_addresses")
        if address not in result: result.append(address)
    return result


def _base58_decode(text: str) -> bytes:
    alphabet="123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number=0
    for char in text:
        if char not in alphabet: raise ValueError("invalid_base58")
        number=number*58+alphabet.index(char)
    raw=number.to_bytes((number.bit_length()+7)//8,"big") if number else b""
    return b"\0"*(len(text)-len(text.lstrip("1")))+raw

def validate_address(spec: AssetSpec, address: str) -> bool:
    address=address.strip()
    if spec.address_kind == "evm": return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", address))
    if spec.address_kind == "tron":
        if not re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", address): return False
        try:
            raw=_base58_decode(address)
            return len(raw)==25 and raw[0]==0x41 and hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4]==raw[21:]
        except ValueError: return False
    if spec.address_kind == "aptos":
        if not re.fullmatch(r"0x[0-9a-fA-F]{1,64}", address): return False
        return len(address.removeprefix("0x"))<=64
    if spec.address_kind == "solana":
        try: return len(_base58_decode(address)) == 32
        except ValueError: return False
    return False


def parse_addresses(fields: dict, *, allow_unverified=False) -> list[str]:
    spec=asset_for(fields.get("network",""), fields.get("asset","USDT"))
    values=_addresses(fields.get("wallet_addresses", ""))
    if not values: raise CryptoError("crypto_wallet_required", "wallet_addresses")
    if len(values)>100: raise CryptoError("crypto_wallet_limit", "wallet_addresses")
    for value in values:
        if not validate_address(spec,value): raise CryptoError("crypto_wallet_address_invalid", "wallet_addresses", value)
    if not spec.enabled and not allow_unverified: raise CryptoError("crypto_asset_pending_verification", "asset", spec.note)
    return values


def validate_config(fields: dict, require_rate=False, allow_unverified=False) -> dict:
    if not isinstance(fields,dict): raise CryptoError("invalid_channel_config")
    network=str(fields.get("network","")).strip().upper(); asset=str(fields.get("asset","USDT")).strip().upper()
    spec=asset_for(network,asset)
    addresses=parse_addresses(fields, allow_unverified=allow_unverified)
    rpc=str(fields.get("rpc_url","")).strip()
    if rpc and (urlsplit(rpc).scheme not in {"http","https"} or not urlsplit(rpc).hostname):
        raise CryptoError("crypto_rpc_invalid", "rpc_url")
    confirmations=DEFAULT_NETWORKS[network]["confirmations"]
    if require_rate:
        try:
            rate=Decimal(str(fields.get("rate_cny","")))
            if not rate.is_finite() or rate<=0: raise ValueError
        except (ValueError,InvalidOperation): raise CryptoError("crypto_rate_required", "rate_cny") from None
    return {"spec":spec,"wallet_addresses":addresses,"rpc_url":rpc,"confirmations":confirmations}


def amount_for_cny(amount: str, rate: Decimal, decimals: int) -> str:
    from .payment_terms import payment_amount
    try:return payment_amount(amount,rate)
    except ValueError:raise CryptoError('crypto_amount_too_small') from None


DEFAULT_NETWORKS = {
    "BSC": {"chain_id": 56, "confirmations": 15}, "TRON": {"chain_id": None, "confirmations": 20},
    "APTOS": {"chain_id": 1, "confirmations": 10}, "ETH": {"chain_id": 1, "confirmations": 12},
    "PLASMA": {"chain_id": 9745, "confirmations": 40}, "SOL": {"chain_id": None, "confirmations": 32},
    "ARBITRUM": {"chain_id": 42161, "confirmations": 20}, "POLYGON": {"chain_id": 137, "confirmations": 128},
    "AVAXC": {"chain_id": 43114, "confirmations": 12},
}
BEPUSDT_RPC_DEFAULTS = {
    "BSC": "https://binance-smart-chain-public.nodies.app/",
    "TRON": "grpc.trongrid.io:50051",
    "APTOS": "https://aptos-rest.publicnode.com/",
    "ETH": "https://ethereum-public.nodies.app/",
    "PLASMA": "https://rpc.plasma.to/",
    "SOL": "https://solana-rpc.publicnode.com/",
    "ARBITRUM": "https://arb1.arbitrum.io/rpc",
    "POLYGON": "https://polygon-public.nodies.app/",
}
# BEpusdt does not currently ship an Avalanche default. Use Avalanche's
# documented C-Chain endpoint for ByPay's ninth network.
BEPUSDT_RPC_DEFAULTS["AVAXC"] = "https://api.avax.network/ext/bc/C/rpc"
# Keep BEpusdt first. Only these public defaults may use a public fallback;
# custom endpoints and credentials must never be silently replaced or forwarded.
PUBLIC_RPC_FALLBACKS = {
    "BSC": ("https://bsc-rpc.publicnode.com", "https://1rpc.io/bnb"),
    "POLYGON": ("https://polygon-bor-rpc.publicnode.com",),
    "ETH": ("https://ethereum-rpc.publicnode.com",),
}
TRON_HTTP_RPC_DEFAULT = "https://api.trongrid.io"
BSC_PUBLIC_RECEIPT_RPC = "https://bsc-dataseed.bnbchain.org"


def rpc_http_endpoint(network: str, rpc_url: str) -> str:
    """Translate BEpusdt's native TRON gRPC default to its TronGrid HTTP API."""
    value = str(rpc_url or "").strip()
    if str(network).upper() == "TRON" and value.rstrip("/") in {"grpc.trongrid.io:50051", "grpc://grpc.trongrid.io:50051"}:
        return TRON_HTTP_RPC_DEFAULT
    return value.rstrip("/") if str(network).upper() == "TRON" else value


SOLANA_MAINNET_GENESIS = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
TRON_MAINNET_GENESIS_ID = "00000000000000001ebf88508a03865c71d452e25f4d51194196a1d22b6653dc"
MAX_RPC_HEAD_AGE = 600
SOLANA_MAX_TRANSACTION_VERSION = 1
TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


@lru_cache(maxsize=4)
def _rpc_client_for_process(pid):
    # Like BEpusdt's NewHttpClient, reuse connections. The PID key prevents
    # sharing an inherited connection pool across gunicorn worker forks.
    return httpx.Client(timeout=httpx.Timeout(10,connect=6),trust_env=False,follow_redirects=False,
                        limits=httpx.Limits(max_connections=20,max_keepalive_connections=10,keepalive_expiry=60))



class CryptoNetworks:
    """Encrypted administrator-managed RPC endpoints and confirmation rules."""
    def __init__(self,store,cipher):
        self.store,self.cipher=store,cipher
        store.crypto_networks=self

    def get(self, network):
        network = str(network).upper()
        if network not in DEFAULT_NETWORKS:
            raise CryptoError("crypto_network_unsupported", "network")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM crypto_network_configs WHERE network=?", (network,)).fetchone()
        base = {**DEFAULT_NETWORKS[network], "rpc_url": BEPUSDT_RPC_DEFAULTS[network], "api_key":"", "revision": 0, "check_version":0}
        if row:
            try:
                base.update(json.loads(self.cipher.decrypt(row["payload"])))
            except (InvalidToken, ValueError):
                raise CryptoError("crypto_rpc_config_locked", "rpc_url") from None
            if not base["rpc_url"]:
                base["rpc_url"] = BEPUSDT_RPC_DEFAULTS[network]
            base["revision"] = row["revision"]
            base["check_version"] = row["check_version"]
        return base

    def public(self):
        rows=[]
        for network,base in DEFAULT_NETWORKS.items():
            config=self.get(network)
            rows.append({"network":network,"chain_id":base["chain_id"],"rpc_configured":bool(config["revision"] and config["rpc_url"]),
                         "confirmations":config["confirmations"],"revision":config["revision"],
                         "checked":config["check_version"]==config["revision"] and config["revision"]>0})
        return rows

    def save(self, network, payload):
        network = str(network).upper()
        current = self.get(network)
        if (not isinstance(payload, dict) or not {"rpc_url", "confirmations", "revision"}.issubset(payload)
                or set(payload)-{"rpc_url","api_key","confirmations","revision"}
                or type(payload["revision"]) is not int or payload["revision"] != current["revision"]):
            raise CryptoError("crypto_network_conflict")
        rpc = payload["rpc_url"] or current["rpc_url"]
        api_key = payload.get("api_key") or (current.get("api_key", "") if rpc == current["rpc_url"] else "")
        parsed=urlsplit(rpc) if isinstance(rpc,str) else None
        is_bepusdt_tron_grpc=network=="TRON" and rpc in {"grpc.trongrid.io:50051","grpc://grpc.trongrid.io:50051"}
        if (not isinstance(rpc, str) or len(rpc) > 2048 or
                (rpc and not is_bepusdt_tron_grpc and (parsed.scheme != "https" or not parsed.hostname
                          or parsed.username or parsed.password))):
            raise CryptoError("crypto_rpc_invalid", "rpc_url")
        try:
            confirmations = int(payload["confirmations"])
        except (ValueError, TypeError):
            raise CryptoError("crypto_confirmations_invalid", "confirmations") from None
        if confirmations < 1 or confirmations > 10000:
            raise CryptoError("crypto_confirmations_invalid", "confirmations")
        if not isinstance(api_key,str) or len(api_key)>512 or any(c in api_key for c in "\r\n"):
            raise CryptoError("crypto_api_key_invalid","api_key")
        encrypted = self.cipher.encrypt(json.dumps({"rpc_url": rpc, "api_key":api_key, "confirmations": confirmations}).encode())
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT revision FROM crypto_network_configs WHERE network=?",(network,)).fetchone()
            actual=row["revision"] if row else 0
            if actual!=payload["revision"]:raise CryptoError("crypto_network_conflict")
            version=actual+1
            if row:
                changed=db.execute("UPDATE crypto_network_configs SET payload=?,revision=?,updated_at=?,check_version=0 WHERE network=? AND revision=?",(encrypted,version,int(time.time()),network,actual)).rowcount
                if not changed:raise CryptoError("crypto_network_conflict")
            else:
                db.execute("INSERT INTO crypto_network_configs(network,payload,revision,updated_at,check_version) VALUES (?,?,?,?,0)",(network,encrypted,version,int(time.time())))
        return self.get(network)


    def _probe(self, network, config):
        rpc=rpc_http_endpoint(network,config["rpc_url"])
        if network in {"BSC","ETH","PLASMA","ARBITRUM","POLYGON","AVAXC"}:
            chain_id=int(CryptoGateway._rpc(rpc,"eth_chainId",[],config.get("api_key","")),16)
            if chain_id!=int(config["chain_id"]):raise CryptoError("crypto_wrong_chain","rpc_url")
            height=int(CryptoGateway._rpc(rpc,"eth_blockNumber",[],config.get("api_key","")),16)
            block=CryptoGateway._rpc(rpc,"eth_getBlockByNumber",[hex(height),False],config.get("api_key",""))
            timestamp=int(block.get("timestamp","0x0"),16)
            if not timestamp or abs(int(time.time())-timestamp)>MAX_RPC_HEAD_AGE:raise CryptoError("crypto_rpc_stale","rpc_url")
            # Connectivity alone is insufficient: the scanner also requires
            # event logs and real transaction receipts (some free RPCs block
            # indexed methods even though chainId/blockNumber succeed).
            settled=max(1,height-int(config["confirmations"])-2)
            settled_block=CryptoGateway._rpc(rpc,"eth_getBlockByNumber",[hex(settled),False],config.get("api_key",""))
            if not settled_block or not settled_block.get("hash"):raise CryptoError("crypto_rpc_invalid_response","rpc_url")
            logs=CryptoGateway._rpc(rpc,"eth_getLogs",[{"fromBlock":hex(settled),"toBlock":hex(settled),"address":asset_for(network,"USDT").contract,"topics":[TRANSFER_EVENT_TOPIC]}],config.get("api_key",""))
            if not isinstance(logs,list):raise CryptoError("crypto_rpc_invalid_response","rpc_url")
            txid=logs[0]["transactionHash"] if logs else next(iter(settled_block.get("transactions",[])),None)
            if not txid:raise CryptoError("crypto_rpc_receipt_unverified","rpc_url")
            receipt=CryptoGateway._rpc(rpc,"eth_getTransactionReceipt",[txid],config.get("api_key",""))
            if not receipt or str(receipt.get("blockHash","")).lower()!=settled_block["hash"].lower() or int(receipt.get("blockNumber","0x0"),16)!=settled:
                raise CryptoError("crypto_rpc_receipt_unverified","rpc_url")
            detail={"chain_id":chain_id,"height":height,"head_timestamp":timestamp,"scanner_methods":["eth_getLogs","eth_getTransactionReceipt"],"receipt_txid":txid}
            if rpc.rstrip('/')=="https://bsc-rpc.publicnode.com" and not config.get("api_key"):
                detail["receipt_rpc_url"]=BSC_PUBLIC_RECEIPT_RPC
        elif network=="SOL":
            genesis=CryptoGateway._rpc(rpc,"getGenesisHash",[],config.get("api_key",""))
            if genesis!=SOLANA_MAINNET_GENESIS:raise CryptoError("crypto_wrong_chain","rpc_url")
            height=CryptoGateway._rpc(rpc,"getSlot",[{"commitment":"finalized"}],config.get("api_key",""))
            timestamp=CryptoGateway._rpc(rpc,"getBlockTime",[height],config.get("api_key",""))
            if not timestamp or abs(int(time.time())-int(timestamp))>MAX_RPC_HEAD_AGE:raise CryptoError("crypto_rpc_stale","rpc_url")
            slots=CryptoGateway._rpc(rpc,"getBlocks",[max(0,height-20),height,{"commitment":"finalized"}],config.get("api_key",""))
            if not slots:raise CryptoError("crypto_rpc_invalid_response","rpc_url")
            block=CryptoGateway._rpc(rpc,"getBlock",[slots[0],{"encoding":"jsonParsed","transactionDetails":"full","rewards":False,"commitment":"finalized","maxSupportedTransactionVersion":SOLANA_MAX_TRANSACTION_VERSION}],config.get("api_key",""))
            transactions=block.get("transactions",[]) if block else []
            if not transactions:raise CryptoError("crypto_rpc_receipt_unverified","rpc_url")
            signature=transactions[0]["transaction"]["signatures"][0]
            tx=CryptoGateway._rpc(rpc,"getTransaction",[signature,{"encoding":"jsonParsed","commitment":"finalized","maxSupportedTransactionVersion":SOLANA_MAX_TRANSACTION_VERSION}],config.get("api_key",""))
            if not tx or int(tx.get("slot",-1))!=slots[0]:raise CryptoError("crypto_rpc_receipt_unverified","rpc_url")
            detail={"genesis_hash":genesis,"height":height,"head_timestamp":timestamp,"scanner_methods":["getBlocks","getBlock","getTransaction"],"receipt_txid":signature}
        elif network=="APTOS":
            response=httpx.get(rpc.rstrip("/")+"/v1",headers={"Authorization":"Bearer "+config.get("api_key","")} if config.get("api_key") else {},timeout=6,follow_redirects=False,trust_env=False);response.raise_for_status();data=response.json()
            if str(data.get("chain_id"))!="1":raise CryptoError("crypto_wrong_chain","rpc_url")
            timestamp=int(data.get("ledger_timestamp","0"))//1_000_000
            if not timestamp or abs(int(time.time())-timestamp)>MAX_RPC_HEAD_AGE:raise CryptoError("crypto_rpc_stale","rpc_url")
            detail={"chain_id":data.get("chain_id"),"height":data.get("ledger_version"),"head_timestamp":timestamp}
        elif network=="TRON":
            api_key=config.get("api_key","");headers={"TRON-PRO-API-KEY":api_key} if api_key else {}
            genesis=httpx.post(rpc.rstrip("/")+"/walletsolidity/getblockbynum",json={"num":0,"visible":True},headers=headers,timeout=6,follow_redirects=False,trust_env=False);genesis.raise_for_status();genesis_id=str(genesis.json().get("blockID",""))
            if genesis_id.lower()!=TRON_MAINNET_GENESIS_ID:raise CryptoError("crypto_wrong_chain","rpc_url")
            response=httpx.post(rpc.rstrip("/")+"/walletsolidity/getnowblock",json={},headers=headers,timeout=6,follow_redirects=False,trust_env=False);response.raise_for_status();data=response.json()
            header=data.get("block_header",{}).get("raw_data",{});timestamp=int(header.get("timestamp",0))//1000
            if type(header.get("number")) is not int or not data.get("blockID"):raise RuntimeError("tron_block_invalid")
            if not timestamp or abs(int(time.time())-timestamp)>MAX_RPC_HEAD_AGE:raise CryptoError("crypto_rpc_stale","rpc_url")
            detail={"genesis_block_id":genesis_id,"height":header["number"],"head_timestamp":timestamp}
        else:raise CryptoError("crypto_network_unsupported","network")
        return detail

    def check(self, network):
        network=str(network).upper()
        config=self.get(network)
        if not config["rpc_url"]:
            return {"verified":False,"provider_code":"rpc_not_configured","field":"rpc_url"}
        # A failed re-check invalidates an earlier success, including a concurrent
        # save. Never leave a stale 'checked' badge after an RPC outage.
        with self.store.connect() as db:
            changed=db.execute("UPDATE crypto_network_configs SET check_version=0 WHERE network=? AND revision=?",(network,config["revision"])).rowcount
        if not changed:
            return {"verified":False,"provider_code":"crypto_network_conflict","network":network}
        candidates=[config["rpc_url"]]
        public_defaults=(BEPUSDT_RPC_DEFAULTS[network],*PUBLIC_RPC_FALLBACKS.get(network,()))
        if not config.get("api_key") and config["rpc_url"].rstrip("/") in {value.rstrip("/") for value in public_defaults}:
            candidates.extend(value for value in public_defaults if value.rstrip("/")!=config["rpc_url"].rstrip("/"))
        attempts=[]
        retryable={"crypto_rpc_subscription_required","crypto_rpc_access_denied","crypto_rpc_rate_limited","crypto_rpc_timeout","crypto_rpc_unreachable","crypto_rpc_http_error","crypto_rpc_method_unavailable","crypto_rpc_rejected","crypto_rpc_stale"}
        for rpc in candidates:
            try:
                detail=self._probe(network,{**config,"rpc_url":rpc})
                fallback=rpc!=config["rpc_url"]
                if fallback:
                    config=self.save(network,{"revision":config["revision"],"rpc_url":rpc,"api_key":"","confirmations":config["confirmations"]})
                with self.store.connect() as db:
                    changed=db.execute("UPDATE crypto_network_configs SET check_version=? WHERE network=? AND revision=?",(config["revision"],network,config["revision"])).rowcount
                if not changed:
                    return {"verified":False,"provider_code":"crypto_network_conflict","network":network}
                return {"verified":True,"provider_code":"rpc_probe_ok","network":network,"revision":config["revision"],"rpc_url":rpc,"fallback_used":fallback,"attempts":attempts,**detail}
            except Exception as exc:
                failure=rpc_failure(exc)
                attempts.append({"host":urlsplit(rpc_http_endpoint(network,rpc)).hostname,**failure})
                if failure["provider_code"] not in retryable:
                    break
        return {"verified":False,"network":network,"revision":config["revision"],"attempts":attempts,**failure}


class CryptoGateway:
    def __init__(self, store=None, networks=None, rate_provider=None):
        self.store, self.networks = store, networks
        self.rate_provider=rate_provider or store.pricing.quote

    @staticmethod
    def _rpc(url, method, params, api_key=""):
        # PublicNode's BSC free gateway serves event logs but requires a
        # personal token for receipt indexing. BNB's official public gateway
        # serves receipts, but restricts eth_getLogs. This explicit read-only
        # combination keeps both operations free, with chain identity checked
        # before every companion read. Never forward custom credentials.
        if url.rstrip('/')=="https://bsc-rpc.publicnode.com" and method=="eth_getTransactionReceipt" and not api_key:
            chain_id=CryptoGateway._rpc(BSC_PUBLIC_RECEIPT_RPC,"eth_chainId",[])
            if int(chain_id,16)!=56:
                raise CryptoError("crypto_wrong_chain","rpc_url")
            return CryptoGateway._rpc(BSC_PUBLIC_RECEIPT_RPC,method,params)
        headers={"Authorization":"Bearer "+api_key} if api_key else {}
        for attempt in range(2):
            try:
                response = _rpc_client_for_process(os.getpid()).post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=headers)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data,dict) or (not data.get("error") and "result" not in data):
                    raise RpcFailure("crypto_rpc_invalid_response",method=method)
                if data.get("error"):
                    code=data["error"].get("code") if isinstance(data["error"],dict) else None
                    reason="crypto_rpc_method_unavailable" if code==-32601 else "crypto_rpc_rate_limited" if code==-32005 else "crypto_rpc_rejected"
                    raise RpcFailure(reason,method=method,rpc_code=code)
                return data["result"]
            except Exception as exc:
                result=rpc_failure(exc)
                if attempt==0 and result["provider_code"] in {"crypto_rpc_timeout","crypto_rpc_unreachable","crypto_rpc_rate_limited"}:
                    time.sleep(0.4)
                    continue
                if isinstance(exc,RpcFailure):raise
                raise RpcFailure(result["provider_code"],method=method,http_status=result.get("http_status"),exception_type=result.get("exception_type")) from None

    def check(self, fields, network_cfg=None):
        try:
            cfg = validate_config(fields)
        except CryptoError as exc:
            return {"authenticated": False, "verified": False, "provider_code": exc.code, "field": exc.field, "detail": exc.detail}
        spec = cfg["spec"]
        network_cfg = network_cfg or (self.networks.get(spec.network) if self.networks else {})
        rpc = rpc_http_endpoint(spec.network, network_cfg.get("rpc_url", ""))
        if not spec.enabled:
            return {"authenticated":True,"verified":False,"provider_code":"crypto_asset_pending_verification","field":"asset","detail":spec.note,"network":spec.network,"asset":spec.asset}
        if network_cfg.get("revision",0)>0 and network_cfg.get("check_version")!=network_cfg.get("revision"):
            return {"authenticated":False,"verified":False,"provider_code":"crypto_network_check_required","field":"rpc_url","network":spec.network}
        if not rpc:
            return {"authenticated": False, "verified": False, "provider_code": "rpc_not_configured", "field": "rpc_url", "network": spec.network, "asset": spec.asset}
        try:
            if spec.address_kind == "evm":
                api_key=network_cfg.get("api_key","")
                chain_id = int(self._rpc(rpc, "eth_chainId", [],api_key), 16)
                expected = network_cfg.get("chain_id")
                if expected is not None and chain_id != int(expected):
                    raise CryptoError("crypto_wrong_chain", "rpc_url")
                height = int(self._rpc(rpc, "eth_blockNumber", [],api_key), 16)
                if not spec.contract:
                    raise CryptoError("crypto_contract_unverified", "asset")
                code = self._rpc(rpc, "eth_getCode", [spec.contract, "latest"],api_key)
                if not isinstance(code, str) or code in {"0x", "0x0"}:
                    raise CryptoError("crypto_contract_missing", "asset")
                decimals = int(self._rpc(rpc, "eth_call", [{"to": spec.contract, "data": "0x313ce567"}, "latest"],api_key), 16)
                if decimals != spec.decimals:
                    raise CryptoError("crypto_decimals_mismatch", "asset")
                symbol_raw=self._rpc(rpc,"eth_call",[{"to":spec.contract,"data":"0x95d89b41"},"latest"],api_key)
                symbol_bytes=bytes.fromhex(symbol_raw.removeprefix("0x"))
                if len(symbol_bytes)>=64:
                    offset=int.from_bytes(symbol_bytes[:32],"big");length=int.from_bytes(symbol_bytes[offset:offset+32],"big");symbol=symbol_bytes[offset+32:offset+32+length].decode("utf8","replace").strip("\0")
                else:symbol=symbol_bytes.rstrip(b"\0").decode("utf8","replace").strip()
                aliases={"BSC_USDC":{"USDC","Binance-Peg USD Coin","Binance-Peg USD Coin (USDC)"},"PLASMA_USDT":{"USDT","USDT0","USD₮0"},"ARBITRUM_USDT":{"USDT","USDT0","USD₮0"},"POLYGON_USDT":{"USDT","USDT0","USD₮0"}}
                if symbol.upper() not in {value.upper() for value in aliases.get(spec.code,{spec.asset})}:
                    raise CryptoError("crypto_symbol_mismatch","asset",symbol)
                meta = {"chain_id": chain_id, "height": height, "contract": spec.contract, "decimals": decimals, "symbol":symbol}
            elif spec.address_kind == "solana":
                genesis = self._rpc(rpc, "getGenesisHash", [],network_cfg.get("api_key",""))
                if genesis != SOLANA_MAINNET_GENESIS:
                    raise CryptoError("crypto_wrong_chain", "rpc_url")
                slot = self._rpc(rpc, "getSlot", [{"commitment": "finalized"}],network_cfg.get("api_key",""))
                token=self._rpc(rpc,"getAccountInfo",[spec.contract,{"encoding":"jsonParsed","commitment":"finalized"}],network_cfg.get("api_key",""))
                mint=token.get("value") or {};parsed=mint.get("data",{}).get("parsed",{});info=parsed.get("info",{})
                if mint.get("owner")!="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA" or parsed.get("type")!="mint" or info.get("isInitialized") is not True:
                    raise CryptoError("crypto_contract_missing","asset")
                decimals=int(info.get("decimals",-1))
                if decimals!=spec.decimals:raise CryptoError("crypto_decimals_mismatch","asset")
                meta = {"genesis_hash": genesis, "height": slot, "contract": spec.contract,"decimals":decimals,"token_program":mint["owner"]}
            elif spec.address_kind == "aptos":
                response = httpx.get(rpc.rstrip("/") + "/v1",headers={"Authorization":"Bearer "+network_cfg.get("api_key","")} if network_cfg.get("api_key") else {}, timeout=6,follow_redirects=False,trust_env=False)
                response.raise_for_status()
                data = response.json()
                if str(data.get("chain_id")) != "1":
                    raise CryptoError("crypto_wrong_chain", "rpc_url")
                resource_url=rpc.rstrip("/")+"/v1/accounts/"+spec.contract+"/resource/"+quote("0x1::fungible_asset::Metadata",safe="")
                token_response=httpx.get(resource_url,headers={"Authorization":"Bearer "+network_cfg.get("api_key","")} if network_cfg.get("api_key") else {},timeout=6,follow_redirects=False,trust_env=False)
                token_response.raise_for_status();token_data=token_response.json().get('data',{})
                decimals=int(token_data.get('decimals',-1));symbol=str(token_data.get('symbol',''))
                if decimals!=spec.decimals:raise CryptoError("crypto_decimals_mismatch","asset")
                allowed={spec.asset}
                if symbol.upper() not in {value.upper() for value in allowed}:raise CryptoError("crypto_symbol_mismatch","asset",symbol)
                meta = {"chain_id": data.get("chain_id"), "height": data.get("ledger_version"), "contract": spec.contract, "decimals": decimals,"symbol":symbol}
            elif spec.address_kind == "tron":
                headers={"TRON-PRO-API-KEY":network_cfg.get("api_key","")} if network_cfg.get("api_key") else {}
                genesis=httpx.post(rpc.rstrip("/")+"/walletsolidity/getblockbynum",json={"num":0,"visible":True},headers=headers,timeout=6,follow_redirects=False,trust_env=False)
                genesis.raise_for_status()
                if str(genesis.json().get("blockID","")).lower()!=TRON_MAINNET_GENESIS_ID:raise CryptoError("crypto_wrong_chain","rpc_url")
                response = httpx.post(rpc.rstrip("/") + "/walletsolidity/getnowblock", json={},headers=headers, timeout=6,follow_redirects=False,trust_env=False)
                response.raise_for_status()
                data = response.json()
                header = data.get("block_header", {}).get("raw_data", {})
                if type(header.get("number")) is not int or not data.get("blockID"):
                    raise RuntimeError("tron_block_invalid")
                contract=httpx.post(rpc.rstrip("/")+"/wallet/getcontract",json={"value":spec.contract,"visible":True},headers=headers,timeout=6,follow_redirects=False,trust_env=False)
                contract.raise_for_status();contract_data=contract.json()
                if not contract_data.get('bytecode'):raise CryptoError("crypto_contract_missing","asset")
                decimals_result=httpx.post(rpc.rstrip("/")+"/wallet/triggerconstantcontract",json={"owner_address":"T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb","contract_address":spec.contract,"function_selector":"decimals()","parameter":"","visible":True},headers=headers,timeout=6,follow_redirects=False,trust_env=False)
                decimals_result.raise_for_status();decimals=int(decimals_result.json().get('constant_result',["0"])[0],16)
                if decimals!=spec.decimals:raise CryptoError("crypto_decimals_mismatch","asset")
                symbol_result=httpx.post(rpc.rstrip("/")+"/wallet/triggerconstantcontract",json={"owner_address":"T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb","contract_address":spec.contract,"function_selector":"symbol()","parameter":"","visible":True},headers=headers,timeout=6,follow_redirects=False,trust_env=False)
                symbol_result.raise_for_status();raw=bytes.fromhex(symbol_result.json().get('constant_result',[""])[0])
                if len(raw)>=64:
                    offset=int.from_bytes(raw[:32],"big");length=int.from_bytes(raw[offset:offset+32],"big");symbol=raw[offset+32:offset+32+length].decode("utf8","strict")
                else:symbol=raw.rstrip(b"\0").decode("utf8","strict")
                if symbol.upper() not in {"USDT","TETHER USD"}:raise CryptoError("crypto_symbol_mismatch","asset",symbol)
                meta = {"height": header["number"], "block_id": data["blockID"], "contract": spec.contract, "decimals": decimals,"symbol":symbol}
            else:
                raise CryptoError("crypto_adapter_missing", "network")
            return {"authenticated": True, "verified": True, "provider_code": "rpc_probe_ok", "network": spec.network,
                    "asset": spec.asset, "wallets": len(cfg["wallet_addresses"]), "metadata": meta}
        except Exception as exc:
            return {"authenticated":False,"verified":False,"network":spec.network,"asset":spec.asset,**rpc_failure(exc)}

    def create_payment(self, number, amount, cfg, *, expires_at, subject=""):
        parsed = validate_config(cfg)
        spec = parsed["spec"]
        network_cfg={}
        if self.networks:
            network_cfg = self.networks.get(spec.network)
            if not network_cfg.get("rpc_url"):
                raise CryptoError("rpc_not_configured", "rpc_url")
            if not network_cfg.get("revision") or network_cfg.get("revision")!=network_cfg.get("check_version"):
                raise CryptoError("crypto_network_check_required","rpc_url")
        try:quote=self.rate_provider(spec.asset)
        except Exception:raise CryptoError("crypto_rate_unavailable") from None
        if not isinstance(quote,dict):raise CryptoError('crypto_rate_unavailable')
        rate=Decimal(str(quote.get('rate')))
        rate_updated_at=int(quote.get('updated_at',0));rate_observed_at=int(quote.get('observed_at',0));rate_source=str(quote.get('source',''))[:32]
        if not rate.is_finite() or rate<=0 or not rate_updated_at or abs(int(time.time())-rate_updated_at)>180:raise CryptoError('crypto_rate_unavailable')
        crypto_amount = amount_for_cny(amount, rate, spec.decimals)
        if Decimal(crypto_amount)<=0:raise CryptoError("crypto_amount_too_small")
        # Capture a chain position before publishing the address. It bounds
        # initial catch-up even after a worker outage.
        scan_from=0
        if self.networks:
            from .crypto_scanner import CryptoScanner
            try:scan_from=max(0,CryptoScanner._latest(None,spec,network_cfg)-2)
            except Exception:raise CryptoError("crypto_rpc_unavailable") from None
        candidates = sorted(parsed["wallet_addresses"], key=lambda address: hashlib.sha256((number + address).encode()).digest())
        policy=self.store.crypto_policy.read() if self.store and hasattr(self.store,'crypto_policy') else {'match_mode':'exact','lookback_hours':3,'offset_confirm':True}
        address=candidates[0]
        if self.store:
            from .payment_terms import address_key,overlaps
            with self.store.connect() as db:
                active=db.execute("SELECT crypto_address,crypto_amount,match_mode FROM orders WHERE channel='crypto' AND crypto_network=? AND crypto_asset=? AND status IN ('CREATED','CONFIRMING') AND (status='CONFIRMING' OR expires_at+json_extract(payment_payload,'$.lookback_hours')*3600>?)",(spec.network,spec.asset,int(time.time()))).fetchall()
                address=next((candidate for candidate in candidates if not any(address_key(spec.network,row['crypto_address'])==address_key(spec.network,candidate) and overlaps(crypto_amount,policy['match_mode'],row['crypto_amount'],row['match_mode']) for row in active)),None)
                if address is None:raise CryptoError('crypto_wallet_amount_conflict','wallet_addresses')
        return {"kind": "crypto", "url": address, "network": spec.network, "asset": spec.asset, "asset_code": spec.code,
                "address": address, "crypto_amount": crypto_amount, "rate_cny": format(rate, "f"),"rate_updated_at":rate_updated_at,
                "rate_observed_at":rate_observed_at,"rate_source":rate_source,"contract": spec.contract,
                "decimals": spec.decimals, "expires_at": expires_at, "subject": subject,
                "fixed_rate_snapshot":quote.get("fixed_rate",""),"market_rate_snapshot":quote.get("market_rate",""),"rate_snapshot_at":quote.get("market_updated_at",0),"match_mode":policy["match_mode"],"lookback_hours":policy["lookback_hours"],"confirmations": int(network_cfg.get("confirmations",parsed["confirmations"])) if policy["offset_confirm"] else 1,"scan_from":scan_from,"identity":spec.identity}
