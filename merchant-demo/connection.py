"""Independent Epay V1/V2 HTTP client. No Pay server internals or provider secrets."""
import time
import re
from decimal import Decimal
from urllib.parse import urlsplit

from epay_protocol import private_key, public_key, public_pem, signed, verify
from pay_http import request_json


# Epay payment types the demo can ask Pay for: the Epay name, not Pay's internal channel name
TYPES=('alipay','wxpay')
# Answers from Pay that the page can explain; anything else is shown as a plain refusal
PAY_ERRORS={'invalid_signature','merchant_unavailable','rsa_required','order_not_found','invalid_merchant_callback','payment_not_configured','channel_disabled','order_conflict','channel_not_integrated','payment_provider_error','payment_creation_pending'}


class ConnectionError(ValueError):
    def __init__(self, code): super().__init__(code); self.code=code


def credentials(config):
    pid=str(config.get('PAY_MERCHANT_ID','')).strip()
    if not pid.isdigit() or not 1<=len(pid)<=12: raise ConnectionError('merchant_id_required')
    version=config.get('PAY_API_VERSION','v1')
    if version not in {'v1','v2'}: raise ConnectionError('invalid_api_version')
    if version=='v1':
        if not 16<=len(config.get('PAY_MD5_KEY',''))<=128: raise ConnectionError('md5_key_required')
    else:
        try: private_key(config.get('PAY_MERCHANT_PRIVATE_KEY',''))
        except (ValueError,TypeError): raise ConnectionError('merchant_private_key_invalid') from None
        try: public_key(config.get('PAY_PLATFORM_PUBLIC_KEY',''))
        except (ValueError,TypeError): raise ConnectionError('platform_public_key_invalid') from None
    return pid,version


def call(config,action,parameters=None):
    pid,version=credentials(config)
    params={'pid':pid,**(parameters or {})}
    origin=config['PAY_ORIGIN'].rstrip('/')
    if version=='v1':
        if action=='create':
            path='/mapi.php';params=signed(params,config['PAY_MD5_KEY'])
        else:
            path='/api.php';params.update(act='query' if action=='info' else 'order',key=config['PAY_MD5_KEY'])
    else:
        path={'create':'/api/pay/create','query':'/api/pay/query','info':'/api/merchant/info'}[action]
        params=signed(params,config['PAY_MERCHANT_PRIVATE_KEY'],'RSA')
    response,status=request_json('POST',origin+path,payload=params,form=True)
    if status!=200:
        raise ConnectionError(response.get('error') if response.get('error') in {'pay_unreachable','pay_redirect_rejected'} else 'pay_api_unavailable')
    if version=='v2' and not verify(response,config['PAY_PLATFORM_PUBLIC_KEY'],'RSA',timestamp=True): raise ConnectionError('platform_signature_invalid')
    if response.get('code')!=(0 if version=='v2' else 1):
        error=response.get('error')
        known=isinstance(error,str) and (error in PAY_ERRORS or re.fullmatch(r'wechat_[a-z0-9_]{2,56}',error) is not None)
        raise ConnectionError(error if known else 'epay_request_failed')
    return response


def check(config):
    response=call(config,'info')
    if str(response.get('pid'))!=str(config['PAY_MERCHANT_ID']): raise ConnectionError('merchant_id_mismatch')
    if response.get('status',response.get('active'))!=1: raise ConnectionError('merchant_unavailable')
    return response


def create(config,number,amount,subject,client_ip='127.0.0.1',pay_type='alipay'):
    if pay_type not in TYPES: raise ConnectionError('invalid_pay_type')
    response=call(config,'create',{'out_trade_no':number,'money':amount,'name':subject,'type':pay_type,
                                  'notify_url':config['DEMO_ORIGIN']+'/notify','return_url':config['DEMO_ORIGIN']+'/return',
                                  'clientip':client_ip,'device':'pc'})
    url=response.get('pay_info') if config['PAY_API_VERSION']=='v2' else response.get('payurl')
    parsed=urlsplit(url or '')
    expected=urlsplit(config['PAY_ORIGIN'])
    same_host=parsed.hostname==expected.hostname or (parsed.scheme==expected.scheme=='http' and {parsed.hostname,expected.hostname} <= {'localhost','127.0.0.1','::1'})
    if parsed.scheme!=expected.scheme or not same_host or (parsed.port or (443 if parsed.scheme=='https' else 80))!=(expected.port or (443 if expected.scheme=='https' else 80)) or not re.fullmatch(r'/cashier/[A-Za-z0-9_-]{40,128}',parsed.path) or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ConnectionError('invalid_checkout_url')
    trade=str(response.get('trade_no',''))
    if not trade or len(trade)>64: raise ConnectionError('invalid_checkout_response')
    return {'pay_url':url,'trade_no':trade}


def verify_notification(config,params):
    pid,version=credentials(config)
    algorithm='RSA' if version=='v2' else 'MD5'
    key=config['PAY_PLATFORM_PUBLIC_KEY'] if version=='v2' else config['PAY_MD5_KEY']
    if params.get('sign_type','MD5')!=algorithm or not verify(params,key,algorithm,timestamp=version=='v2'): raise ConnectionError('invalid_notification_signature')
    if str(params.get('pid'))!=pid or params.get('trade_status')!='TRADE_SUCCESS' or params.get('type') not in TYPES: raise ConnectionError('invalid_notification_fields')
    return params
