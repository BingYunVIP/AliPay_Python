"""Local unit, database and permission checks; no external gateway or fake payment."""
import concurrent.futures
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from decimal import Decimal
from web.app import create_app
from web.store import Store,present_order
from web.payment_terms import payment_amount,two,matches,overlaps,address_key
from web.operations import rate_fee
from web.security import validate_admin_identity

ORIGIN='http://127.0.0.1:888'
PASSWORD='Secure12!Abc'

class CurrentPlatform(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='bypay-current-')
        self.db=Path(self.tmp.name)/'application.sqlite3'
        self.app=create_app({'DATABASE':str(self.db),'BYPAY_LOG_FILE':self.tmp.name+'/trace','PUBLIC_BASE_URL':ORIGIN,'BYPAY_QR_PREFETCH':'0','FRONTEND_DIST':str(Path(__file__).resolve().parents[1]/'frontend/dist')})
        self.store=self.app.extensions['store'];self.client=self.app.test_client()
        self.headers={'Origin':ORIGIN}
    def tearDown(self):self.tmp.cleanup()
    def initial_login(self):
        r=self.client.post('/api/auth/login',json={'email':'admin@pay.com','password':'Admin123'},headers=self.headers)
        self.assertEqual(r.status_code,200)
        self.headers['X-CSRF-Token']=r.json['csrf_token'];return r
    def ready_admin(self):
        self.initial_login()
        r=self.client.post('/api/auth/initial-credentials',json={'email':'owner@example.com','current_password':'Admin123','password':PASSWORD,'password_confirmation':PASSWORD},headers=self.headers)
        self.assertEqual(r.status_code,200)
        r=self.client.post('/api/auth/login',json={'email':'owner@example.com','password':PASSWORD,'password_confirmation':PASSWORD},headers={'Origin':ORIGIN})
        self.assertEqual(r.status_code,200);self.headers['X-CSRF-Token']=r.json['csrf_token']
        return self.store.user_by_email('owner@example.com')
    def test_bootstrap_and_hashed_password(self):
        user=self.store.user_by_email('admin@pay.com')
        self.assertEqual(user['role'],'admin');self.assertEqual(user['must_change_credentials'],1)
        self.assertNotEqual(user['password'],'Admin123')
        self.assertEqual(self.initial_login().json['user']['must_change_credentials'],True)
    def test_initial_admin_cannot_use_protected_routes(self):
        self.initial_login()
        for path in ['/api/admin/channels','/api/console/orders','/api/merchant/config','/api/admin/rates/settings']:
            r=self.client.get(path);self.assertEqual(r.status_code,403,path);self.assertEqual(r.json['error'],'credentials_change_required')
    def test_initial_me_only_discloses_identity(self):
        self.initial_login();data=self.client.get('/api/auth/me').json
        self.assertNotIn('api_key',data);self.assertNotIn('access',data)
    def test_invalid_setup_is_atomic(self):
        self.initial_login();r=self.client.post('/api/auth/initial-credentials',json={'email':'admin@pay.com','current_password':'Admin123','password':PASSWORD,'password_confirmation':PASSWORD},headers=self.headers)
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.store.user_by_email('admin@pay.com')['must_change_credentials'],1)
    def test_setup_requires_csrf(self):
        self.initial_login();r=self.client.post('/api/auth/initial-credentials',json={'email':'owner@example.com','current_password':'Admin123','password':PASSWORD,'password_confirmation':PASSWORD},headers={'Origin':ORIGIN})
        self.assertEqual(r.status_code,403)
    def test_setup_requires_current_password(self):
        self.initial_login();r=self.client.post('/api/auth/initial-credentials',json={'email':'owner@example.com','current_password':'incorrect','password':PASSWORD,'password_confirmation':PASSWORD},headers=self.headers)
        self.assertEqual(r.status_code,401)
    def test_setup_revokes_every_session_and_survives_restart(self):
        self.initial_login();uid=self.store.user_by_email('admin@pay.com')['id'];other,_=self.store.session(uid)
        r=self.client.post('/api/auth/initial-credentials',json={'email':'owner@example.com','current_password':'Admin123','password':PASSWORD,'password_confirmation':PASSWORD},headers=self.headers)
        self.assertEqual(r.status_code,200);self.assertIsNone(self.store.authenticate(other));self.assertEqual(self.client.get('/api/auth/me').status_code,401)
        self.store.bootstrap();self.assertIsNone(self.store.user_by_email('admin@pay.com'))
        self.assertEqual(self.store.user_by_email('owner@example.com')['must_change_credentials'],0)
    def test_only_one_admin_database_constraint(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:db.execute("INSERT INTO users(email,password,role,created_at) VALUES ('another@example.com','hash','admin',?)",(int(time.time()),))
    def test_three_default_disabled_channels(self):
        rows=self.app.extensions['channels'].all();self.assertEqual([r['name'] for r in rows],['支付宝','微信支付','BSC_USDT']);self.assertFalse(any(r['enabled'] for r in rows))
    def test_delete_default_does_not_recreate_on_restart(self):
        self.ready_admin();row=self.app.extensions['channels'].all()[0]
        r=self.client.delete('/api/admin/channel-configs/'+str(row['config_id']),headers=self.headers);self.assertEqual(r.status_code,200)
        from web.channels import ChannelStore
        ChannelStore(self.store,self.app.extensions['alipay_config'],self.app.extensions['site_settings'].urls)
        self.assertEqual(len(self.app.extensions['channels'].all()),2)
    def test_incomplete_candidate_does_not_save_or_enable(self):
        self.ready_admin();row=self.app.extensions['channels'].all()[0]
        r=self.client.put('/api/admin/channel-configs/'+str(row['config_id']),json={'name':'未完成配置','version':row['version'],'revision':row['revision'],'fields':{}},headers=self.headers)
        self.assertEqual(r.status_code,400);after=self.app.extensions['channels'].describe('alipay',row['config_id'])
        self.assertEqual(after['name'],row['name']);self.assertEqual(after['version'],row['version']);self.assertFalse(after['enabled'])
    def test_channel_pagination_shape(self):
        self.ready_admin();r=self.client.get('/api/admin/channels?page=1&page_size=9')
        self.assertEqual((r.json['total'],r.json['page_size']),(3,9))
    def test_empty_market_does_not_invent_quotes(self):
        self.ready_admin();r=self.client.get('/api/admin/rates');self.assertEqual(r.json['total'],0);self.assertIsNone(r.json['rates']['USDT'])
        with self.assertRaises(ValueError):self.app.extensions['pricing'].quote('USDT')
    def test_pricing_defaults_do_not_expose_api_key(self):
        self.ready_admin();s=self.client.get('/api/admin/rates/settings').json['settings'];self.assertEqual(s['interval_seconds'],60);self.assertEqual(s['fixed_rates'],{'USDT':'','USDC':''});self.assertNotIn('api_key',s)
    def test_crypto_policy_single_amount_source(self):
        self.ready_admin();cfg=self.client.get('/api/admin/crypto-policy').json['settings'];cfg.update(min_amount='0.10',max_amount='100.00')
        r=self.client.put('/api/admin/crypto-policy',json=cfg,headers=self.headers);self.assertEqual(r.status_code,200)
        self.assertEqual(self.app.extensions['site_settings'].read()['max_amount'],'100.00')
        self.assertEqual(self.client.put('/api/admin/crypto-policy',json=cfg,headers=self.headers).status_code,409)
    def test_unsigned_current_merchant_routes_are_rejected(self):
        for version in ['v1','v2']:
            for action in ['create','query','close','refund']:
                r=self.client.post(f'/api/merchant/{version}/orders/{action}',data={'pid':'999','out_trade_no':'UNSIGNED'})
                self.assertNotEqual(r.status_code,404);self.assertNotIn(r.json.get('code'),[0,1])
    def test_legacy_addresses_are_not_redirected(self):
        for path in ['/dashboard?tab=channels','/api.php','/mapi.php','/submit.php','/admin/uset.php','/alipay/return']:
            r=self.client.get(path);self.assertEqual(r.status_code,404,path);self.assertNotIn('Location',r.headers)
    def test_current_routes_refresh(self):
        if not (Path(__file__).resolve().parents[1]/'frontend/dist/index.html').exists():self.skipTest('Build current frontend before route document checks')
        for path in ['/admin/security/setup','/admin/overview','/admin/channels','/admin/rates/history','/admin/settings/nodes','/user/orders']:
            response=self.client.get(path);self.assertEqual(response.status_code,200,path);response.close()
    def test_user_cannot_read_admin_configuration(self):
        self.ready_admin();self.store.register('merchant@example.com','Merchant123!')
        c=self.app.test_client();r=c.post('/api/auth/login',json={'email':'merchant@example.com','password':'Merchant123!'},headers={'Origin':ORIGIN});self.assertEqual(r.status_code,200)
        self.assertEqual(c.get('/api/admin/channels').status_code,403);self.assertEqual(c.get('/api/admin/rates/settings').status_code,403)
    def test_duplicate_registration_database_race(self):
        def register(_):
            try:self.store.register('same@example.com','Merchant123!');return 'created'
            except sqlite3.IntegrityError:return 'duplicate'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:outcomes=list(pool.map(register,range(12)))
        self.assertEqual(outcomes.count('created'),1);self.assertEqual(outcomes.count('duplicate'),11)
    def test_rate_limit_is_shared_and_atomic(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:self.store.rate_limit('parallel',5,60),range(20)))
        self.assertEqual(sum(results),5)
    def test_fee_snapshot_on_created_order_is_not_actual_income(self):
        self.ready_admin();user=self.store.register('merchant@example.com','Merchant123!')
        self.app.extensions['operations'].ensure(user['id'])
        cfg={'app_id':'','seller_id':'','channel_version':1,'expires_at':int(time.time())+900}
        order=self.store.create_order(user['id'],{'out_trade_no':'DB_CHECK_001','subject':'数据库约束检查','amount':'42.00','product':'page','channel':'alipay'},'',cfg)
        with self.store.connect() as db:bill=dict(db.execute('SELECT * FROM financial_orders WHERE number=?',(order['out_trade_no'],)).fetchone())
        self.assertEqual(bill['fee'],105)
        with self.app.app_context():shown=present_order(order)
        self.assertEqual(shown['fee_cny'],'1.05');self.assertIsNone(shown['net_income_cny'])
        self.assertNotIn('fixed_rate_snapshot',shown)
    def test_group_change_only_changes_future_snapshot(self):
        self.ready_admin();user=self.store.register('merchant@example.com','Merchant123!');ops=self.app.extensions['operations'];ops.ensure(user['id'])
        with self.store.connect() as db:ops.snapshot(db,user['id'],'SNAPSHOT_A','42.00')
        self.store.groups.assign(user['id'],2,1)
        with self.store.connect() as db:
            ops.snapshot(db,user['id'],'SNAPSHOT_B','42.00');rows={r['number']:r['fee'] for r in db.execute("SELECT number,fee FROM financial_orders WHERE number LIKE 'SNAPSHOT_%'")}
        self.assertEqual(rows,{'SNAPSHOT_A':105,'SNAPSHOT_B':42})
    def test_ledger_is_balanced_and_immutable(self):
        self.ready_admin();ops=self.app.extensions['operations']
        with self.store.connect() as db:ops.journal(db,'constraint-entry','constraint','check',1,[('checking',0,100),('checking',1,-100)])
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:db.execute("UPDATE ledger_lines SET cents=0 WHERE event_id='constraint-entry'")
        with self.assertRaises(ValueError):
            with self.store.connect() as db:ops.journal(db,'bad-entry','constraint','check',1,[('checking',0,100)])
    def test_untrusted_forwarding_header_is_ignored(self):
        with self.app.test_request_context('/',headers={'X-Forwarded-For':'8.8.8.8','CF-Connecting-IP':'1.1.1.1'},environ_base={'REMOTE_ADDR':'192.168.20.10'}):
            from flask import request
            self.assertEqual(self.app.extensions['operations'].rules.client_ip(request),'192.168.20.10')
    def test_explicit_trusted_chain_strips_only_trusted_hops(self):
        rules=self.app.extensions['operations'].rules;cfg=rules.read();rules.save({'version':cfg['version'],'trusted_proxies':['127.0.0.1/32']})
        with self.app.test_request_context('/',headers={'X-Forwarded-For':'1.1.1.1, 8.8.8.8'},environ_base={'REMOTE_ADDR':'127.0.0.1'}):
            from flask import request
            self.assertEqual(rules.client_ip(request),'8.8.8.8')
    def test_setup_confirmation_is_checked_by_server(self):
        self.initial_login()
        r=self.client.post('/api/auth/initial-credentials',json={'email':'owner@example.com','current_password':'Admin123','password':PASSWORD,'password_confirmation':'different'},headers=self.headers)
        self.assertEqual(r.status_code,400);self.assertEqual(r.json['error'],'password_confirmation_mismatch')
    def test_rpc_key_is_not_transferred_to_new_endpoint(self):
        nets=self.app.extensions['crypto_networks'];cfg=nets.get('BSC')
        first=nets.save('BSC',{'revision':cfg['revision'],'rpc_url':'https://node-one.example.com','confirmations':15,'api_key':'unit-key-never-sent'})
        second=nets.save('BSC',{'revision':first['revision'],'rpc_url':'https://node-two.example.com','confirmations':15,'api_key':''})
        self.assertEqual(second['api_key'],'')
    def test_scanner_lease_is_exclusive_and_recoverable(self):
        from web.crypto_scanner import CryptoScanner
        scanner=self.app.extensions['crypto_scanner'];other=CryptoScanner(self.store,self.app.extensions['channels'],self.app.extensions['crypto_networks'],lambda *a,**k:None)
        with scanner._leased(3,3) as held:
            self.assertTrue(held)
            with other._leased(3,3) as acquired:self.assertFalse(acquired)
            scanner._renew()
        with other._leased(3,3) as acquired:self.assertTrue(acquired)
    def test_cursor_progress_and_explicit_reorg_rewind(self):
        scanner=self.app.extensions['crypto_scanner']
        scanner._save_cursor('unit-cursor',100);scanner._save_cursor('unit-cursor',99)
        self.assertEqual(scanner._cursor('unit-cursor'),100)
        scanner._save_cursor('unit-cursor',80,rewind=True);self.assertEqual(scanner._cursor('unit-cursor'),80)
    def _pricing_fixture(self,fixed='6.00',market='7.00'):
        pricing=self.app.extensions['pricing'];now=int(time.time())
        cfg={**pricing.settings(private=True),'fixed_rates':{'USDT':fixed,'USDC':''}}
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO console_settings VALUES ('pricing',?)",(json.dumps(pricing.cipher.encrypt(json.dumps(cfg).encode()).decode()),))
            db.execute("INSERT INTO rate_snapshots(asset,rate,quoted_at,observed_at,source) VALUES ('USDT',?,?,?,'isolated calculation fixture')",(market,now,now))
        return pricing.quote('USDT')
    def _crypto_payment(self,quote,amount='42.00'):
        now=int(time.time())
        return {'network':'BSC','asset':'USDT','asset_code':'BSC_USDT','address':'0x'+'1'*40,'crypto_amount':payment_amount(amount,quote['rate']),
            'expires_at':now+900,'lookback_hours':3,'match_mode':'exact','rate_cny':quote['rate'],'rate_source':quote['source'],
            'rate_updated_at':quote['updated_at'],'rate_observed_at':quote['observed_at'],'fixed_rate_snapshot':quote['fixed_rate'],
            'market_rate_snapshot':quote['market_rate'],'rate_snapshot_at':quote['market_updated_at']}
    def test_fixed_rate_priority_and_immutable_order_terms(self):
        quote=self._pricing_fixture();self.assertEqual((quote['rate'],quote['market_rate']),('6.00','7.00'))
        user=self.store.register('terms@example.com','Merchant123!');self.app.extensions['operations'].ensure(user['id'])
        cfg={'payment_config_id':3,'channel_version':3}
        order=self.store.create_crypto_order(user['id'],{'out_trade_no':'UNIT_TERMS','subject':'Isolated terms check','amount':'42.00'},cfg,self._crypto_payment(quote))
        self._pricing_fixture('5.00','8.00')
        frozen=self.store.order(order['out_trade_no'])
        self.assertEqual((frozen['amount'],frozen['crypto_amount'],frozen['fixed_rate_snapshot'],frozen['market_rate_snapshot']),('42.00','7.00','6.00','7.00'))
        self.assertEqual(frozen['status'],'CREATED');self.assertIsNone(frozen['paid_at'])
        with self.app.app_context():self.assertEqual(present_order(frozen)['fee_cny'],'1.05')
    def test_automatic_quote_example_retains_cny_principal(self):
        quote=self._pricing_fixture('','7.00');payment=self._crypto_payment(quote,'100.00')
        self.assertEqual(payment['crypto_amount'],'14.29');self.assertEqual(quote['fixed_rate'],'')
    def test_concurrent_wallet_reservation_has_one_winner(self):
        from web.crypto import CryptoError
        quote=self._pricing_fixture();payment=self._crypto_payment(quote)
        user=self.store.register('race@example.com','Merchant123!');self.app.extensions['operations'].ensure(user['id'])
        def reserve(index):
            try:
                self.store.create_crypto_order(user['id'],{'out_trade_no':f'UNIT_POOL_{index}','subject':'Reservation constraint check','amount':'42.00'},
                    {'payment_config_id':3,'channel_version':3},payment)
                return 'reserved'
            except CryptoError:return 'occupied'
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:results=list(pool.map(reserve,range(20)))
        self.assertEqual(results.count('reserved'),1);self.assertEqual(results.count('occupied'),19)
    def test_current_v1_md5_signed_info(self):
        from bypay.epay_protocol import sign
        self.ready_admin();merchants=self.app.extensions['merchants'];cfg=merchants.describe(1)
        params={'pid':str(cfg['merchant_id']),'sign_type':'MD5'};params['sign']=sign(params,cfg['md5_key'],'MD5')
        response=self.client.post('/api/merchant/v1/info',data=params)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['code'],1)
    def test_current_v2_rsa_signed_info_and_response(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from bypay.epay_protocol import sign,verify,public_pem
        self.ready_admin();key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        merchants=self.app.extensions['merchants'];cfg=merchants.update(1,{'merchant_public_key':public_pem(key)})
        params={'pid':str(cfg['merchant_id']),'sign_type':'RSA','timestamp':str(int(time.time()))};params['sign']=sign(params,key,'RSA')
        response=self.client.post('/api/merchant/v2/info',data=params)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['code'],0)
        self.assertTrue(verify(response.json,cfg['platform_public_key'],'RSA'))
    def test_settlement_is_cny_only(self):
        ops=self.app.extensions['operations']
        with self.assertRaises(ValueError):ops.beneficiary(1,{'channel':'crypto','network':'BSC','asset':'USDT','address':'0x'+'1'*40})
        ops.beneficiary(1,{'channel':'bank','account':'unit-account-not-used-for-transfer','name':'Constraint check'})
        with self.store.connect() as db:
            columns={r['name'] for r in db.execute('PRAGMA table_info(settlements)')}
        self.assertFalse(any(name.startswith('crypto_') for name in columns))
    def test_merchant_server_and_payer_addresses_are_distinct(self):
        cfg={'app_id':'','seller_id':'','channel_version':1,'merchant_server_ip':'192.0.2.1'}
        order=self.store.create_order(1,{'out_trade_no':'IP_SEPARATION','subject':'Provenance check','amount':'1.00','product':'page'},'',cfg)
        self.assertEqual(order['client_ip'],'');self.assertEqual(order['merchant_server_ip'],'192.0.2.1')
        self.store.capture_payer_ip(order['out_trade_no'],'198.51.100.2')
        self.store.capture_payer_ip(order['out_trade_no'],'203.0.113.3')
        after=self.store.order(order['out_trade_no'])
        self.assertEqual(after['client_ip'],'198.51.100.2');self.assertEqual(after['merchant_server_ip'],'192.0.2.1')
    def test_non_cny_merchant_invoice_is_rejected(self):
        from bypay.epay_protocol import sign
        self.ready_admin();cfg=self.app.extensions['merchants'].describe(1)
        params={'pid':str(cfg['merchant_id']),'sign_type':'MD5','currency':'USD'};params['sign']=sign(params,cfg['md5_key'],'MD5')
        r=self.client.post('/api/merchant/v1/orders/create',data=params)
        self.assertEqual(r.status_code,400);self.assertEqual(r.json['error'],'unsupported_order_currency')
    def test_registration_review_and_default_group_are_atomic(self):
        user=self.store.register('review@example.com','Merchant123!')
        self.app.extensions['operations'].ensure(user['id'])
        with self.store.connect() as db:
            row=db.execute('SELECT u.role,u.group_id,a.review,a.review_required FROM users u JOIN merchant_access a ON a.user_id=u.id WHERE u.id=?',(user['id'],)).fetchone()
        self.assertEqual(tuple(row),('user',1,'pending',1))
        rules=self.app.extensions['operations'].rules;cfg=rules.read()
        with self.assertRaises(ValueError):rules.save({'version':cfg['version'],'registration_review':False})
    def test_manual_merchant_starts_approved_in_ordinary_group(self):
        user=self.store.register('manual@example.com','Merchant123!',manual=True)
        state=self.app.extensions['operations'].ensure(user['id'])
        self.assertEqual((state['review'],state['review_required'],state['fee_due']),('approved',0,0))
        self.assertEqual(self.store.user_by_email('manual@example.com')['group_id'],1)
    def test_manual_merchant_http_domain_and_deposit_sources(self):
        self.ready_admin()
        r=self.client.post('/api/admin/users',json={'email':'manual-http@example.com','password':'Merchant14!Abc','domains':['https://shop.example.com/catalog'],
            'group_id':1,'pay_enabled':True,'disabled':False,'deposit':'10.00'},headers=self.headers)
        self.assertEqual(r.status_code,201,r.json)
        user=r.json['user'];self.assertEqual(user['role'],'user')
        self.assertIn('shop.example.com',set(self.app.extensions['callback_hosts']))
        with self.store.connect() as db:
            state=db.execute('SELECT review,review_required FROM merchant_access WHERE user_id=?',(user['id'],)).fetchone()
            deposit=db.execute('SELECT source,cents FROM deposits WHERE user_id=?',(user['id'],)).fetchone()
        self.assertEqual(tuple(state),('approved',0));self.assertEqual(tuple(deposit),('manual',1000))
    def test_order_list_detail_and_export_enforce_role_fields(self):
        self.ready_admin();user=self.store.register('fields@example.com','Merchant123!',manual=True)
        self.app.extensions['merchants'].ensure(user['id'])
        self.store.create_order(user['id'],{'out_trade_no':'CHECK_FIELDS_001','subject':'Unfunded field projection check','amount':'42.00','product':'page'},'',{'app_id':'','seller_id':'','channel_version':1})
        admin=self.client.get('/api/console/orders').json['orders'][0]
        self.assertEqual(admin['merchant_email'],'fields@example.com');self.assertIn('fixed_rate_snapshot',admin)
        detail=self.client.get('/api/v1/payments/CHECK_FIELDS_001').json['order']
        self.assertIn('market_rate_snapshot',detail);self.assertEqual(detail['merchant_order_no'],'CHECK_FIELDS_001')
        header=self.client.get('/api/console/orders/export').get_data(as_text=True).splitlines()[0]
        self.assertIn('merchant_id',header);self.assertIn('merchant_email',header);self.assertIn('market_rate_snapshot',header)
        c=self.app.test_client();c.post('/api/auth/login',json={'email':'fields@example.com','password':'Merchant123!'},headers={'Origin':ORIGIN})
        ordinary=c.get('/api/console/orders').json['orders'][0]
        self.assertNotIn('fixed_rate_snapshot',ordinary);self.assertNotIn('merchant_email',ordinary)
        self.assertEqual(ordinary['order_amount'],'42.00');self.assertEqual(ordinary['fee_cny'],'1.05');self.assertIsNone(ordinary['net_income_cny'])
        header=c.get('/api/console/orders/export').get_data(as_text=True).splitlines()[0]
        self.assertIn('order_amount',header);self.assertIn('payment_amount',header);self.assertNotIn('market_rate_snapshot',header)
    def test_setup_accepts_long_password_with_three_categories(self):
        self.initial_login();password='LongPassword123456'
        r=self.client.post('/api/auth/initial-credentials',json={'email':'long-owner@example.com','current_password':'Admin123','password':password,'password_confirmation':password},headers=self.headers)
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.client.get('/api/auth/me').status_code,401)
        r=self.client.post('/api/auth/login',json={'email':'long-owner@example.com','password':password},headers={'Origin':ORIGIN})
        self.assertEqual(r.status_code,200);self.assertFalse(r.json['user']['must_change_credentials'])
    def test_foreign_keys_and_integrity(self):
        with self.store.connect() as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

