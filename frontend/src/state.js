import { computed, reactive, ref, watch } from 'vue'

export const locale = ref(document.documentElement.lang === 'en' ? 'en' : 'zh')
export const dark = ref(document.documentElement.classList.contains('dark'))
export const auth = reactive({ user: null, csrf: '', key: null })
export const t = (zh, en) => locale.value === 'zh' ? zh : en
export const isAdmin = computed(() => auth.user?.role === 'admin')

watch(locale, value => {
  document.documentElement.lang = value === 'zh' ? 'zh-CN' : 'en'
  document.title = value === 'zh' ? '冰云支付 - 聚合支付' : 'BingYun Pay - Payment Aggregation'
  try { localStorage.setItem('bypay-locale', value) } catch { /* Storage is optional. */ }
}, { immediate: true })
watch(dark, value => {
  document.documentElement.classList.toggle('dark', value)
  document.querySelector('meta[name="theme-color"]').content = value ? '#0e1116' : '#f4f5f7'
  try { localStorage.setItem('bypay-theme', value ? 'dark' : 'light') } catch { /* Storage is optional. */ }
})

export class ApiError extends Error {
  constructor(code, status) { super(code); this.status = status }
}

export async function api(path, options = {}) {
  let response
  try {
    response = await fetch(path, { ...options, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': auth.csrf, ...options.headers },
      signal: options.signal ?? AbortSignal.timeout(20000),
    })
  } catch { throw new ApiError('network_error', 0) }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401) { auth.user = null; auth.csrf = ''; auth.key = null }
    const error = new ApiError(data.error || 'request_failed', response.status)
    error.field = data.field
    error.reason = data.reason
    error.serverMessage=data.message
    throw error
  }
  return data
}

let initPromise
export function initializeAuth() {
  return initPromise ??= api('/api/auth/me').then(data => {
    auth.user = data.user; auth.csrf = data.csrf_token; auth.key = data.api_key
  }).catch(() => {})
}

