"""Verify externally submitted Crypto transfer receipts with the saved public RPC."""
from __future__ import annotations
import hashlib
import re
from decimal import Decimal
import httpx
from .crypto import CryptoError, CryptoGateway, _base58_decode, validate_address

TRANSFER_TOPIC="0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

def _post(url,payload,headers=None):
    response=httpx.post(url,json=payload,headers=headers or {},timeout=12,follow_redirects=False,trust_env=False);response.raise_for_status();return response.json()
def _get(url,headers=None):
    response=httpx.get(url,headers=headers or {},timeout=12,follow_redirects=False,trust_env=False);response.raise_for_status();return response.json()
def _tron_payload(value):
    raw=_base58_decode(value)
    if len(raw)!=25 or raw[0]!=0x41 or hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4]!=raw[21:]:raise CryptoError('invalid_crypto_refund_address')
    return raw[:21].hex().upper()

def _verify_evm(rpc,api_key,spec,txid,destination,atomic):
    receipt=CryptoGateway._rpc(rpc,'eth_getTransactionReceipt',[txid],api_key)
    if not receipt:return None
    if receipt.get('status','0x0') in {'0x0','0x00'}:raise CryptoError('crypto_transfer_failed')
    height=int(receipt['blockNumber'],16);block=CryptoGateway._rpc(rpc,'eth_getBlockByNumber',[hex(height),False],api_key)
    if not block or receipt.get('blockHash','').lower()!=block.get('hash','').lower():raise CryptoError('crypto_transfer_not_canonical')
    seen=False
    for log in receipt.get('logs',[]):
        topics=log.get('topics') or []
        if str(log.get('address','')).lower()!=spec.contract.lower() or len(topics)<3 or ('0x'+topics[0].removeprefix('0x')).lower()!=TRANSFER_TOPIC:continue
        if '0x'+topics[2][-40:].lower()==destination.lower() and int(log.get('data','0x0'),16)==atomic:seen=True;break
    if not seen:raise CryptoError('crypto_transfer_mismatch')
    latest=int(CryptoGateway._rpc(rpc,'eth_blockNumber',[],api_key),16)
    return {'block_height':height,'confirmations':max(0,latest-height+1)}

def _verify_tron(rpc,api_key,spec,txid,destination,atomic):
    headers={'TRON-PRO-API-KEY':api_key} if api_key else {};rpc=rpc.rstrip('/')
    info=_post(rpc+'/walletsolidity/gettransactioninfobyid',{'value':txid},headers)
    if not info:return None
    if info.get('receipt',{}).get('result')!='SUCCESS':raise CryptoError('crypto_transfer_failed')
    contract=_tron_payload(spec.contract);target=_tron_payload(destination);seen=False
    for log in info.get('log',[]):
        address=str(log.get('address','')).removeprefix('0x').upper();topics=log.get('topics') or []
        if address not in {contract,contract[-40:]} or len(topics)<3 or ('0x'+topics[0].removeprefix('0x')).lower()!=TRANSFER_TOPIC:continue
        if ('41'+topics[2][-40:]).upper()==target and int(log.get('data','0x0'),16)==atomic:seen=True;break
    if not seen:raise CryptoError('crypto_transfer_mismatch')
    height=int(info['blockNumber']);block=_post(rpc+'/wallet/getblockbynum',{'num':height,'visible':True},headers)
    if not block:return None
    if str(info.get('blockID',block.get('blockID',''))).lower()!=str(block.get('blockID','')).lower():raise CryptoError('crypto_transfer_not_canonical')
    latest=_post(rpc+'/walletsolidity/getnowblock',{},headers)
    return {'block_height':height,'confirmations':max(0,int(latest['block_header']['raw_data']['number'])-height+1)}

def _verify_solana(rpc,api_key,spec,txid,destination,atomic):
    tx=CryptoGateway._rpc(rpc,'getTransaction',[txid,{'encoding':'jsonParsed','commitment':'finalized','maxSupportedTransactionVersion':0}],api_key)
    if not tx:return None
    meta=tx.get('meta') or {}
    if meta.get('err') is not None:raise CryptoError('crypto_transfer_failed')
    from .crypto_scanner import _solana_token_transfers
    if not any(item['destination']==destination and int(item['amount']*(Decimal(10)**spec.decimals))==atomic for item in _solana_token_transfers(tx,spec)):
        raise CryptoError('crypto_transfer_mismatch')
    latest=int(CryptoGateway._rpc(rpc,'getSlot',[{'commitment':'finalized'}],api_key))
    return {'block_height':int(tx['slot']),'confirmations':max(0,latest-int(tx['slot'])+1),'finalized':True}

