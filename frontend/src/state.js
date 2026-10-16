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
  turnstile_configuration_required:['请填写 Turnstile Site Key、Secret Key 和验证域名','Complete Turnstile settings'],
  merchant_risk_hold:['商户收款因风控规则暂停，请联系管理员','Merchant risk hold'],
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
  payment_not_configured: ['支付宝尚未配置完成，请由管理员检查回调配置。', 'Alipay is not configured. Ask the administrator to check setup.'],
  payment_provider_error: ['支付宝接口返回异常，请查看事件日志并核对配置。', 'Alipay returned an error. Review the event log and configuration.'],
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
  refund_provider_error: ['支付宝未确认退款成功，请查看退款日志', 'Alipay did not confirm this refund — check the log'],
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
  alipay_public_key_is_app_key: ['这里填成了应用公钥，请改填支付宝提供的公钥', 'This is your app public key; use the public key supplied by Alipay'],
}
export const errorText = (error) => (error.serverMessage || t(...(errors[error.message] || ['操作未完成，请稍后重试', 'The request could not be completed — please retry']))).replace(/[。！？.!?]+$/u, '')
export async function copyText(value) {
  await navigator.clipboard.writeText(value)
}
