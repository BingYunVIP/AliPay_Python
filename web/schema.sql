-- Current complete schema. Fresh installations only.
CREATE TABLE IF NOT EXISTS api_keys (
                    hash TEXT PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
                    suffix TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS channel_configs (archived_at INTEGER,
                    id INTEGER PRIMARY KEY, channel TEXT NOT NULL, name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0, is_default INTEGER NOT NULL DEFAULT 0,
                    version INTEGER REFERENCES channel_versions(id), revision INTEGER NOT NULL DEFAULT 1, checked_version INTEGER NOT NULL DEFAULT -1);
CREATE TABLE IF NOT EXISTS channel_versions (id INTEGER PRIMARY KEY, channel TEXT NOT NULL, payload BLOB NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS checkout_jobs (
                    out_trade_no TEXT PRIMARY KEY REFERENCES orders(out_trade_no),
                    locked_until INTEGER NOT NULL DEFAULT 0, lease TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS console_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crypto_block_cursors(network TEXT PRIMARY KEY,height INTEGER NOT NULL,updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS crypto_network_configs(network TEXT PRIMARY KEY,payload BLOB NOT NULL,revision INTEGER NOT NULL DEFAULT 1,updated_at INTEGER NOT NULL, check_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS crypto_observations(txid TEXT NOT NULL,log_index TEXT NOT NULL,out_trade_no TEXT NOT NULL DEFAULT '',network TEXT NOT NULL,asset TEXT NOT NULL,address TEXT NOT NULL,amount TEXT NOT NULL,block_height INTEGER NOT NULL,block_hash TEXT NOT NULL,seen_at INTEGER NOT NULL,PRIMARY KEY(network,txid,log_index));
CREATE TABLE IF NOT EXISTS deposits(number TEXT PRIMARY KEY,user_id INTEGER NOT NULL,cents INTEGER NOT NULL,release_at INTEGER NOT NULL,status TEXT DEFAULT 'frozen',source TEXT NOT NULL DEFAULT 'real',remaining INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS email_codes(email TEXT PRIMARY KEY,hash TEXT,expires INTEGER,attempts INTEGER DEFAULT 0,consumed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, out_trade_no TEXT, accepted INTEGER NOT NULL,
                    reason TEXT NOT NULL, created_at INTEGER NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'notification', details TEXT NOT NULL DEFAULT '{}', user_id INTEGER);
CREATE TABLE IF NOT EXISTS financial_orders(number TEXT PRIMARY KEY,gross INTEGER NOT NULL,fee INTEGER NOT NULL,commission INTEGER NOT NULL,inviter INTEGER,policy TEXT NOT NULL,paid INTEGER DEFAULT 0,phase TEXT DEFAULT 'pending',available_at INTEGER,fee_reversed INTEGER DEFAULT 0,commission_reversed INTEGER DEFAULT 0,commission_released INTEGER DEFAULT 0,service_fee INTEGER NOT NULL DEFAULT 0,channel_cost INTEGER NOT NULL DEFAULT 0,service_fee_reversed INTEGER NOT NULL DEFAULT 0,channel_cost_reversed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS ledger_events(id TEXT PRIMARY KEY,kind TEXT NOT NULL,reference TEXT NOT NULL,user_id INTEGER NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ledger_lines(id INTEGER PRIMARY KEY,event_id TEXT NOT NULL REFERENCES ledger_events(id),account TEXT NOT NULL,user_id INTEGER NOT NULL,cents INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS limits (key TEXT PRIMARY KEY, count INTEGER, expires INTEGER);
CREATE TABLE IF NOT EXISTS mail_jobs(id INTEGER PRIMARY KEY,dedupe TEXT UNIQUE,payload BLOB,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,next_at INTEGER,locked_until INTEGER DEFAULT 0,error TEXT DEFAULT '',created_at INTEGER);
CREATE TABLE IF NOT EXISTS mail_templates(event TEXT,language TEXT,subject TEXT,html TEXT,PRIMARY KEY(event,language));
CREATE TABLE IF NOT EXISTS merchant_access(user_id INTEGER PRIMARY KEY REFERENCES users(id),review TEXT DEFAULT 'approved',review_note TEXT DEFAULT '',fee_due INTEGER DEFAULT 0,fee_paid INTEGER DEFAULT 0,inviter INTEGER,invite_code TEXT UNIQUE,risk_hold INTEGER DEFAULT 0,risk_reason TEXT DEFAULT '',review_required INTEGER NOT NULL DEFAULT 0, pay_enabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS merchant_accounts (
                  pid INTEGER PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
                  md5_key BLOB NOT NULL, public_key TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'both', created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS merchant_cancel_requests (
                  platform_no TEXT PRIMARY KEY REFERENCES orders(out_trade_no), user_id INTEGER NOT NULL REFERENCES users(id),
                  state TEXT NOT NULL CHECK(state IN ('pending','confirmed','failed','superseded')),
                  created_at INTEGER NOT NULL, provider_state TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS merchant_domains(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,host TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at INTEGER NOT NULL,UNIQUE(user_id,host));
CREATE TABLE IF NOT EXISTS merchant_groups (
                    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
                    service_rate TEXT NOT NULL, cost_rate TEXT NOT NULL,
                    billing_mode TEXT NOT NULL DEFAULT 'merchant_fee', version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS merchant_key_info (name TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS merchant_orders (
                  platform_no TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), pid INTEGER NOT NULL REFERENCES merchant_accounts(pid),
                  merchant_no TEXT NOT NULL, version TEXT NOT NULL, request_hash TEXT NOT NULL,
                  md5_key BLOB NOT NULL, return_url TEXT NOT NULL, param TEXT NOT NULL DEFAULT '',
                  token_hash TEXT UNIQUE NOT NULL, token BLOB NOT NULL, created_at INTEGER NOT NULL, amount TEXT NOT NULL DEFAULT '0.00', reservation_state TEXT NOT NULL DEFAULT 'active',
                  UNIQUE(user_id,merchant_no));
CREATE TABLE IF NOT EXISTS merchant_payment_cursors (user_id INTEGER NOT NULL, channel TEXT NOT NULL, config_id INTEGER NOT NULL, PRIMARY KEY(user_id,channel));
CREATE TABLE IF NOT EXISTS merchant_payment_settings (user_id INTEGER PRIMARY KEY REFERENCES users(id), version INTEGER NOT NULL, payload TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS merchant_quota_reservations (number TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS orders (merchant_server_ip TEXT NOT NULL DEFAULT '', fixed_rate_snapshot TEXT NOT NULL DEFAULT '', market_rate_snapshot TEXT NOT NULL DEFAULT '', rate_snapshot_at INTEGER NOT NULL DEFAULT 0, match_mode TEXT NOT NULL DEFAULT 'exact',
                    out_trade_no TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    subject TEXT NOT NULL, amount TEXT NOT NULL, product TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'alipay', status TEXT NOT NULL DEFAULT 'CREATED',
                    pay_url TEXT NOT NULL, trade_no TEXT UNIQUE,
                    app_id TEXT NOT NULL, seller_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL, paid_at INTEGER, notify_url TEXT NOT NULL DEFAULT '',
                    client_ip TEXT NOT NULL DEFAULT '', channel_version INTEGER, expires_at INTEGER, payment_payload TEXT NOT NULL DEFAULT '{}', request_hash TEXT, provider_notify_url TEXT, provider_return_url TEXT, payment_config_id INTEGER, deleted_at INTEGER, refunded_amount TEXT NOT NULL DEFAULT '0.00', provider_status TEXT NOT NULL DEFAULT '', cashier_qr TEXT NOT NULL DEFAULT '', active_refund_no TEXT, crypto_network TEXT NOT NULL DEFAULT '', crypto_asset TEXT NOT NULL DEFAULT '', crypto_amount TEXT NOT NULL DEFAULT '', crypto_address TEXT NOT NULL DEFAULT '', crypto_rate TEXT NOT NULL DEFAULT '', crypto_rate_updated_at INTEGER NOT NULL DEFAULT 0, crypto_rate_source TEXT NOT NULL DEFAULT '', crypto_quoted_at INTEGER NOT NULL DEFAULT 0, crypto_txid TEXT NOT NULL DEFAULT '', crypto_block INTEGER NOT NULL DEFAULT 0, from_address TEXT NOT NULL DEFAULT '', crypto_seen_at INTEGER NOT NULL DEFAULT 0, crypto_confirmations INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, out_trade_no TEXT UNIQUE NOT NULL REFERENCES orders(out_trade_no),
                    user_id INTEGER NOT NULL, url TEXT NOT NULL, payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL,
                    locked_until INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS payment_attempts(number TEXT PRIMARY KEY,user_id INTEGER NOT NULL,ip TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS payment_checks (
                    out_trade_no TEXT PRIMARY KEY REFERENCES orders(out_trade_no),
                    next_check INTEGER NOT NULL, locked_until INTEGER NOT NULL DEFAULT 0,
                    lease TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                    checked_at INTEGER, state TEXT NOT NULL DEFAULT 'scheduled', error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS payment_returns (
                    hash TEXT PRIMARY KEY, out_trade_no TEXT NOT NULL REFERENCES orders(out_trade_no),
                    expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS platform_bills(number TEXT PRIMARY KEY,user_id INTEGER NOT NULL,purpose TEXT NOT NULL,amount INTEGER NOT NULL,status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS refund_deposit_lots(refund_no TEXT NOT NULL,deposit_no TEXT NOT NULL,cents INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'held',PRIMARY KEY(refund_no,deposit_no));
CREATE TABLE IF NOT EXISTS refund_holds(refund_no TEXT PRIMARY KEY,user_id INTEGER NOT NULL,source TEXT NOT NULL,cents INTEGER NOT NULL,fee_return INTEGER NOT NULL,commission_return INTEGER NOT NULL,status TEXT DEFAULT 'held',service_fee_return INTEGER NOT NULL DEFAULT 0,channel_cost_return INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS refunds (
                    refund_no TEXT PRIMARY KEY, out_trade_no TEXT NOT NULL REFERENCES orders(out_trade_no),
                    amount TEXT NOT NULL, reason TEXT NOT NULL, actor INTEGER NOT NULL,
                    status TEXT NOT NULL, created_at INTEGER NOT NULL, confirmed_at INTEGER,
                    locked_until INTEGER NOT NULL DEFAULT 0, next_query INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', manual_reference TEXT NOT NULL DEFAULT '', crypto_txid TEXT NOT NULL DEFAULT '', crypto_network TEXT NOT NULL DEFAULT '', crypto_asset TEXT NOT NULL DEFAULT '', crypto_amount TEXT NOT NULL DEFAULT '', crypto_refund_to TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS service_health (name TEXT PRIMARY KEY, last_seen INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    csrf TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settlement_accounts(user_id INTEGER PRIMARY KEY,payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS settlements(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,gross INTEGER NOT NULL,fee INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at INTEGER NOT NULL,paid_at INTEGER,reference TEXT UNIQUE,beneficiary BLOB);
CREATE TABLE IF NOT EXISTS short_links (
                    token TEXT PRIMARY KEY, out_trade_no TEXT UNIQUE NOT NULL REFERENCES orders(out_trade_no),
                    created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS task_runs(name TEXT PRIMARY KEY,locked_until INTEGER DEFAULT 0,started_at INTEGER,finished_at INTEGER,status TEXT DEFAULT 'idle',error TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS users (must_change_credentials INTEGER NOT NULL DEFAULT 0,
                    id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user'
                    CHECK(role IN ('admin','user')), created_at INTEGER NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, group_id INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS crypto_observations_block ON crypto_observations(network,block_height);
CREATE UNIQUE INDEX IF NOT EXISTS crypto_refund_txid_unique ON refunds(crypto_txid) WHERE crypto_txid!='';
CREATE UNIQUE INDEX IF NOT EXISTS default_channel_config ON channel_configs(channel) WHERE is_default=1;
CREATE INDEX IF NOT EXISTS due_payment_checks ON payment_checks(next_check);
CREATE INDEX IF NOT EXISTS merchant_cancel_window ON merchant_cancel_requests(user_id,state,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS one_admin ON users(role) WHERE role='admin';
CREATE INDEX IF NOT EXISTS order_refunds ON refunds(out_trade_no,created_at DESC);
CREATE INDEX IF NOT EXISTS user_orders ON orders(user_id, created_at DESC);
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_lines BEGIN SELECT RAISE(ABORT,'immutable_ledger');END;
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_lines BEGIN SELECT RAISE(ABORT,'immutable_ledger');END;
CREATE TABLE IF NOT EXISTS rate_snapshots(id INTEGER PRIMARY KEY,asset TEXT NOT NULL,rate TEXT NOT NULL,quoted_at INTEGER NOT NULL,observed_at INTEGER NOT NULL,source TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS rate_asset_time ON rate_snapshots(asset,id DESC);
CREATE TABLE IF NOT EXISTS rate_sync_runs(id INTEGER PRIMARY KEY,created_at INTEGER NOT NULL,success INTEGER NOT NULL,message TEXT NOT NULL);
