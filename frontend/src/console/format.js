import { errors, locale, t } from '../state'

export const amount = cents => (Number(cents || 0) / 100).toLocaleString(locale.value === 'zh' ? 'zh-CN' : 'en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const date = timestamp => timestamp ? new Date(timestamp * 1000).toLocaleString(locale.value === 'zh' ? 'zh-CN' : 'en-GB', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—'
export const fullDate = timestamp => timestamp ? new Date(timestamp * 1000).toLocaleString(locale.value === 'zh' ? 'zh-CN' : 'en-GB', { hour12: false }) : '—'
export const dayOnly = timestamp => timestamp ? new Date(timestamp * 1000).toLocaleDateString(locale.value === 'zh' ? 'zh-CN' : 'en-GB', { year: 'numeric', month: '2-digit', day: '2-digit' }) : '—'
export const time = timestamp => timestamp ? new Date(timestamp * 1000).toLocaleTimeString(locale.value === 'zh' ? 'zh-CN' : 'en-GB', { hour12: false }) : '—'
export const status = value => ({ CREATED: t('待支付', 'Awaiting payment'), CONFIRMING:t('正在确认','Confirming'), PAID: t('已支付', 'Paid'), CLOSED: t('已关闭', 'Closed'), EXPIRED: t('已过期', 'Expired'), REFUNDING: t('退款处理中', 'Refund pending'), PARTIALLY_REFUNDED: t('部分退款', 'Partially refunded'), REFUNDED: t('已退款', 'Refunded'), pending: t('等待投递', 'Pending'), delivered: t('投递成功', 'Delivered'), failed: t('投递失败', 'Failed') })[value] || value
export const productName = value => ({ page: t('网页支付', 'Web payment'), wap: t('手机支付', 'Mobile payment'), app: t('APP 支付', 'App payment'), mini: t('小程序支付', 'Mini program') })[value] || value
const messages = {
  admin_credentials_initialized:['管理员首次凭据已修改','Administrator credentials initialized'],pricing_settings_saved:['汇率设置已保存','Pricing settings saved'],crypto_policy_saved:['Crypto 参数已保存','Crypto policy saved'],channel_config_deleted:['收款配置已删除','Channel configuration deleted'],
  crypto_refund_reservation_cancelled:['Crypto 退款预留已取消','Crypto refund reservation cancelled'],
  crypto_network_checked: ['Crypto 网络已检查', 'Crypto network checked'],
  crypto_transfer_observed: ['发现匹配的链上转账', 'Matching chain transfer observed'],
  crypto_scan_failed: ['扫链失败，等待重试', 'Chain scan failed; retry pending'],
  crypto_payment_confirmed: ['链上收款已确认', 'Chain payment confirmed'],
  crypto_transfer_seen: ['链上转账等待确认', 'Chain transfer awaiting confirmation'],
  crypto_confirmed: ['链上收款已入账', 'Chain payment recorded'],
  crypto_reorg: ['检测到链重组，重新核验', 'Chain reorganization detected'],
  crypto_refund_transfer_required: ['退款已预留，等待人工链上转账', 'Refund reserved; manual transfer required'],
  crypto_refund_evidence_recorded: ['管理员已登记链上退款凭据', 'Administrator recorded refund evidence'],

  cashier_frame_ready: ['支付宝原生二维码页面已就绪', 'Alipay original QR frame ready'],
  cashier_qr_requested: ['正在读取支付宝收银台二维码', 'Loading the Alipay cashier QR'],
  cashier_qr_ready: ['支付宝收银台二维码已就绪', 'Alipay cashier QR ready'],
  cashier_qr_unavailable: ['未能获取支付宝收银台二维码', 'Alipay cashier QR unavailable'],
  merchant_group_saved: ['商户分组费率已保存', 'Billing group updated'],
  merchant_group_assigned: ['商户计费分组已调整', 'Merchant billing group changed'],
  crypto_network_saved: ['Crypto 网络配置已保存', 'Crypto network settings saved'],
  crypto_order_created: ['Crypto 收款订单已创建', 'Crypto payment created'],
  channel_config_added: ['已添加收款配置', 'Payment configuration added'],
  order_deleted: ['订单已移入已删除', 'Order moved to deleted'],
  order_restored: ['订单已恢复', 'Order restored'],
  refund_requested: ['已提交退款请求', 'Refund requested'],
  refund_response_received: ['收到支付宝退款响应', 'Alipay refund response received'],
  refund_query_received: ['收到支付宝退款查询结果', 'Alipay refund query received'],
  refund_success: ['退款已确认', 'Refund confirmed'],
  refund_failed: ['退款未成功', 'Refund rejected'],
  refund_unknown: ['退款结果待确认', 'Refund pending confirmation'],
  refund_unconfirmed: ['退款结果待确认，将按原请求号核验', 'Refund unconfirmed; verifying the original request'],
  request_validated: ['创建请求已通过校验', 'Creation request validated'],
  checkout_url_signed: ['SDK 已生成支付链接', 'SDK signed the checkout URL'],
  app_order_signed: ['已生成 APP 支付参数，等待客户端调起', 'App payment parameters signed — awaiting native checkout'],
  provider_creation_requested: ['正在通过官方接口创建交易', 'Creating a trade through the official API'],
  checkout_ready: ['支付参数已就绪，等待付款', 'Checkout is ready — awaiting payment'],
  order_saved: ['订单已保存，等待付款', 'Order saved, awaiting payment'],
  same_order_returned: ['重复请求，返回原订单', 'Duplicate request; original order returned'],
  query_requested: ['正在查询支付宝交易', 'Query sent to Alipay'],
  sdk_response_received: ['已收到支付宝查询结果', 'Alipay query response received'],
  query_verified_and_saved: ['查询结果验签与订单校验通过，状态已同步', 'Query verified and order status synchronized'],
  awaiting_checkout: ['渠道尚未生成交易，继续等待', 'The channel has no transaction yet — waiting'],
  payment_response_signature_invalid: ['支付宝查询响应验签失败，保留原订单状态', 'Response signature invalid — order unchanged'],
  payment_request_signature_invalid: ['支付宝请求签名不匹配，请核对 APPID 与密钥', 'Request signature mismatch — check App ID and keys'],
  payment_app_id_invalid: ['支付宝 APPID 无效', 'Alipay App ID is invalid'],
  payment_mismatch: ['渠道返回的交易与订单不匹配，拒绝更新', 'The channel result does not match the order — update rejected'],
  notification_received: ['收到支付通知，等待验证', 'Payment notification received; awaiting verification'],
  signature_valid: ['通知签名验证通过', 'Notification signature verified'],
  notification_verified_and_saved: ['订单、金额与商户校验通过', 'Order, amount, and merchant verified'],
  invalid_signature: ['签名验证失败，拒绝通知', 'Invalid signature; notification rejected'],
  amount_mismatch: ['订单金额不匹配', 'Order amount mismatch'],
  merchant_mismatch: ['应用或收款商户不匹配', 'App or merchant mismatch'],
  unknown_order: ['本地不存在该订单', 'Order not found locally'],
  trade_mismatch: ['渠道交易号不匹配', 'Channel trade number mismatch'],
  trade_conflict: ['渠道交易号已关联其他订单', 'Trade number belongs to another order'],
  browser_return_is_not_payment_confirmation: ['浏览器同步返回（不确认到账）', 'Browser returned (does not confirm payment)'],
  checkout_open_requested: ['用户打开收银台', 'User opened the checkout'],
  mobile_checkout_opened: ['手机收银台短链接被打开（不确认到账）', 'Mobile checkout link opened (does not confirm payment)'],
  payment_provider_error: ['支付渠道接口调用失败', 'Payment channel API request failed'],
  payment_not_configured: ['渠道参数尚未配置完整', 'Channel configuration incomplete'],
  TRADE_SUCCESS: ['支付成功，订单已入账', 'Payment confirmed'],
  TRADE_FINISHED: ['交易完成', 'Trade completed'],
  TRADE_CLOSED: ['交易关闭通知已处理', 'Trade closure processed'],
  WAIT_BUYER_PAY: ['等待买家付款', 'Waiting for buyer payment'],
  merchant_callback_delivered: ['商户已确认接收业务回调', 'Merchant acknowledged the callback'],
  merchant_callback_retry: ['投递失败，已安排自动重试', 'Delivery failed; retry scheduled'],
  merchant_callback_failed: ['重试已用尽，请检查商户站点', 'Retries exhausted; check merchant endpoint'],
  callback_retry_scheduled: ['已将回调重新加入投递队列', 'Callback requeued for delivery'],
  settings_saved: ['系统设置已保存', 'System settings saved'],
  orders_exported: ['已导出订单记录', 'Orders exported'],
  user_created: ['已创建普通用户', 'User created'],
  user_disabled: ['已停用用户并撤销密钥', 'User disabled and API key revoked'],
  user_enabled: ['已恢复用户访问', 'User access restored'],
  user_pay_enabled: ['已开启商户支付权限', 'Merchant payment permission enabled'],
  user_pay_disabled: ['已关闭商户支付权限', 'Merchant payment permission disabled'],
  channel_config_saved: ['渠道配置已加密保存', 'Channel configuration encrypted and saved'],
  channel_checked: ['已在线检查渠道配置', 'Channel configuration checked online'],
  wechat_query_requested: ['正在查询微信支付订单', 'Query sent to WeChat Pay'],
  wechat_query_received: ['已收到微信支付查询结果', 'WeChat Pay query response received'],
  wechat_refund_response: ['收到微信支付退款响应', 'WeChat Pay refund response received'],
  wechat_refund_query_received: ['收到微信支付退款查询结果', 'WeChat Pay refund query received'],
  refund_processing: ['微信支付正在处理退款，后台会继续查询', 'WeChat Pay is processing the refund; verification continues'],
  refund_abnormal: ['微信支付提示退款异常，请到商户平台交易中心手动处理', 'WeChat Pay reports an abnormal refund — handle it in the merchant platform'],
  refund_provider_error: ['支付渠道拒绝了退款', 'The payment channel rejected the refund'],
  domain_requested: ['商户申请了支付域名', 'A merchant requested a payment domain'],
  domain_approved: ['支付域名已通过', 'Payment domain approved'],
  domain_rejected: ['支付域名未通过', 'Payment domain rejected'],
  merchant_approved: ['商户审核已通过', 'Merchant approved'],
  merchant_rejected: ['商户审核未通过', 'Merchant rejected'],
  merchant_credentials_updated: ['商户接口凭据已更新', 'Merchant API credentials updated'],
  merchant_payment_settings_saved: ['商户交易参数已保存', 'Merchant transaction settings saved'],
  merchant_order_cancelled: ['商户请求已确认关闭订单', 'Merchant-requested order closure confirmed'],
  merchant_cancel_failed: ['支付渠道未确认关闭订单', 'Payment provider did not confirm order closure'],
  merchant_risk_released: ['已解除商户风控暂停', 'Merchant risk hold released'],
  epay_order_created: ['商户通过接口创建了订单', 'A merchant created an order through the API'],
  epay_request_rejected: ['商户接口请求被拒绝', 'A merchant API request was rejected'],
  orders_delete: ['订单已批量移入已删除', 'Orders moved to deleted'],
  orders_restore: ['订单已批量恢复', 'Orders restored'],
  smtp_saved: ['邮箱配置已保存', 'Email settings saved'],
  smtp_test_sent: ['测试邮件已提交 SMTP 服务器', 'Test email handed to the SMTP server'],
  mail_template_saved: ['邮件模板已保存', 'Email template saved'],
  turnstile_saved: ['人机验证配置已保存', 'Human check settings saved'],
  platform_policy_saved: ['商户与资金规则已保存', 'Merchant and funds rules saved'],
  settlement_paid: ['结算已登记出款', 'Settlement marked as paid'],
  settlement_cancel: ['结算申请已取消', 'Settlement cancelled'],
  platform_task_requested: ['手动执行了平台任务', 'Platform tasks run manually'],
  platform_task_failed: ['平台任务执行失败', 'A platform task failed'],
}
export const logMessage = reason => messages[reason] ? t(...messages[reason]) : errors[reason] ? t(...errors[reason]) : reason
const stages = {
  refund: ['退款处理', 'Refunds'], create: ['订单创建', 'Order creation'], sdk: ['渠道接口', 'Channel API'], query: ['交易查询', 'Payment query'], notify: ['支付通知', 'Notifications'],
  merchant: ['商户', 'Merchants'], console: ['操作审计', 'Audit'], return: ['同步返回', 'Browser return'], checkout: ['收银台', 'Checkout'], order: ['订单状态', 'Order status'],
  crypto: ['Crypto 收款', 'Crypto payments'],
  request: ['接口请求', 'API requests'], settings: ['系统设置', 'Settings'], settlement: ['结算', 'Settlements'], mail: ['邮件', 'Email'], task: ['计划任务', 'Tasks'], domain: ['支付域名', 'Domains'], risk: ['风控', 'Risk'],
}
export const logCategories = Object.keys(stages)
export const stageName = stage => { const entry = stages[stage?.split('.')[0]]; return entry ? t(...entry) : stage }
// accepted events are fine; a rejected or failed one is a problem; anything else not accepted yet is still waiting for its check
export const logLevel = event => event.accepted ? 'ok' : /\.(rejected|failed|error)$|invalid|mismatch|conflict|failed|rejected|error|unknown_order|abnormal/.test(`${event.stage} ${event.reason}`) ? 'bad' : 'wait'
const sameDay = (a, b) => a.toDateString() === b.toDateString()
export const dayLabel = timestamp => {
  const day = new Date(timestamp * 1000), now = new Date(), yesterday = new Date(now.getTime() - 86400000)
  if (sameDay(day, now)) return t('今天', 'Today')
  if (sameDay(day, yesterday)) return t('昨天', 'Yesterday')
  return day.toLocaleDateString(locale.value === 'zh' ? 'zh-CN' : 'en-GB', { year: day.getFullYear() === now.getFullYear() ? undefined : 'numeric', month: 'long', day: 'numeric', weekday: 'short' })
}
export const ago = timestamp => {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - timestamp))
  if (seconds < 10) return t('刚刚', 'just now')
  if (seconds < 60) return t(`${seconds} 秒前`, `${seconds}s ago`)
  if (seconds < 3600) return t(`${Math.floor(seconds / 60)} 分钟前`, `${Math.floor(seconds / 60)} min ago`)
  if (seconds < 86400) return t(`${Math.floor(seconds / 3600)} 小时前`, `${Math.floor(seconds / 3600)} h ago`)
  return t(`${Math.floor(seconds / 86400)} 天前`, `${Math.floor(seconds / 86400)} d ago`)
}
