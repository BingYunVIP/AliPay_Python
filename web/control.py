"""Current administrator workflows; probes never create payment transactions."""
import json
import secrets
from flask import g,jsonify,request
from bypay.constants import GATEWAY_BY_ENV
from .channels import FIELDS,SECRETS,REQUIRED,INTEGRATED,ChannelError

def install_control(app,auth,trace,ApiError,alipay_client,wechat_client):
    store=app.extensions['store'];channels=app.extensions['channels']
    pricing=app.extensions['pricing'];policy=app.extensions['crypto_policy'];networks=app.extensions['crypto_networks']

    def probe(channel,cfg):
        if channel=='crypto':
            net=networks.check(cfg['network'])
            if not net.get('verified'):return net
            result=app.extensions['crypto_gateway'].check(cfg,networks.get(cfg['network']))
            result['network_revision']=net.get('revision')
            return result
        if channel=='wechat':return wechat_client(cfg).check()
        if channel=='alipay':
            config={**cfg,**app.extensions['site_settings'].urls(),'gateway':GATEWAY_BY_ENV[cfg.get('env','production')],'sign_type':'RSA2','charset':'utf-8'}
            result=alipay_client(config).query(out_trade_no='BYPAY_CHECK_'+secrets.token_hex(12))
            passed=result.get('sub_code')=='ACQ.TRADE_NOT_EXIST'
            return {'authenticated':passed,'verified':passed,'provider_code':result.get('sub_code') or result.get('code','unknown_response')}
        return {'authenticated':False,'verified':False,'provider_code':'payments_not_enabled'}

    app.extensions['channel_probe']=probe

    @app.get('/api/admin/rates')
    @auth(session_only=True,admin=True)
    def rates_history():
        try:page=int(request.args.get('page','1'))
        except ValueError:raise ApiError('invalid_page') from None
        if not 1<=page<=100000:raise ApiError('invalid_page')
        return jsonify(**pricing.history(page))

    @app.get('/api/admin/rates/settings')
    @auth(session_only=True,admin=True)
    def rates_settings():return jsonify(settings=pricing.settings())

    @app.put('/api/admin/rates/settings')
    @auth(session_only=True,admin=True)
    def save_rates_settings():
        try:result=pricing.save(request.get_json(silent=True))
        except ValueError as exc:raise ApiError(str(exc),409 if str(exc)=='settings_conflict' else 400) from None
        except TypeError:raise ApiError('invalid_pricing_settings') from None
        trace('pricing.config','pricing_settings_saved',{'version':result['version']},user_id=g.user['id'])
        return jsonify(settings=result)

    @app.post('/api/admin/rates/sync')
    @auth(session_only=True,admin=True)
    def sync_rates():
        try:return jsonify(**pricing.sync())
        except ValueError as exc:raise ApiError(str(exc),503) from None

    @app.get('/api/admin/crypto-policy')
    @auth(session_only=True,admin=True)
    def get_crypto_policy():return jsonify(settings=policy.read())

    @app.put('/api/admin/crypto-policy')
    @auth(session_only=True,admin=True)
    def save_crypto_policy():
        try:result=policy.save(request.get_json(silent=True))
        except ValueError as exc:raise ApiError(str(exc),409 if str(exc)=='settings_conflict' else 400) from None
        trace('crypto.config','crypto_policy_saved',{'version':result['version']},user_id=g.user['id'])
        return jsonify(settings=result)

    @app.delete('/api/admin/channel-configs/<int:config_id>')
    @auth(session_only=True,admin=True)
    def delete_channel(config_id):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM channel_configs WHERE id=? AND archived_at IS NULL',(config_id,)).fetchone():raise ApiError('not_found',404)
            import time
            db.execute('UPDATE channel_configs SET archived_at=?,enabled=0,is_default=0,revision=revision+1 WHERE id=?',(int(time.time()),config_id))
        trace('console.channel','channel_config_deleted',{'config_id':config_id},user_id=g.user['id'])
        return jsonify(deleted=True)