class AmountRules(unittest.TestCase):
    def test_confirmed_examples(self):
        self.assertEqual(payment_amount('42.00','6.00'),'7.00');self.assertEqual(rate_fee(4200,'2.50'),105)
        self.assertEqual(payment_amount('100.00','7.00'),'14.29');self.assertEqual(rate_fee(10000,'2.50'),250)
    def test_half_up_not_binary_round(self):self.assertEqual(two('1.005'),'1.01');self.assertEqual(two('2.675'),'2.68')
    def test_zero_rounded_payment_is_rejected(self):
        with self.assertRaises(ValueError):payment_amount('0.01','7.00')
    def test_exact_and_no_underpayment_rounding(self):
        self.assertTrue(matches('14.2900','14.29'));self.assertFalse(matches('14.289','14.29'));self.assertFalse(matches('14.30','14.29'))
    def test_prefix_boundary(self):self.assertTrue(matches('14.291','14.29','prefix'));self.assertFalse(matches('14.30','14.29','prefix'));self.assertFalse(matches('1000','100.00','prefix'))
    def test_round_boundary(self):self.assertTrue(matches('14.285','14.29','round'));self.assertFalse(matches('14.295','14.29','round'))
    def test_cross_mode_overlaps(self):self.assertTrue(overlaps('14.29','exact','14.29','round'));self.assertTrue(overlaps('14.29','prefix','14.30','round'));self.assertFalse(overlaps('14.29','exact','14.30','exact'))
    def test_case_sensitive_addresses(self):self.assertEqual(address_key('BSC','0xAbC'),'0xabc');self.assertNotEqual(address_key('SOL','AbC'),address_key('SOL','abc'))
    def test_admin_policy_accepts_any_three_categories_and_long_passwords(self):
        for value in [PASSWORD,'Abcdefgh1234','UPPERCASE12!','lowercase12!','Abcdefghijk!','LongPassword123456','Aa1'+'x'*125]:
            with self.subTest(category_case=value[:3]):validate_admin_identity('owner@example.com',value)
    def test_admin_policy_rejects_short_weak_or_whitespace(self):
        for value in ['Abcdefg123!','abcdefghijkl','ABCDEFGHIJKL','Abcdefghijkl','lowercase12345','UPPERCASE12345','aaaa!!!!!aaa','Abcd123! xyZ']:
            with self.subTest(case=value[:3]),self.assertRaises(ValueError):validate_admin_identity('owner@example.com',value)
    def test_admin_policy_matches_login_length_limit(self):
        for value in ['Aa1'+'x'*126,None,12345]:
            with self.subTest(kind=type(value).__name__),self.assertRaises(ValueError):validate_admin_identity('owner@example.com',value)

if __name__=='__main__':unittest.main()
