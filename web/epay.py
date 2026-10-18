"""Epay merchant-facing endpoints reuse Pay's existing payment/query pipeline."""
import hmac
import ipaddress
import re
import time
from decimal import Decimal
from functools import wraps
from urllib.parse import urlencode, urlsplit

from flask import Response, g, jsonify, redirect, request

from bypay.epay_protocol import signed, verify
from bypay.payment_validation import money
from bypay.wechat_client import WechatError
from .merchants import EPAY_CHANNELS
from .webhooks import validate_callback


def frame_origins(callback):
    parsed=urlsplit(callback)
    origins={parsed.scheme+'://'+parsed.netloc}
    if parsed.scheme=='http' and parsed.hostname in {'localhost','127.0.0.1','::1'}:
        suffix=':'+str(parsed.port) if parsed.port else ''
        origins.update('http://'+host+suffix for host in ('localhost','127.0.0.1','[::1]'))
    return sorted(origins)


def install_epay(app, merchants, auth, trace, create_payment, prepare_qr, ApiError):
    store = merchants.store

    def public_origin():
        configured=merchants.site.read()['origin']
        base=urlsplit(configured);incoming=urlsplit(request.host_url)
        loopback={'localhost','127.0.0.1','::1'}
        if (base.scheme==incoming.scheme=='http' and base.hostname in loopback and incoming.hostname in loopback
                and (base.port or 80)==(incoming.port or 80)):
            return request.host_url.rstrip('/')
        return configured

    def target_user():
        pid = request.args.get('merchant_id')
        if not pid: return g.user['id']
        account = merchants.account(pid)
        if not account or (account['user_id']!=g.user['id'] and g.user['role']!='admin'): raise ApiError('not_found',404)
        return account['user_id']

    @app.get('/api/merchant/config')
    @auth(session_only=True)
    def merchant_config():
        return jsonify({**merchants.describe(target_user()),'interface_url':public_origin()})

    @app.post('/api/merchant/config')
    @auth(session_only=True)
    def merchant_config_save():
        user = target_user()
        try: result = merchants.update(user,request.get_json(silent=True))
        except ValueError as exc: raise ApiError(str(exc)) from None
        trace('merchant.config','merchant_credentials_updated',{'merchant_id':result['merchant_id'],'signature_mode':result['signature_mode']},user_id=g.user['id'])
        result['interface_url']=public_origin()
        return jsonify(result)

    @app.get('/admin/uset.php')
    def old_admin_route():
        pid = request.args.get('uid','')
        return redirect('/dashboard?tab=keys'+('&merchant_id='+pid if pid.isdigit() else ''))

    @app.get('/api/epay/platform-key')
    def platform_public_key():
        from bypay.epay_protocol import public_pem
        return jsonify(algorithm='RSA',public_key=public_pem(merchants.platform))

    def parameters():
        source = request.form if request.method=='POST' else request.args
        if not source or any(len(source.getlist(k))!=1 or not re.fullmatch(r'[A-Za-z0-9_]+',k) for k in source):
            raise ApiError('invalid_parameters')
        if len(source)>40 or any(len(v)>12000 for v in source.values()): raise ApiError('invalid_parameters')
        return source.to_dict()

    def authenticated(params,version,legacy=False):
        pid = params.get('pid','')
        if not re.fullmatch(r'[0-9]{1,12}',str(pid)): raise ApiError('invalid_merchant_id',401)
        account = merchants.account(int(pid))
        if not account or account['disabled']: raise ApiError('merchant_unavailable',401)
        if version=='v1' and account['mode']=='rsa': raise ApiError('rsa_required',401)
        if legacy:
            valid = hmac.compare_digest(merchants.secret(account),params.get('key',''))
        else:
            algorithm = 'RSA' if version=='v2' else 'MD5'
            valid = params.get('sign_type','MD5')==algorithm and verify(params, account['public_key'] if version=='v2' else merchants.secret(account), algorithm, timestamp=version=='v2')
        if not valid: raise ApiError('invalid_signature',401)
        if not store.rate_limit('epay:'+str(account['pid']),120,60): raise ApiError('rate_limited',429)
        g.user = {'id':account['user_id'],'role':'user','email':account['email']}
        return account

    def result(body,version,status=200):
        if version=='v2': body = signed(body,merchants.platform,'RSA')
        return jsonify(body),status

    messages = {'invalid_signature':'签名校验失败，请检查商户 ID 和签名密钥','merchant_unavailable':'商户不存在或已停用',
                'rsa_required':'此商户仅允许 RSA 签名','order_not_found':'订单号不存在','order_conflict':'同一商户订单号的参数发生变化',
                'channel_not_integrated':'该收款渠道尚未接入','payment_not_configured':'收款渠道尚未配置','wechat_https_required':'微信支付需要平台使用公网 HTTPS 站点地址','wechat_no_auth':'微信支付商户号未开通该产品','wechat_app_id_mismatch':'微信支付 AppID 与商户号未绑定','invalid_merchant_callback':'商户回调地址未获允许','amount_exceeded':'金额超过平台单笔上限'}
    def endpoint(version, action):
        def decorate(fn):
            @wraps(fn)
            def wrapped():
                try:
                    params = parameters()
                    account = authenticated(params,version)
                    return fn(params,account)
                except (ApiError,ValueError) as exc:
                    code = exc.code if isinstance(exc,ApiError) else str(exc)
                    trace('merchant.request','epay_request_rejected',{'action':action,'version':version,'error':code},user_id=getattr(g,'user',{}).get('id'),accepted=False)
                    return result({'code':-3 if code in {'invalid_signature','merchant_unavailable','rsa_required'} else -1,'msg':app.extensions['operations'].rules.read()['blocked_message'] if code=='product_blocked' else messages.get(code,code),'error':code},version)
            return wrapped
        return decorate

    def create(params,account,version,page=False):
        merchant_no=params.get('out_trade_no','')
        if not re.fullmatch(r'[A-Za-z0-9._|\-]{1,64}',merchant_no): raise ApiError('invalid_order_number')
        name=params.get('name','').strip()
        if not 1<=len(name)<=127: raise ApiError('invalid_subject')
        amount=money(params.get('money',''))
        if Decimal(amount)>Decimal(store.setting('max_amount',money(app.config['TEST_MAX_AMOUNT']))):raise ApiError('amount_exceeded')
        epay_type=params.get('type','alipay')
        channel=EPAY_CHANNELS.get(epay_type)
        if not channel: raise ApiError('channel_not_integrated')
        callback=params.get('notify_url','')
        parsed=validate_callback(callback,app.extensions['callback_hosts'],app.extensions['callback_local_origins'])
        return_url=params.get('return_url','')
        if return_url:
            returned=urlsplit(return_url)
            if returned.scheme!=parsed.scheme or returned.netloc!=parsed.netloc or returned.username or returned.password or returned.fragment or any(ord(c)<33 for c in return_url) or len(return_url)>1024:
                raise ApiError('invalid_merchant_return')
        if len(params.get('param',''))>512: raise ApiError('invalid_param')
        if params.get('clientip'):
            try: ipaddress.ip_address(params['clientip'])
            except ValueError: raise ApiError('invalid_client_ip') from None
        normalized={**params,'name':name,'money':amount,'type':epay_type}
        record=merchants.reserve(account,normalized,version)
        number=record['platform_no']
        try: response=app.make_response(create_payment({'out_trade_no':number,'subject':name,'amount':amount,'channel':channel,'product':'page','notify_url':callback}))
        except WechatError as exc: raise ApiError(exc.code) from None  # the merchant gets the same envelope as for any other refusal
        if response.status_code>=400: raise ApiError((response.get_json(silent=True) or {}).get('error','payment_creation_failed'))
        url=merchants.checkout_url(record,public_origin())
        trace('merchant.create','epay_order_created',{'pid':account['pid'],'merchant_order_no':merchant_no,'version':version},number,account['user_id'])
        if page: return redirect(url,303)
        return result({'code':0,'trade_no':number,'pay_type':'jump','pay_info':url} if version=='v2' else {'code':1,'msg':'success','trade_no':number,'payurl':url},version)

    @app.post('/mapi.php')
    @endpoint('v1','create')
    def v1_create(params,account): return create(params,account,'v1')

    @app.route('/submit.php',methods=['GET','POST'])
    @endpoint('v1','submit')
    def v1_submit(params,account): return create(params,account,'v1',True)

    @app.post('/api/pay/create')
    @endpoint('v2','create')
    def v2_create(params,account): return create(params,account,'v2')

    @app.route('/api/pay/submit',methods=['GET','POST'])
    @endpoint('v2','submit')
    def v2_submit(params,account): return create(params,account,'v2',True)

    def info(account):
        with store.connect() as db: count=db.execute('SELECT count(*) FROM merchant_orders WHERE user_id=?',(account['user_id'],)).fetchone()[0]
        access=app.extensions['operations'].access(account['user_id'])
        return {'pid':account['pid'],'status':1,'pay_status':int(access['ready']),'order_num':count}

    @app.post('/api/merchant/info')
    @endpoint('v2','info')
    def v2_info(params,account): return result({'code':0,**info(account)},'v2')

    @app.post('/api/pay/query')
    @endpoint('v2','query')
    def v2_query(params,account):
        order=merchants.resolve(account,params)
        if not order: raise ApiError('order_not_found')
        return result({'code':0,**merchants.query(order)},'v2')

    @app.route('/api.php',methods=['GET','POST'])
    def v1_api():
        route=request.args.get('s')
        if route:
            routes={'pay/create':v2_create,'pay/submit':v2_submit,'pay/query':v2_query,'merchant/info':v2_info}
            if route not in routes: return result({'code':-5,'msg':'接口方法不存在'},'v2')
            return routes[route]()
        try:
            params=parameters();account=authenticated(params,'v1',legacy=True)
            action=params.get('act',request.args.get('act'))
            if action=='query': return result({'code':1,'active':1,**info(account)},'v1')
            if action=='order':
                order=merchants.resolve(account,params)
                if not order: raise ApiError('order_not_found')
                return result({'code':1,**merchants.query(order)},'v1')
            raise ApiError('unsupported_method')
        except (ApiError,ValueError) as exc:
            code=exc.code if isinstance(exc,ApiError) else str(exc)
            return result({'code':-3,'msg':app.extensions['operations'].rules.read()['blocked_message'] if code=='product_blocked' else messages.get(code,code),'error':code},'v1')

    def checkout(token):
        value=merchants.cashier(token)
        if not value: raise ApiError('cashier_not_found',404)
        record,order=value
        g.user={'id':order['user_id'],'role':'user'}
        return record,order

    def view(record,order,inline=False):
        from .cashier import qr_inline
        expired=order['status']=='CREATED' and order['expires_at']<=int(time.time())
        data={'trade_no':record['platform_no'],'out_trade_no':record['merchant_no'],'subject':order['subject'],
                'amount':order['amount'],'channel':order['channel'],'status':'EXPIRED' if expired else order['status'],
                'paid':bool(order['paid_at']),'expires_at':order['expires_at'],'qr_ready':bool(order['cashier_qr']),
                'return_available':bool(record['return_url']), 'parent_origins':frame_origins(order['notify_url']), 'parent_origin':urlsplit(order['notify_url']).scheme+'://'+urlsplit(order['notify_url']).netloc}
        if inline: data['qr_svg']=qr_inline(order)  # the finished image, so that the page needs no second request for it
        return data

    @app.get('/api/cashier/<token>')
    def checkout_state(token):
        record,order=checkout(token)
        return jsonify(view(record,order,inline=request.args.get('qr')=='1'))

    @app.post('/api/cashier/<token>/prepare')
    def checkout_prepare(token):
        record,order=checkout(token)
        if request.headers.get('Origin') and request.headers['Origin'] not in {merchants.site.read()['origin'],public_origin()}: raise ApiError('invalid_origin',403)
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict) or set(payload)-{'refresh'}: raise ApiError('invalid_json')
        if not order['paid_at']:
            prepare_qr(order['out_trade_no'],payload)
        return jsonify(view(record,store.order(order['out_trade_no']),inline=True))

    @app.get('/api/cashier/<token>/qr.svg')
    def checkout_qr(token):
        from .qrcode import payment_qr_svg
        _,order=checkout(token)
        if order['paid_at'] or order['status']!='CREATED' or order['expires_at']<=int(time.time()): raise ApiError('payment_order_expired',409)
        if not order['cashier_qr']: raise ApiError('cashier_qr_unavailable',404)
        return Response(payment_qr_svg(order['cashier_qr'],order['channel']),mimetype='image/svg+xml')

    @app.get('/api/cashier/<token>/return')
    def checkout_return(token):
        record,order=checkout(token)
        if not order['paid_at'] or not record['return_url']: raise ApiError('payment_not_confirmed',409)
        return redirect(record['return_url']+('?' if '?' not in record['return_url'] else '&')+urlencode(merchants.notification(order['out_trade_no'])),303)
