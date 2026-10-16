import base64
import hashlib
import json
import time
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
from decimal import Decimal

import httpx
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa,padding

from web.app import create_app
from bypay.alipay_client import AlipayClient
from web.mailing import render,default_template
from web.platform_rules import cents


class PlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.public=cls.private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        cls.secret=cls.private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.cfg={'TESTING':True,'PUBLIC_BASE_URL':'http://localhost:888','DATABASE':str(self.root/'web.sqlite3'),'BYPAY_ADMIN_EMAIL':'admin','BYPAY_ADMIN_PASSWORD':'admin','ALLOW_REGISTRATION':'true','ALIPAY_APP_ID':'2026000000000000','ALIPAY_SELLER_ID':'2088000000000000','ALIPAY_PRIVATE_KEY':self.secret,'ALIPAY_PUBLIC_KEY':self.public}
        self.app=create_app(self.cfg);self.store=self.app.extensions['store'];self.ops=self.app.extensions['operations'];self.mail=self.app.extensions['mailer'];self.bot=self.app.extensions['antibot']
        self.admin=self.app.test_client();login=self.admin.post('/api/auth/login',json={'email':'admin','password':'admin'}).json
        self.admin_headers={'Origin':'http://localhost:888','X-CSRF-Token':login['csrf_token']}
        self.client,self.user,self.headers=self.register('user@example.test')
        self.number=0
        guard=patch('socket.create_connection',side_effect=AssertionError('No external calls in unit tests'));guard.start();self.addCleanup(guard.stop)

    def register(self,email,**extra):
        client=self.app.test_client();response=client.post('/api/auth/register',json={'email':email,'password':'Test-password-123',**extra})
        self.assertEqual(response.status_code,200,response.json)
        return client,response.json['user']['id'],{'Origin':'http://localhost:888','X-CSRF-Token':response.json['csrf_token']}

    def policy(self,**updates):return self.ops.rules.save({**updates,'version':self.ops.rules.read()['version']})

    def create(self,client=None,headers=None,**updates):
        self.number+=1
        return (client or self.client).post('/api/v1/payments',json={'out_trade_no':f'PLATFORM_{self.number:08d}','amount':'100.00','subject':'测试商品','channel':'alipay','product':'page',**updates},headers=headers or self.headers)

    def paid(self,order):
        params={'out_trade_no':order['out_trade_no'],'total_amount':order['amount'],'trade_status':'TRADE_SUCCESS','trade_no':'20261016'+hashlib.sha256(order['out_trade_no'].encode()).hexdigest()[:18],'app_id':'2026000000000000','seller_id':'2088000000000000','sign_type':'RSA2'}
        params['sign']=base64.b64encode(self.private.sign(AlipayClient.build_sign_content(params).encode(),padding.PKCS1v15(),hashes.SHA256())).decode()
        response=self.app.test_client().post('/alipay/notify',data=params);self.assertEqual(response.data,b'success',response.data)

    def balances(self,user=None):return self.ops.account(user or self.user)['balances']
    def balanced(self):
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COALESCE(SUM(cents),0) FROM ledger_lines').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM (SELECT event_id FROM ledger_lines GROUP BY event_id HAVING sum(cents)!=0)').fetchone()[0],0)

    def smtp(self,**updates):return self.mail.save({'version':self.mail.config()['version'],'enabled':True,'username':'sender@qq.com','sender':'sender@qq.com','password':'smtp-private-authorization-code',**updates})

    def test_policy_validates_versions_money_rates_and_proxy_rules(self):
        with self.assertRaises(ValueError):self.policy(fee_rate='NaN')
        with self.assertRaises(ValueError):self.policy(fee_rate='101')
        with self.assertRaises(ValueError):self.policy(ip_source='x_real_ip')
        with self.assertRaises(ValueError):self.ops.rules.save({'version':-1,'deposit_enabled':True})
        self.assertEqual(self.ops.rules.read()['registration_fee'],'10.00')

    def test_smtp_secret_is_encrypted_and_test_send_uses_tls(self):
        result=self.smtp();self.assertNotIn('password',result)
        with self.store.connect() as db:self.assertNotIn('smtp-private-authorization-code',db.execute("SELECT value FROM console_settings WHERE key='smtp_settings'").fetchone()[0])
        with patch('web.mailing.smtplib.SMTP_SSL') as smtp:
            response=self.admin.post('/api/admin/mail/test',json={'recipient':'test@example.test'},headers=self.admin_headers)
            self.assertEqual(response.status_code,200,response.json)
            smtp.assert_called_once();self.assertEqual(smtp.call_args.args[:2],('smtp.qq.com',465))
            smtp.return_value.__enter__.return_value # client object itself is used in with block
            smtp.return_value.login.assert_called_once_with('sender@qq.com','smtp-private-authorization-code')
            smtp.return_value.send_message.assert_called_once()
        self.assertEqual(self.client.put('/api/admin/mail',json={},headers=self.headers).status_code,403)
        self.assertEqual(self.admin.put('/api/admin/mail',json={}).status_code,403)

    def test_template_placeholders_escape_values_and_preview_is_script_isolated(self):
        template=default_template('verification','zh')
        value=render(template,{'recipient_name':'<img src=x onerror=alert(1)>','verification_code':'123456'})
        self.assertIn('&lt;img',value['html'])
        with self.assertRaises(ValueError):render({'subject':'x','html':'{{__import__(x)}}'},{})
        response=self.admin.post('/api/admin/mail/preview',json={'template':template},headers=self.admin_headers)
        self.assertEqual(response.status_code,200);self.assertIn("default-src 'none'",response.json['html'])
        self.assertIn('123456',response.json['html'])
        long={'subject':'long mail','html':'<p>'+('正文'*18000)+'</p>'}
        self.assertEqual(self.admin.post('/api/admin/mail/preview',json={'template':long},headers=self.admin_headers).status_code,200)
        self.assertEqual(self.admin.post('/api/admin/mail/preview',json={'template':{'subject':'large','html':'a'*300000}},headers=self.admin_headers).status_code,413)

    def test_email_code_is_hashed_rate_limited_consumed_and_not_in_api_logs(self):
        self.smtp(registration_code=True)
        response=self.app.test_client().post('/api/auth/email-code',json={'email':'new@example.test'},headers={'Origin':'http://localhost:888'})
        self.assertEqual(response.status_code,200);self.assertNotIn('code',response.json)
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM email_codes').fetchone();job=db.execute('SELECT * FROM mail_jobs').fetchone()
        payload=json.loads(self.mail.cipher.decrypt(job['payload']))
        import re
        code=re.search(r'>[0-9]{6}</div>',payload['html'])[0][1:7]
        self.assertNotIn(code,row['hash']);self.assertEqual(self.app.test_client().post('/api/auth/email-code',json={'email':'new@example.test'}).status_code,400)
        user=self.register('new@example.test',email_code=code)
        with self.assertRaises(ValueError):self.mail.consume_code('new@example.test',code)
        self.assertNotIn(code,json.dumps(self.store.events()))

    def test_otp_wrong_attempts_are_bounded(self):
        self.smtp(registration_code=True);self.mail.issue_code('code@example.test','127.0.0.1')
        for _ in range(6):
            with self.assertRaises(ValueError):self.mail.consume_code('code@example.test','wrong!')
        with self.store.connect() as db:db.execute('UPDATE email_codes SET expires=0')
        with self.assertRaises(ValueError):self.mail.consume_code('code@example.test','123456')

    def test_turnstile_binds_hostname_action_and_has_no_local_bypass(self):
        self.bot.save({'version':0,'enabled':True,'site_key':'site-fixture','secret_key':'secret-fixture','hostnames':['localhost']})
        real=httpx.Client
        def client(data):return patch('web.antibot.httpx.Client',side_effect=lambda **kw:real(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=data)),**kw))
        with client({'success':True,'hostname':'localhost','action':'login'}):
            self.bot.verify('token','127.0.0.1','login')
            with self.assertRaises(ValueError):self.bot.verify('token','127.0.0.1','register')
        for data in ({'success':True,'hostname':'evil.test','action':'login'},{'success':False,'hostname':'localhost','action':'login'}):
            with client(data),self.assertRaises(ValueError):self.bot.verify('token','127.0.0.1','login')
        self.assertEqual(self.app.test_client().post('/api/auth/login',json={'email':'admin','password':'admin'}).status_code,403)
        self.assertNotIn('secret_key',self.bot.public())

    def test_registration_review_and_paid_fee_are_both_required_and_fee_is_server_set(self):
        self.policy(registration_review=True,registration_paid=True)
        client,user,headers=self.register('pending@example.test')
        self.assertEqual(self.create(client,headers).json['error'],'registration_review_required')
        review=self.admin.post(f'/api/admin/users/{user}/review',json={'status':'approved','note':'checked'},headers=self.admin_headers)
        self.assertEqual(review.status_code,200)
        self.assertEqual(self.create(client,headers).json['error'],'registration_payment_required')
        bill=client.post('/api/account/bill',json={'purpose':'registration','amount':'0.01'},headers=headers)
        self.assertEqual(bill.status_code,201,bill.json);order=bill.json['order'];self.assertEqual(order['amount'],'10.00')
        again=client.post('/api/account/bill',json={'purpose':'registration'},headers=headers)
        self.assertEqual(again.json['order']['out_trade_no'],order['out_trade_no'])
        self.paid(order);self.paid(order)
        self.assertTrue(self.ops.access(user)['ready']);self.assertEqual(self.ops.access(user)['fee_paid'],1000)
        self.assertEqual(self.create(client,headers).status_code,201)
        self.assertTrue(self.ops.access(self.user)['ready'],'Existing users are not retroactively paywalled')
        self.balanced()

    def test_product_keyword_applies_to_api_and_returns_configured_message(self):
        self.policy(blocked_keywords=['BAD'],blocked_message='商品审核未通过')
        result=self.create(subject='ｂａｄ 商品')
        self.assertEqual(result.status_code,403);self.assertEqual(result.json['message'],'商品审核未通过')

    def test_domains_require_per_merchant_approval(self):
        self.policy(domain_add=True,domain_enforce=True)
        self.assertEqual(self.create().json['error'],'domain_not_authorized')
        self.client.post('/api/domains',json={'host':'shop.example.test'},headers=self.headers)
        identity=self.admin.get('/api/domains').json['domains'][0]['id']
        self.assertEqual(self.create(notify_url='https://shop.example.test/notify').status_code,400)
        self.admin.post(f'/api/admin/domains/{identity}',json={'status':'approved'},headers=self.admin_headers)
        self.assertEqual(self.create(notify_url='https://shop.example.test/notify').status_code,201)
        other,_,headers=self.register('other@example.test')
        self.assertEqual(self.create(other,headers,notify_url='https://shop.example.test/notify').json['error'],'domain_not_authorized')

    def test_fee_snapshot_duplicate_notify_and_low_amount_rounding(self):
        row=self.create().json['order'];self.policy(fee_rate='50.00');self.paid(row);self.paid(row)
        self.assertEqual(self.balances()['pending'],'98.50')
        self.policy(fee_threshold='0.10',fee_minimum='0.20')
        tiny=self.create(amount='0.01').json['order'];self.paid(tiny)
        with self.store.connect() as db:
            fee=db.execute('SELECT gross,fee FROM financial_orders WHERE number=?',(tiny['out_trade_no'],)).fetchone();self.assertEqual(tuple(fee),(1,1))
        self.balanced()

    def test_partial_refunds_reverse_proportional_fee_once(self):
        row=self.create().json['order'];self.paid(row)
        self._refund(row,'RF_ONE_001','40.00')
        self.assertEqual(self.balances()['pending'],'59.10')
        self._refund(self.store.order(row['out_trade_no']),'RF_TWO_002','60.00')
        self.assertEqual(self.balances()['pending'],'0.00')
        self.assertEqual(self.balances()['refund_hold'],'0.00');self.balanced()

    def _refund(self,row,no,value):
        row=self.store.order(row['out_trade_no'])
        record={'refund_no':no,'amount':value,'out_trade_no':row['out_trade_no']}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.ops.reserve_refund(db,row,no,value);self.ops.finish_refund(db,row,record,'SUCCESS')
            old=self.store.order(row['out_trade_no'])['refunded_amount']
            db.execute('UPDATE orders SET refunded_amount=? WHERE out_trade_no=?',(str(Decimal(old)+Decimal(value)),row['out_trade_no']))
            self.ops.finish_refund(db,row,record,'SUCCESS')

    def test_merchant_bears_fee_requires_extra_balance_for_full_refund(self):
        self.policy(refund_fee_payer='merchant');row=self.create().json['order'];self.paid(row)
        with self.store.connect() as db,self.assertRaisesRegex(ValueError,'refund_balance_insufficient'):self.ops.reserve_refund(db,self.store.order(row['out_trade_no']),'REFUND_FEE','100.00')
        self.balanced()

    def test_merchant_refund_fee_cannot_consume_other_pending_order_entitlement(self):
        self.policy(refund_fee_payer='merchant',settlement_days=0)
        first=self.create().json['order'];second=self.create().json['order'];self.paid(first);self.paid(second)
        with self.store.connect() as db:
            self.ops.journal(db,'fixture-topup','fixture','topup',self.user,[('clearing',0,150),('available',self.user,-150)])
        self._refund(first,'RF_TWO_LOTS','100.00');self.ops.tick()
        self.assertEqual(self.balances()['pending'],'0.00');self.assertEqual(self.balances()['available'],'98.50');self.balanced()

    def test_refund_failure_releases_hold_and_unknown_keeps_it(self):
        row=self.create().json['order'];self.paid(row);record={'refund_no':'RF_UNKNOWN','amount':'20.00','out_trade_no':row['out_trade_no']}
        with self.store.connect() as db:
            self.ops.reserve_refund(db,self.store.order(row['out_trade_no']),record['refund_no'],record['amount']);self.ops.finish_refund(db,self.store.order(row['out_trade_no']),record,'UNKNOWN')
        self.assertEqual(self.balances()['refund_hold'],'19.70')
        with self.store.connect() as db:self.ops.finish_refund(db,self.store.order(row['out_trade_no']),record,'FAILED')
        self.assertEqual(self.balances()['pending'],'98.50');self.assertEqual(self.balances()['refund_hold'],'0.00')

    def test_invitation_cashback_caps_and_refund_reversal(self):
        self.policy(invite_enabled=True,invite_basis='amount',invite_rate='50.00',invite_cap_fee=True)
        inviter=self.ops.access(self.user)['invite_code'];client,user,headers=self.register('invited@example.test',invite_code=inviter)
        row=self.create(client,headers).json['order'];self.paid(row)
        self.assertEqual(self.balances(self.user)['available'],'1.50')
        self._refund(row,'REF_INVITE','100.00');self.assertEqual(self.balances(self.user)['available'],'0.00');self.balanced()

    def test_deposit_requires_verified_payment_and_freeze(self):
        self.policy(deposit_enabled=True,deposit_minimum='10.00',deposit_freeze_days=7)
        self.assertEqual(self.create().json['error'],'deposit_required')
        bill=self.client.post('/api/account/bill',json={'purpose':'deposit','amount':'10.00'},headers=self.headers).json['order']
        self.assertEqual(self.balances()['deposit'],'0.00');self.paid(bill);self.assertEqual(self.balances()['deposit'],'10.00')
        with self.assertRaisesRegex(ValueError,'deposit_still_frozen'):self.ops.release_deposit(self.user,bill['out_trade_no'])
        self.policy(deposit_freeze_days=0)
        with self.store.connect() as db:db.execute('UPDATE deposits SET release_at=0')
        self.ops.release_deposit(self.user,bill['out_trade_no']);self.assertEqual(self.balances()['available'],'10.00');self.balanced()

    def test_settlement_freezes_balance_cancellation_and_verified_reference(self):
        self.policy(settlement_days=0,settlement_mode='manual',settlement_rate='1.00',settlement_daily_limit=3)
        row=self.create().json['order'];self.paid(row);self.ops.tick()
        self.assertEqual(self.balances()['available'],'98.50')
        self.ops.beneficiary(self.user,{'channel':'alipay','account':'payer@example.test','name':'测试用户'})
        first=self.ops.settlement(self.user,'50.00');self.assertEqual(self.balances()['settlement_hold'],'50.00')
        self.ops.finish_settlement(first,'cancel','');self.assertEqual(self.balances()['available'],'98.50')
        second=self.ops.settlement(self.user,'50.00')
        with self.assertRaises(ValueError):self.ops.finish_settlement(second,'paid','')
        self.ops.finish_settlement(second,'paid','REAL_TRANSFER_FIXTURE_001')
        with self.assertRaises(ValueError):self.ops.finish_settlement(second,'paid','REAL_TRANSFER_FIXTURE_001')
        self.assertEqual(self.balances()['available'],'48.50');self.balanced()

    def test_ip_headers_only_trusted_from_explicit_proxy_and_risk_frequency(self):
        self.policy(ip_source='x_forwarded_for',trusted_proxies=['127.0.0.1/32'])
        with self.app.test_request_context('/',headers={'X-Forwarded-For':'203.0.113.99, 198.51.100.1'},environ_base={'REMOTE_ADDR':'8.8.8.8'}):
            from flask import request
            self.assertEqual(self.ops.rules.client_ip(request),'8.8.8.8')
        with self.app.test_request_context('/',headers={'X-Forwarded-For':'203.0.113.99, 198.51.100.1'},environ_base={'REMOTE_ADDR':'127.0.0.1'}):self.assertEqual(self.ops.rules.client_ip(request),'198.51.100.1')
        self.policy(risk_enabled=True,risk_merchant_count=1)
        self.assertEqual(self.create().status_code,201)
        self.assertEqual(self.create().json['error'],'risk_frequency_exceeded')

    def test_bulk_selection_scope_refunding_skip_and_selected_export(self):
        a=self.create().json['order'];b=self.create(product='wap').json['order'];other,_,headers=self.register('bulk-other@example.test');foreign=self.create(other,headers).json['order']
        with self.store.connect() as db:db.execute("UPDATE orders SET status='REFUNDING' WHERE out_trade_no=?",(b['out_trade_no'],))
        ids=[a['out_trade_no'],b['out_trade_no'],foreign['out_trade_no']]
        csv=self.client.post('/api/console/orders/export?product=page',json={'ids':ids},headers=self.headers)
        self.assertEqual(csv.status_code,200);self.assertIn(a['out_trade_no'],csv.text);self.assertNotIn(foreign['out_trade_no'],csv.text)
        self.assertIn(b['out_trade_no'],csv.text)  # an explicit selection is not narrowed by the filters active at export time
        response=self.client.post('/api/console/orders/batch',json={'ids':ids,'action':'delete'},headers=self.headers)
        self.assertEqual(response.json['changed'],1);self.assertEqual(response.json['skipped'],[b['out_trade_no']]);self.assertFalse(self.store.order(foreign['out_trade_no'])['deleted_at'])
        self.assertEqual(self.client.post('/api/console/orders/batch',json={'all_matching':True,'action':'delete'},headers=self.headers).json['error'],'invalid_selection')
        self.balanced()

    def test_order_ids_are_scoped_and_selections_survive_later_filters(self):
        a=self.create().json['order'];b=self.create(product='wap').json['order'];other,_,other_headers=self.register('ids-other@example.test');foreign=self.create(other,other_headers).json['order']
        listed=self.client.get('/api/console/orders/ids').json
        self.assertEqual(set(listed['ids']),{a['out_trade_no'],b['out_trade_no']});self.assertFalse(listed['limited'])
        self.assertEqual(self.client.get('/api/console/orders/ids?product=wap').json['ids'],[b['out_trade_no']])
        self.assertEqual(self.client.get('/api/console/orders/ids?search=no-such-order').json['ids'],[])
        self.assertEqual(self.client.get('/api/console/orders/ids?status=bogus').status_code,400)
        self.assertEqual(self.app.test_client().get('/api/console/orders/ids').status_code,401)
        ids=[a['out_trade_no'],b['out_trade_no'],foreign['out_trade_no']]
        csv=self.client.post('/api/console/orders/export?search=gone&status=PAID&product=app',json={'ids':ids},headers=self.headers)
        self.assertEqual(csv.status_code,200);self.assertIn(a['out_trade_no'],csv.text);self.assertIn(b['out_trade_no'],csv.text);self.assertNotIn(foreign['out_trade_no'],csv.text)
        self.assertEqual(self.client.post('/api/console/orders/batch?search=gone',json={'ids':ids,'action':'delete'},headers=self.headers).json['changed'],2)
        self.assertEqual(self.client.get('/api/console/orders/ids').json['ids'],[])
        self.assertEqual(set(self.client.get('/api/console/orders/ids?deleted=1').json['ids']),{a['out_trade_no'],b['out_trade_no']})
        self.assertEqual(self.client.post('/api/console/orders/batch?deleted=1&status=PAID',json={'ids':ids,'action':'restore'},headers=self.headers).json['changed'],2)
        self.assertFalse(self.store.order(foreign['out_trade_no'])['deleted_at'])

    def test_order_ids_listing_stops_at_the_batch_limit(self):
        cfg=self.app.extensions['alipay_config']
        for i in range(1001):self.store.create_order(self.user,{'out_trade_no':f'LIMIT_{i:05}','subject':'x','amount':'1.00','product':'page'},'https://example.test',cfg)
        listed=self.client.get('/api/console/orders/ids').json
        self.assertEqual(len(listed['ids']),1000);self.assertTrue(listed['limited'])

    def test_otp_five_wrong_numeric_codes_lock_the_original_code(self):
        self.smtp(registration_code=True);self.mail.issue_code('lock@example.test','127.0.0.1')
        with self.store.connect() as db:
            job=db.execute('SELECT payload FROM mail_jobs').fetchone()
        import re
        correct=re.search(r'>[0-9]{6}</div>',json.loads(self.mail.cipher.decrypt(job['payload']))['html'])[0][1:7]
        wrong='000000' if correct!='000000' else '111111'
        for _ in range(5):
            with self.assertRaises(ValueError):self.mail.consume_code('lock@example.test',wrong)
        with self.assertRaises(ValueError):self.mail.consume_code('lock@example.test',correct)

    def test_mail_queue_retries_without_logging_smtp_secret_or_code(self):
        self.smtp();self.mail.enqueue('reminder','recipient@example.test',{'message':'notice'},'notification-1')
        with patch.object(self.mail,'send',side_effect=OSError('private smtp details')):self.assertTrue(self.mail.tick())
        with self.store.connect() as db:
            row=db.execute('SELECT status,attempts,error FROM mail_jobs').fetchone();self.assertEqual(row['attempts'],1);self.assertEqual(row['status'],'pending');self.assertEqual(row['error'],'OSError')
            db.execute('UPDATE mail_jobs SET next_at=0')
        with patch.object(self.mail,'send'):self.assertTrue(self.mail.tick())
        with self.store.connect() as db:row=db.execute('SELECT status,payload FROM mail_jobs').fetchone();self.assertEqual(row['status'],'sent');self.assertIsNone(row['payload'])
        self.assertNotIn('private smtp details',json.dumps(self.store.events()))

    def test_risk_uses_mature_orders_and_task_cannot_run_twice(self):
        self.policy(risk_enabled=True,risk_failure_count=1)
        row=self.create().json['order']
        with self.store.connect() as db:db.execute('UPDATE orders SET expires_at=? WHERE out_trade_no=?',(int(time.time())-5,row['out_trade_no']))
        self.assertTrue(self.ops.tick());self.assertFalse(self.ops.tick());self.assertTrue(self.ops.access(self.user)['risk_hold'])
        self.assertEqual(self.create().json['error'],'merchant_risk_hold')
        self.assertEqual(self.admin.post(f'/api/admin/users/{self.user}/risk-release',json={},headers=self.admin_headers).status_code,200)
        self.assertFalse(self.ops.access(self.user)['risk_hold'])

    def test_daily_and_settlement_commission_release_is_idempotent(self):
        self.policy(invite_enabled=True,invite_timing='settlement',settlement_days=0,invite_rate='10.00')
        client,user,headers=self.register('commission@example.test',invite_code=self.ops.access(self.user)['invite_code'])
        row=self.create(client,headers).json['order'];self.paid(row)
        self.assertEqual(self.balances()['commission_hold'],'0.15');self.ops.tick()
        self.assertEqual(self.balances()['available'],'0.15');self.assertEqual(self.balances()['commission_hold'],'0.00')
        with self.store.connect() as db:db.execute('UPDATE task_runs SET finished_at=0')
        self.ops.tick();self.assertEqual(self.balances()['available'],'0.15');self.balanced()

    def test_platform_bills_cannot_be_refunded_through_merchant_order_endpoint(self):
        self.policy(deposit_enabled=True,deposit_minimum='10.00',merchant_refund=True)
        bill=self.client.post('/api/account/bill',json={'purpose':'deposit','amount':'10.00'},headers=self.headers).json['order'];self.paid(bill)
        response=self.client.post(f"/api/v1/payments/{bill['out_trade_no']}/refund",json={'refund_no':'RF_DEPOSIT_BYPASS','amount':'10.00','reason':'test'},headers=self.headers)
        self.assertEqual(response.status_code,409);self.assertEqual(response.json['error'],'platform_bill_refund_requires_review');self.assertEqual(self.balances()['deposit'],'10.00')

    def test_merchant_refund_switch_is_enforced_server_side(self):
        row=self.create().json['order'];self.paid(row)
        payload={'refund_no':'RF_SELF_SERVICE','amount':'1.00','reason':'test'}
        route=f"/api/v1/payments/{row['out_trade_no']}/refund"
        self.assertEqual(self.client.post(route,json=payload,headers=self.headers).status_code,403)
        self.policy(merchant_refund=True)
        order=self.store.order(row['out_trade_no'])
        with patch('bypay.alipay_client.AlipayClient.refund',return_value={'is_success':True,'out_trade_no':row['out_trade_no'],'trade_no':order['trade_no'],'refund_fee':'1.00'}):
            response=self.client.post(route,json=payload,headers=self.headers)
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(response.json['refund']['status'],'SUCCESS');self.balanced()

    def test_concurrent_settlements_cannot_reserve_the_same_balance(self):
        from concurrent.futures import ThreadPoolExecutor
        self.policy(settlement_days=0,settlement_mode='manual',settlement_daily_limit=5)
        row=self.create().json['order'];self.paid(row);self.ops.tick()
        self.ops.beneficiary(self.user,{'channel':'alipay','account':'concurrency@example.test','name':'测试'})
        def reserve():
            try:return self.ops.settlement(self.user,'60.00')
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(lambda _:reserve(),range(2)))
        self.assertEqual(sum(result is not None for result in results),1)
        self.assertEqual(self.balances()['settlement_hold'],'60.00');self.assertEqual(self.balances()['available'],'38.50');self.balanced()

    def test_registration_overpayment_is_credited_to_available_not_double_charged(self):
        self.policy(registration_paid=True)
        client,user,headers=self.register('latepaid@example.test')
        bill=client.post('/api/account/bill',json={'purpose':'registration'},headers=headers).json['order'];self.paid(bill)
        with self.store.connect() as db:
            db.execute("INSERT INTO platform_bills(number,user_id,purpose,amount) VALUES ('BILL_LATE_FIXTURE',?,'registration',1000)",(user,))
            self.ops.paid(db,{'out_trade_no':'BILL_LATE_FIXTURE','user_id':user,'amount':'10.00'})
        self.assertEqual(self.ops.access(user)['fee_paid'],1000);self.assertEqual(self.balances(user)['available'],'10.00');self.balanced()

    def test_ledger_cannot_be_changed_or_deleted(self):
        row=self.create().json['order'];self.paid(row)
        import sqlite3
        for query in ('DELETE FROM ledger_lines','UPDATE ledger_lines SET cents=0'):
            with self.store.connect() as db,self.assertRaises(sqlite3.IntegrityError):db.execute(query)
        self.balanced()


if __name__=='__main__':unittest.main()