def _verify_aptos(rpc,api_key,spec,txid,destination,atomic):
    headers={'Authorization':'Bearer '+api_key} if api_key else {};rpc=rpc.rstrip('/')
    response=httpx.get(rpc+f'/v1/transactions/by_hash/{txid}',headers=headers,timeout=12,follow_redirects=False,trust_env=False)
    if response.status_code==404:return None
    response.raise_for_status();tx=response.json()
    if tx.get('success') is not True:raise CryptoError('crypto_transfer_failed')
    target=destination.lower().removeprefix('0x').zfill(64);metadata=spec.contract.lower().removeprefix('0x').zfill(64)
    for event in tx.get('events',[]):
        if event.get('type') not in {'0x1::fungible_asset::Deposit','0x1::fungible_asset::DepositEvent'}:continue
        data=event.get('data',{})
        if int(data.get('amount','0'))!=atomic:continue
        store=str(data.get('store','')).lower();resources=_get(rpc+f'/v1/accounts/{store}/resources?ledger_version={tx["version"]}',headers)
        core=next((x.get('data',{}) for x in resources if x.get('type')=='0x1::object::ObjectCore'),{})
        fungible=next((x.get('data',{}) for x in resources if x.get('type')=='0x1::fungible_asset::FungibleStore'),{})
        owner=str(core.get('owner','')).lower().removeprefix('0x').zfill(64);meta=fungible.get('metadata','');meta=str(meta.get('inner','') if isinstance(meta,dict) else meta).lower().removeprefix('0x').zfill(64)
        if owner==target and meta==metadata:
            latest=int(_get(rpc+'/v1',headers)['ledger_version'])
            return {'block_height':int(tx['version']),'confirmations':max(0,latest-int(tx['version'])+1),'finalized':True}
    raise CryptoError('crypto_transfer_mismatch')

def verify_transfer(networks,spec,txid,destination,amount):
    if not isinstance(destination,str) or not validate_address(spec,destination):raise CryptoError('invalid_crypto_refund_address')
    try:
        value=Decimal(str(amount));atomic=int(value*(Decimal(10)**spec.decimals))
        if not value.is_finite() or value<=0 or Decimal(atomic).scaleb(-spec.decimals)!=value:raise ValueError()
    except Exception:raise CryptoError('invalid_crypto_refund_amount') from None
    net=networks.get(spec.network)
    if not net.get('rpc_url') or not net.get('revision') or net.get('revision')!=net.get('check_version'):raise CryptoError('crypto_network_check_required')
    if not isinstance(txid,str):raise CryptoError('invalid_crypto_refund_txid')
    if spec.address_kind=='evm' and not re.fullmatch(r'0x[0-9a-fA-F]{64}',txid):raise CryptoError('invalid_crypto_refund_txid')
    if spec.address_kind=='tron' and not re.fullmatch(r'[0-9a-fA-F]{64}',txid):raise CryptoError('invalid_crypto_refund_txid')
    if spec.address_kind=='aptos' and not re.fullmatch(r'0x[0-9a-fA-F]{64}',txid):raise CryptoError('invalid_crypto_refund_txid')
    if spec.address_kind=='solana':
        try:valid=len(_base58_decode(txid))==64
        except ValueError:valid=False
        if not valid:raise CryptoError('invalid_crypto_refund_txid')
    verify={'evm':_verify_evm,'tron':_verify_tron,'aptos':_verify_aptos,'solana':_verify_solana}[spec.address_kind]
    try:result=verify(net['rpc_url'],net.get('api_key',''),spec,txid,destination,atomic)
    except CryptoError:raise
    except Exception:raise CryptoError('crypto_receipt_unavailable') from None
    if result is None:return None
    required=int(net['confirmations'])
    if result['confirmations']<required:raise CryptoError('crypto_transfer_not_confirmed')
    return {**result,'required_confirmations':required,'network':spec.network,'asset':spec.asset,'amount':format(value,'f'),'destination':destination,'txid':txid}