export const errors = {
  registration_review_required:['商户尚未通过审核，请在账户中心查看进度','Merchant review is required'],
  registration_payment_required:['请先完成注册付费','Registration payment is required'],
  invalid_email_code:['验证码无效、已使用或已过期','Invalid or expired email code'],
  human_verification_required:['请先完成人机验证','Complete the human verification'],
  human_verification_failed:['人机验证失败，请重新验证','Human verification failed'],
  smtp_test_failed:['测试邮件发送失败，请检查 SMTP 地址、授权码和服务器日志','SMTP test failed'],
  smtp_disabled:['请先启用并保存邮箱配置','Enable SMTP first'],
  smtp_credentials_required:['请填写发件地址、SMTP 账号与授权码','Complete SMTP credentials'],
  invalid_turnstile:['Site Key 或 Secret Key 的格式不正确','The Site Key or Secret Key is not valid'],
  invalid_turnstile_hostnames:['域名格式不正确，每行一个，不含协议、端口和路径','Domains must be one per line, without scheme, port or path'],
  invalid_policy:['规则内容不正确，请检查后重试','Check the rule values and try again'],
  invalid_money:['金额须为最多两位小数的数字','Enter an amount with at most two decimals'],
  settlement_not_pending:['这笔结算已经处理过，请刷新后查看','This settlement was already handled — refresh to see it'],
  settlement_evidence_required:['登记出款需要填写 6 到 128 个字符的转账流水号','Enter a transfer reference of 6 to 128 characters to record a payout'],
  settlement_disabled:['结算申请方式已关闭','Settlement requests are turned off'],
  turnstile_configuration_required:['请填写 Turnstile Site Key、Secret Key 和验证域名','Complete Turnstile settings'],
  merchant_risk_hold:['商户收款因风控规则暂停，请联系管理员','Merchant risk hold'],
  merchant_pay_disabled:['商户支付权限已关闭，请联系管理员','Payment permission is turned off for this merchant'],
  invalid_domain:['网站域名格式不正确，请填写例如 shop.example.com','Enter a site domain such as shop.example.com'],
  domain_limit_exceeded:['网站域名数量超过后台允许的数量','Too many site domains for the current limit'],
  deposit_required:['请先在账户中心缴纳足额保证金','Merchant deposit is required'],
  domain_not_authorized:['支付域名尚未通过审核','Payment domain is not approved'],
  refund_balance_insufficient:['可用或待结算余额不足以预留退款金额','Insufficient funds to reserve this refund'],
  merchant_refund_disabled:['平台未开启商户自助退款','Merchant refunds are disabled'],
  batch_limit_exceeded:['单次最多处理 1000 笔，请缩小筛选范围','Limit selection to 1000 orders'],
  invalid_selection:['请选择要操作的订单','Select orders'],
  settlement_balance_insufficient:['金额不足最低结算金额或超过可用余额','Insufficient settlement balance'],
  beneficiary_required:['请先保存结算账户','Save a beneficiary first'],
  deposit_still_frozen:['保证金仍在冻结期或存在待处理交易','Deposit is still frozen'],
  platform_bill_refund_requires_review:['注册费和保证金不通过普通商户退款入口退回，请联系管理员','Platform bills require separate review'],
  settlement_balance_changed:['账户余额或风控状态已变化，请重新核对后处理结算','The balance or risk state has changed'],
  product_blocked:['该商品暂不支持支付','This product is not supported'],
  network_error: ['连接服务失败，请检查网络后重试。', 'Unable to reach the service. Please retry.'],
  invalid_credentials: ['邮箱或密码不正确。', 'Incorrect email or password.'],
  invalid_email: ['请输入有效的邮箱地址。', 'Enter a valid email address.'],
  password_too_short: ['密码至少需要 12 个字符。', 'Use at least 12 characters for your password.'],
  email_unavailable: ['该邮箱无法注册，请尝试登录。', 'This email is unavailable. Try signing in.'],
  registration_closed: ['暂未开放注册，请联系冰云支持。', 'Registration is closed. Contact support.'],
  rate_limited: ['操作较频繁，请稍后再试。', 'Too many attempts. Please try again later.'],
  payment_not_configured: ['这个收款渠道的参数还没有配置完整，请管理员检查', 'This payment channel is not fully configured — ask the administrator to check it'],
  payment_provider_error: ['支付渠道返回异常，请查看系统日志并核对配置', 'The payment channel returned an error — review the event log and the configuration'],
  payment_mismatch: ['交易信息核对失败，订单未更新，请检查日志。', 'Payment details do not match. The order was not updated.'],
  payment_result_expired: ['支付结果链接已失效，请登录查看订单', 'This receipt link expired — sign in to view your order'],
  payment_response_signature_invalid: ['支付宝返回结果验签失败，请管理员检查支付宝公钥', 'Alipay response signature failed — check the Alipay public key'],
  payment_request_signature_invalid: ['支付宝请求签名不匹配，请管理员核对 APPID 和应用密钥', 'Alipay request signature failed — check the App ID and app keys'],
  payment_app_id_invalid: ['支付宝 APPID 不正确，请管理员核对渠道配置', 'The Alipay App ID is invalid — check the channel configuration'],
  invalid_amount: ['金额须为大于 0、最多两位小数的数字。', 'Enter a positive amount with at most two decimal places.'],
  test_amount_exceeded: ['金额超过当前测试上限。', 'Amount exceeds the current test limit.'],
  order_conflict: ['该订单号已被使用，请创建新订单。', 'This order number is already in use. Create a new order.'],
  unauthorized: ['登录已过期，请重新登录。', 'Your session expired. Please sign in again.'],
  invalid_csrf: ['登录状态已更新，请刷新页面重试。', 'Please reload the page and try again.'],
  invalid_origin: ['当前页面地址与启动地址不一致，请使用启动脚本显示的访问地址', 'Open the site URL shown by the startup script'],
  unsupported_product: ['此渠道或产品暂未开放。', 'This payment product is not available yet.'],
  channel_disabled: ['支付宝新订单已暂停接收，请联系管理员。', 'Alipay is paused for new orders. Contact your administrator.'],
  user_disabled: ['此账户已停用，请联系管理员。', 'This account is disabled. Contact your administrator.'],
  callback_not_retryable: ['此回调已送达或正在投递，请刷新后查看。', 'This callback was delivered or is in progress. Refresh to check its status.'],
  export_limit_exceeded: ['最多导出 10,000 笔订单，请缩小筛选范围。', 'Limit your filters to 10,000 orders before exporting.'],
  cashier_qr_unavailable: ['未能获取支付宝收银台二维码，请重试或打开收银台', 'Unable to load the Alipay QR — retry or open checkout'],
  order_not_refundable: ['仅已支付且有可退金额的订单支持退款', 'Only paid orders with a refundable balance can be refunded'],
  refund_exceeds_remaining: ['退款金额超过该订单剩余可退金额', 'Refund exceeds the remaining refundable amount'],
  refund_conflict: ['退款请求号已被使用，请核对原退款请求', 'This refund ID belongs to another request'],
  refund_provider_error: ['支付渠道未确认退款成功，请查看退款日志', 'The payment channel did not confirm this refund — check the log'],
  refund_in_progress: ['退款正在处理中，请待结果确认后操作', 'Wait for the pending refund to be confirmed'],
  invalid_refund_reason: ['请填写退款原因，最多 128 个字符', 'Enter a refund reason of up to 128 characters'],
  invalid_settings: ['配置内容不正确，请检查后重试。', 'Check the configuration values and try again.'],
  settings_conflict: ['设置已在其他页面更新，请重新载入后再保存', 'Settings changed in another page — reload before saving'],
  buyer_identity_required: ['请填写付款用户的 OpenID 或支付宝 UID', 'Enter the payer OpenID or Alipay UID'],
  invalid_buyer_identity: ['付款用户标识格式不正确', 'The payer identifier format is invalid'],
  payment_product_unavailable: ['当前支付宝应用尚未开通此产品，请检查开放平台的产品签约', 'This Alipay app has not enabled this product — check its subscriptions'],
  payment_creation_pending: ['订单正在创建，请稍后使用同一订单重试', 'Order creation is in progress — retry the same order shortly'],
  payment_order_expired: ['订单已结束或超过有效期，请查询状态后重新下单', 'This order ended or expired — check its status before creating a new one'],
  qr_not_available: ['此订单暂不能生成二维码，请使用对应的支付入口', 'A QR code is unavailable — use the payment entry for this order'],
  admin_protected: ['唯一管理员账户不能停用。', 'The administrator account cannot be disabled.'],
  invalid_channel_field: ['格式不正确，请检查后重新保存', 'Invalid format; check the value and save again'],
  invalid_channel_key: ['密钥格式不正确，需要 2048 位或更高的 RSA 密钥', 'Use a valid RSA key of at least 2048 bits'],
  invalid_channel_config: ['渠道参数不正确，请刷新配置后重试', 'Reload the channel configuration and try again'],
  channel_config_conflict: ['配置已被更新，请关闭后重新打开编辑窗口', 'Configuration changed; close and reopen the editor'],
  channel_config_locked: ['无法读取加密配置，请检查服务器渠道主密钥文件', 'Unable to decrypt channel configuration; check the server master key'],
  channel_key_empty:['请粘贴密钥内容','Paste the key'],
  channel_key_certificate:['这是证书，不是密钥。商户私钥请粘贴 apiclient_key.pem 的内容；公钥请下载公钥文件，不要用证书','This is a certificate, not a key. Paste apiclient_key.pem as the private key; download the public key file instead of using a certificate'],
  channel_key_public_key:['这是公钥，这里需要私钥（apiclient_key.pem，以 -----BEGIN PRIVATE KEY----- 开头）','This is a public key; this field needs the private key (apiclient_key.pem, starting with -----BEGIN PRIVATE KEY-----)'],
  channel_key_private_key:['这是私钥，这里需要公钥文件（-----BEGIN PUBLIC KEY----- 开头），请不要把私钥填到公钥位置','This is a private key; this field needs the public key file (starting with -----BEGIN PUBLIC KEY-----)'],
  channel_key_encrypted:['私钥带有密码保护，暂不支持，请使用未加密的密钥文件','This private key is password protected, which is not supported — use the unencrypted file'],
  channel_key_incomplete:['密钥内容不完整，请从 -----BEGIN 一行完整复制到 -----END 一行','The key is incomplete — copy everything from the -----BEGIN line to the -----END line'],
  channel_key_unreadable:['无法识别这个密钥，请确认复制的是 .pem 文件里的完整内容','This key cannot be read — copy the complete contents of the .pem file'],
  channel_key_not_rsa:['这不是 RSA 密钥，需要 RSA 2048 位以上','This is not an RSA key — an RSA key of 2048 bits or more is required'],
  channel_key_too_short:['密钥长度不足，需要 2048 位或更高的 RSA 密钥','The key is too short — use an RSA key of 2048 bits or more'],
  wechat_public_key_is_merchant_key:['这是你自己的商户公钥，不是微信支付公钥。请到商户平台「账户中心 → API安全 → 管理公钥」下载微信支付公钥','This is your own merchant public key, not the WeChat Pay public key — download it from the merchant platform under API security → manage public key'],
  wechat_https_required:['微信支付要求公网 HTTPS 站点地址，请先在「系统管理 → 域名与通知」填写 https:// 开头的地址','WeChat Pay needs a public HTTPS site address — set one under System → Domains & notifications first'],
  wechat_order_number_too_long:['微信支付的商户订单号最长 32 个字符','WeChat Pay order numbers can have at most 32 characters'],
  wechat_order_number_used:['微信支付提示订单号已使用，请换一个订单号重新下单','WeChat Pay says this order number is already used — create the order again with a new number'],
  wechat_order_closed:['这笔订单已在微信支付关闭，请重新下单','This order is closed at WeChat Pay — create a new one'],
  wechat_sign_error:['微信支付拒绝了签名：请核对商户号、商户证书序列号和商户私钥是否来自同一个证书','WeChat Pay rejected the signature — check that the merchant number, certificate serial number and private key belong to the same certificate'],
  wechat_app_id_mismatch:['AppID 与商户号没有绑定，请在商户平台「产品中心 → APPID授权管理」关联并确认','The AppID is not bound to this merchant number — link and confirm it under product center → AppID authorization'],
  wechat_mch_not_exists:['微信支付商户号不存在，请核对','The WeChat Pay merchant number does not exist — check it'],
  wechat_no_auth:['商户号还没有开通这个微信支付产品，请在商户平台「产品中心」开通','This product is not enabled for the merchant number — enable it in the product center'],
  wechat_invalid_request:['微信支付拒绝了请求，常见原因是微信支付公钥 ID 填错，请对照「API安全 → 管理公钥」','WeChat Pay refused the request — often a wrong public key ID; compare it with API security → manage public key'],
  wechat_param_error:['微信支付认为订单参数有误，请查看系统日志中的说明','WeChat Pay found a problem with the order parameters — see the explanation in the event log'],
  wechat_rate_limited:['微信支付接口调用过于频繁，请稍后重试','WeChat Pay is rate limiting requests — retry shortly'],
  wechat_rule_limit:['触发了微信支付的业务规则限制，请查看系统日志','A WeChat Pay business rule was triggered — see the event log'],
  wechat_system_error:['微信支付暂时不可用，请稍后重试','WeChat Pay is temporarily unavailable — retry shortly'],
  wechat_network_error:['无法连接微信支付，请检查服务器网络后重试','Cannot reach WeChat Pay — check the server network and retry'],
  wechat_response_signature_invalid:['微信支付返回内容验签失败，请核对微信支付公钥是否正确','The WeChat Pay answer failed signature verification — check the WeChat Pay public key'],
  wechat_public_key_mismatch:['微信支付返回的公钥 ID 与配置不一致，请核对微信支付公钥 ID','The public key ID in the WeChat Pay answer differs from the configured one'],
  wechat_notification_invalid:['微信支付通知无法解密，请核对 APIv3 密钥','The WeChat Pay notice cannot be decrypted — check the APIv3 key'],
  wechat_refund_balance:['商户号可用余额不足以退款，请先充值或等待资金到账后再退款','The merchant balance is too low for this refund — top up or wait for funds to settle'],
  wechat_refund_overdue:['这笔订单超过了微信支付允许退款的期限','This order is past the period in which WeChat Pay allows refunds'],
  wechat_refund_user_abnormal:['微信支付提示付款用户账户异常，无法原路退款','WeChat Pay reports the payer account is abnormal, so the refund cannot go back to it'],
  channel_check_unavailable:['这个渠道暂不支持在线检查','Online checks are not available for this channel'],
  alipay_public_key_is_app_key: ['这里填成了应用公钥，请改填支付宝提供的公钥', 'This is your app public key; use the public key supplied by Alipay'],
}
export const errorText = (error) => (error.serverMessage || t(...((error.message === 'invalid_channel_key' && errors['channel_key_' + error.reason]) || errors[error.message] || ['操作未完成，请稍后重试', 'The request could not be completed — please retry']))).replace(/[。！？.!?]+$/u, '')
export async function copyText(value) {
  await navigator.clipboard.writeText(value)
}
