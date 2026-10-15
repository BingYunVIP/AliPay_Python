<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, auth, copyText, errorText, isAdmin, locale, t } from '../state'
import Icon from '../components/Icon.vue'

const router = useRouter()
const orders = ref([]), events = ref([]), capabilities = ref(null), diagnostics = ref(null)
const tab = ref('orders'), busy = ref(false), error = ref(''), notice = ref(''), search = ref('')
const paymentDialog = ref(null), keyDialog = ref(null), result = ref(null), newKey = ref(''), keyBusy = ref(false)
const subject = ref(''), amount = ref('0.01'), product = ref('page'), syncBusy = ref('')
let refreshTimer, disposed = false, loading = false
const filteredOrders = computed(() => orders.value.filter(o => `${o.out_trade_no} ${o.subject}`.toLowerCase().includes(search.value.toLowerCase())))
const filteredEvents = computed(() => events.value.filter(e => `${e.out_trade_no || ''} ${e.stage} ${e.reason}`.toLowerCase().includes(search.value.toLowerCase())))
const paid = computed(() => orders.value.filter(o => o.status === 'PAID'))
const paidAmount = computed(() => (paid.value.reduce((sum, o) => sum + Math.round(Number(o.amount) * 100), 0) / 100).toFixed(2))
const tabs = computed(() => [{ id: 'orders', label: t('交易记录', 'Transactions'), icon: 'card' }, { id: 'logs', label: t('事件日志', 'Event log'), icon: 'terminal' }, { id: 'keys', label: t('API 密钥', 'API keys'), icon: 'key' }, ...(isAdmin.value ? [{ id: 'diagnostics', label: t('回调检查', 'Callback checks'), icon: 'shield' }] : [])])
const time = timestamp => timestamp ? new Date(timestamp * 1000).toLocaleString(locale.value === 'zh' ? 'zh-CN' : 'en-GB', { hour12: false }) : '—'
const statusText = status => ({ CREATED: t('等待支付', 'Awaiting payment'), PAID: t('已支付', 'Paid'), CLOSED: t('已关闭', 'Closed') })[status] || status
const messages = {
  request_validated: ['创建请求已通过校验', 'Creation request validated'],
  checkout_url_signed: ['SDK 已生成支付链接，等待打开收银台', 'SDK signed a checkout URL; awaiting checkout'],
  order_saved: ['本地订单已保存，尚未确认付款', 'Local order saved; payment not yet confirmed'],
  same_order_returned: ['重复创建请求，已返回原订单', 'Duplicate request; original order returned'],
  query_requested: ['开始向支付宝查询交易', 'Query sent to Alipay'],
  sdk_response_received: ['已收到 SDK 查询返回内容', 'Received SDK query response'],
  notification_received: ['收到回调，内容尚未验证', 'Notification received; not yet verified'],
  signature_valid: ['RSA2 验签通过，继续核对业务字段', 'RSA2 signature valid; checking payment details'],
  notification_verified_and_saved: ['金额、应用与收款方一致，订单已更新', 'Amount and merchant verified; order updated'],
  invalid_signature: ['验签失败，已拒绝回调', 'Invalid signature; notification rejected'],
  amount_mismatch: ['金额不符，订单未更新', 'Amount mismatch; order unchanged'],
  merchant_mismatch: ['应用或收款方不符，已拒绝', 'App or seller mismatch; rejected'],
  unknown_order: ['本地不存在该订单，已拒绝', 'Unknown order; rejected'],
  trade_mismatch: ['交易号不符，已拒绝', 'Trade number mismatch; rejected'],
  trade_conflict: ['交易号已关联其他订单，已拒绝', 'Trade number belongs to another order; rejected'],
  browser_return_is_not_payment_confirmation: ['浏览器已返回，此步骤不确认到账', 'Browser returned; this does not confirm payment'],
  checkout_open_requested: ['用户点击打开收银台，等待支付宝确认', 'User clicked checkout; awaiting confirmation from Alipay'],
  payment_provider_error: ['支付宝 SDK 调用失败，请检查配置', 'Alipay SDK call failed; check configuration'],
  payment_not_configured: ['支付宝配置不完整，请检查 APP ID、收款方 UID 和密钥', 'Alipay configuration is incomplete; check app ID, seller UID, and keys'],
  invalid_amount: ['订单金额格式不正确，已拒绝创建', 'Invalid amount; order creation rejected'],
  test_amount_exceeded: ['订单金额超过单笔测试上限', 'Amount exceeds the per-order test limit'],
  order_conflict: ['订单号重复且请求内容不同，已拒绝', 'Order number reused with different details; rejected'],
  invalid_merchant_callback: ['商户回调地址不符合配置的允许范围', 'Merchant callback URL is outside the configured allowlist'],
  unsupported_product: ['此支付产品或渠道尚未接入', 'This payment product or channel is not available yet'],
  TRADE_SUCCESS: ['交易成功，已记录到账', 'Payment confirmed'],
  TRADE_FINISHED: ['交易完成', 'Trade finished'],
  TRADE_CLOSED: ['收到交易关闭通知', 'Trade closed notification received'],
  WAIT_BUYER_PAY: ['等待买家付款', 'Waiting for buyer payment'],
  merchant_callback_delivered: ['商户已确认接收支付回调', 'Merchant acknowledged the payment notification'],
  merchant_callback_retry: ['商户回调失败，已安排自动重试', 'Merchant callback failed; automatic retry scheduled'],
  merchant_callback_failed: ['商户回调重试已用尽，请检查接收站点', 'Merchant callback retries exhausted; check the receiving site'],
}
const logMessage = reason => messages[reason] ? t(...messages[reason]) : reason

function moveTab(event) {
  const index = tabs.value.findIndex(item => item.id === tab.value)
  const direction = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
  if (!direction && !['Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.value.length - 1 : (index + direction + tabs.value.length) % tabs.value.length
  tab.value = tabs.value[next].id
  nextTick(() => document.getElementById(`tab-${tab.value}`)?.focus())
}
async function refresh() {
  if (loading || disposed) return
  loading = true
  try {
    const [o, e] = await Promise.all([api('/api/v1/payments'), api('/api/events')])
    if (!disposed) { orders.value = o.orders; events.value = e.events }
  } catch (e) {
    if (!disposed) { error.value = errorText(e); if (e.status === 401) router.push('/login') }
  } finally { loading = false }
}
onMounted(async () => {
  try {
    const [caps, me] = await Promise.all([api('/api/capabilities'), api('/api/auth/me')])
    capabilities.value = caps; auth.key = me.api_key
    if (isAdmin.value) diagnostics.value = await api('/api/admin/diagnostics')
  } catch (e) { error.value = errorText(e) }
  await refresh()
  if (!disposed) refreshTimer = setInterval(() => { if (!document.hidden) refresh() }, 6000)
})
onBeforeUnmount(() => { disposed = true; clearInterval(refreshTimer); newKey.value = '' })
async function logout() {
  try { await api('/api/auth/logout', { method: 'POST' }); auth.user = null; auth.csrf = ''; auth.key = null; router.push('/') }
  catch (e) { error.value = errorText(e) }
}
async function openPayment() {
  subject.value = t('冰云支付测试订单', 'BingYun Pay test order'); result.value = null; error.value = ''
  await nextTick(); paymentDialog.value.showModal()
}
async function createPayment() {
  busy.value = true; error.value = ''
  try {
    const number = `PAY_${Date.now()}_${crypto.randomUUID().slice(0, 8)}`
    const data = await api('/api/v1/payments', { method: 'POST', body: JSON.stringify({ out_trade_no: number, subject: subject.value, amount: amount.value, product: product.value, channel: 'alipay' }) })
    result.value = data.order; await refresh()
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
async function sync(order) {
  syncBusy.value = order.out_trade_no; error.value = ''; notice.value = ''
  try {
    const data = await api(`/api/v1/payments/${order.out_trade_no}/sync`, { method: 'POST' })
    notice.value = data.message === 'awaiting_checkout' ? t('支付宝尚未查到交易。仅生成链接不会建单，请先打开收银台。', 'No trade found yet. Open checkout first; signing a URL does not create a trade at Alipay.') : t('已查询支付宝，订单状态已同步。', 'Queried Alipay and refreshed the order status.')
    await refresh()
  } catch (e) { error.value = errorText(e) }
  finally { syncBusy.value = '' }
}
function checkoutOpened(order) {
  api(`/api/v1/payments/${order.out_trade_no}/checkout-opened`, { method: 'POST' }).catch(() => {})
}
async function makeKey() {
  keyBusy.value = true; error.value = ''
  try {
    const data = await api('/api/keys', { method: 'POST' }); newKey.value = data.api_key
    auth.key = { suffix: newKey.value.slice(-4) }; notice.value = ''
  } catch (e) { error.value = errorText(e) }
  finally { keyBusy.value = false }
}
function closeKey() { keyDialog.value.close(); newKey.value = '' }
async function copy(value) {
  try { await copyText(value); notice.value = t('已复制到剪贴板。', 'Copied to clipboard.') }
  catch { notice.value = t('无法访问剪贴板，请选中文字手动复制。', 'Clipboard unavailable. Please select and copy the text.') }
}
</script>
<template>
  <div class="dashboard byh-wrap">
    <div class="dashboard-heading"><div><span class="section-kicker">{{ isAdmin ? 'ADMIN CONSOLE' : 'PAYMENT CONSOLE' }}</span><h1>{{ t('支付控制台', 'Payment console') }}</h1><p>{{ auth.user?.email }}<span class="role-label">{{ isAdmin ? t('管理员', 'Administrator') : t('普通用户', 'User') }}</span></p></div><div class="heading-actions"><button class="icon-button" @click="logout" :aria-label="t('退出登录', 'Sign out')" :title="t('退出登录', 'Sign out')"><Icon name="logout" /></button><button class="button" @click="openPayment"><Icon name="plus" :size="17" />{{ t('创建测试订单', 'Create test order') }}</button></div></div>
    <div v-if="capabilities" class="console-note"><Icon name="help" :size="17" /><span>{{ capabilities.environment === 'production' ? t('当前为支付宝正式环境。打开收银台并付款会产生真实交易。', 'Alipay production environment. Completing checkout creates a real payment.') : t('当前为支付宝沙箱环境。', 'Alipay sandbox environment.') }} {{ t('单笔测试上限', 'Test limit') }} ¥{{ capabilities.max_amount }}</span></div>
    <p v-if="error && !paymentDialog?.open && !keyDialog?.open" class="error-box" role="alert">{{ error }}</p><p v-if="notice" class="notice-box" role="status">{{ notice }}</p>
    <div class="stats-grid"><article class="panel stat"><div><span>{{ t('确认收款', 'Confirmed payments') }}</span><Icon name="card" /></div><strong><small>¥</small>{{ paidAmount }}</strong><p>{{ t('最近 100 笔订单 · CNY', 'Latest 100 orders · CNY') }}</p></article><article class="panel stat"><div><span>{{ t('成功订单', 'Paid orders') }}</span><Icon name="success" /></div><strong>{{ paid.length }}<small>{{ t(' 笔', ' orders') }}</small></strong><p>{{ t('以验签回调或主动查询确认为准', 'Confirmed by verified notification or query') }}</p></article><article class="panel stat"><div><span>{{ t('等待支付', 'Awaiting payment') }}</span><Icon name="timer" /></div><strong>{{ orders.filter(o => o.status === 'CREATED').length }}<small>{{ t(' 笔', ' orders') }}</small></strong><p>{{ t('仅生成链接不代表付款成功', 'A checkout URL does not confirm payment') }}</p></article></div>
    <div class="dashboard-tabs" role="tablist" @keydown="moveTab" :aria-label="t('控制台内容', 'Console sections')"><button v-for="item in tabs" :key="item.id" role="tab" :tabindex="tab === item.id ? 0 : -1" :aria-selected="tab === item.id" :aria-controls="`panel-${item.id}`" :id="`tab-${item.id}`" :class="{ selected: tab === item.id }" @click="tab = item.id; search = ''; notice = ''; error = ''"><Icon :name="item.icon" :size="17" />{{ item.label }}</button></div>
    <section v-if="tab === 'orders' || tab === 'logs'" :id="`panel-${tab}`" role="tabpanel" :aria-labelledby="`tab-${tab}`" class="panel data-panel">
      <div class="panel-toolbar"><div><h2>{{ tab === 'orders' ? t('交易记录', 'Transactions') : t('支付事件日志', 'Payment event log') }}</h2><p>{{ tab === 'orders' ? t('每 6 秒刷新本地状态；查询支付宝需手动点击。', 'Local status refreshes every 6 seconds. Alipay queries are manual.') : t('展开一条日志，查看脱敏后的参数与返回内容。', 'Expand an event to inspect redacted parameters and responses.') }}</p></div><div class="table-tools"><label class="search-field"><Icon name="search" :size="17" /><input v-model="search" :placeholder="t('搜索订单号', 'Search order number')" :aria-label="t('搜索订单号', 'Search order number')" /></label><button class="icon-button" @click="refresh" :aria-label="t('刷新记录', 'Refresh records')"><Icon name="refresh" :size="18" /></button></div></div>
      <div v-if="tab === 'orders' && filteredOrders.length" class="table-scroll"><table><thead><tr><th>{{ t('订单 / 商品', 'Order / Item') }}</th><th>{{ t('金额', 'Amount') }}</th><th>{{ t('状态', 'Status') }}</th><th>{{ t('创建时间', 'Created') }}</th><th class="align-right">{{ t('操作', 'Actions') }}</th></tr></thead><tbody><tr v-for="order in filteredOrders" :key="order.out_trade_no"><td><b>{{ order.subject }}</b><code>{{ order.out_trade_no }}</code></td><td class="amount-cell">¥{{ order.amount }}</td><td><span class="status-chip" :class="order.status">{{ statusText(order.status) }}</span></td><td class="time-cell">{{ time(order.created_at) }}</td><td class="order-actions"><a v-if="order.status === 'CREATED'" :href="order.pay_url" target="_blank" rel="noopener noreferrer" class="text-link" @click="checkoutOpened(order)">{{ t('去支付', 'Pay') }}<Icon name="external" :size="14" /></a><button class="text-link" :disabled="!!syncBusy" @click="sync(order)"><Icon v-if="syncBusy === order.out_trade_no" class="spin" name="loader" :size="14" />{{ t('查询支付宝', 'Query Alipay') }}</button><button class="subtle-button" @click="tab = 'logs'; search = order.out_trade_no">{{ t('日志', 'Logs') }}</button></td></tr></tbody></table></div>
      <div v-else-if="tab === 'logs' && filteredEvents.length" class="event-list"><details v-for="event in filteredEvents" :key="event.id" class="event-row"><summary><span class="event-state" :class="{ rejected: !event.accepted }"><Icon :name="event.accepted ? 'check' : 'help'" :size="14" /></span><span class="event-message"><b>{{ logMessage(event.reason) }}</b><span>{{ event.out_trade_no || t('系统 / 待验证通知', 'System / unverified notification') }}<span class="event-stage">{{ event.stage }}</span></span></span><time>{{ time(event.created_at) }}</time><Icon name="chevron" :size="16" /></summary><pre>{{ JSON.stringify(event.details, null, 2) }}</pre></details></div>
      <div v-else class="empty-state"><span class="empty-icon"><Icon :name="tab === 'orders' ? 'card' : 'terminal'" :size="30" /></span><h3>{{ search ? t('没有匹配的记录', 'No matching records') : tab === 'orders' ? t('你的第一笔支付，从这里开始', 'Your first payment starts here') : t('暂时没有支付日志', 'No payment events yet') }}</h3><p>{{ search ? t('试试其他订单号或清空搜索。', 'Try another order number or clear the search.') : t('创建一笔测试订单后，处理过程会清晰地记录在这里。', 'Create a test order to follow every step here.') }}</p><button v-if="!search && tab === 'orders'" class="button button-secondary" @click="openPayment"><Icon name="plus" :size="16" />{{ t('创建测试订单', 'Create test order') }}</button></div>
    </section>
    <section v-if="tab === 'keys'" id="panel-keys" role="tabpanel" aria-labelledby="tab-keys" class="panel settings-panel"><span class="large-icon"><Icon name="key" :size="28" /></span><h2>{{ t('连接你的应用', 'Connect your application') }}</h2><p>{{ t('API 密钥用于服务器调用。每个账户拥有独立密钥和订单，密钥仅在生成时显示一次。', 'Use an API key for server requests. Each account has separate keys and orders. A key is shown only when generated.') }}</p><div class="key-preview"><code>{{ auth.key ? `byp_••••••••••••••••${auth.key.suffix}` : t('尚未创建密钥', 'No key created yet') }}</code><span class="status-chip" :class="auth.key ? 'PAID' : 'CLOSED'">{{ auth.key ? t('已配置', 'Configured') : t('待配置', 'Not configured') }}</span></div><button class="button" @click="newKey = ''; notice = ''; keyDialog.showModal()"><Icon name="key" :size="17" />{{ auth.key ? t('重新生成密钥', 'Rotate API key') : t('生成 API 密钥', 'Generate API key') }}</button><p class="settings-hint"><Icon name="lock" :size="15" />{{ t('将密钥保存为服务器环境变量 BYPAY_API_KEY，不要放在网页前端。', 'Store the key in a server environment variable named BYPAY_API_KEY.') }}</p></section>
    <section v-if="tab === 'diagnostics' && isAdmin" id="panel-diagnostics" role="tabpanel" aria-labelledby="tab-diagnostics" class="panel settings-panel"><span class="large-icon"><Icon name="shield" :size="28" /></span><h2>{{ t('检查支付宝回调配置', 'Check your Alipay callback') }}</h2><p>{{ t('这里检查本机配置，不会发起支付请求。域名需解析到 VPS，HTTPS 需可从公网访问。', 'These local checks do not send payment requests. Point your domain to the VPS and make HTTPS publicly accessible.') }}</p><template v-if="diagnostics"><div class="diagnostic-urls"><label>{{ t('站点域名', 'Site origin') }}<code>{{ diagnostics.origin }}</code></label><label>notify_url<code>{{ diagnostics.notify_url }}</code></label><label>return_url<code>{{ diagnostics.return_url }}</code></label></div><ul class="diagnostic-checks"><li v-for="check in diagnostics.checks" :key="check.name"><Icon :name="check.ok ? 'success' : 'alert'" :class="check.ok ? 'positive' : 'warning'" :size="18" /><span>{{ ({ app_id: 'APP ID', seller_id: t('收款方 UID', 'Seller UID'), app_private_key: t('应用私钥', 'App private key'), alipay_public_key: t('支付宝公钥', 'Alipay public key'), sdk_keys: t('SDK 密钥格式', 'SDK key format') })[check.name] }}</span><b>{{ check.ok ? t('已通过', 'Passed') : t('待配置', 'Needs setup') }}</b></li></ul><div class="console-note"><Icon name="help" :size="18" /><span>{{ t('支付宝通知固定由 pay.bingyun.vip 接收。独立商户测试站 test.bingyun.vip 通过 API 下单，并在自己的 /notify 接收冰云支付签名后的业务回调。', 'Alipay notifies pay.bingyun.vip. The independent merchant demo at test.bingyun.vip creates orders through the API and receives signed Pay notifications at its own /notify endpoint.') }}</span></div></template></section>
  </div>

  <dialog ref="paymentDialog" class="dialog" :aria-label="t('创建测试订单', 'Create test order')" @cancel="!busy || $event.preventDefault()"><div class="dialog-header"><span class="section-kicker">NEW PAYMENT</span><button class="icon-button" :disabled="busy" @click="paymentDialog.close(); error = ''" :aria-label="t('关闭', 'Close')"><Icon name="x" /></button></div><template v-if="!result"><h2>{{ t('创建测试订单', 'Create a test order') }}</h2><p>{{ t('先生成支付链接，再由你打开支付宝收银台付款。', 'Generate a payment link, then open Alipay checkout to pay.') }}</p><form @submit.prevent="createPayment"><label for="subject">{{ t('商品名称', 'Item name') }}</label><input id="subject" v-model="subject" maxlength="128" required /><div class="form-row"><div><label for="amount">{{ t('金额（元）', 'Amount (CNY)') }}</label><input id="amount" v-model="amount" inputmode="decimal" required pattern="[0-9]+(\.[0-9]{1,2})?" /></div><div><label for="product">{{ t('支付方式', 'Product') }}</label><select id="product" v-model="product"><option value="page">{{ t('支付宝 · 网页支付', 'Alipay · Web') }}</option><option value="wap">{{ t('支付宝 · 手机支付', 'Alipay · Mobile') }}</option></select></div></div><p v-if="error" class="error-box" role="alert">{{ error }}</p><button class="button full-width" :disabled="busy"><Icon :name="busy ? 'loader' : 'arrow'" :class="{ spin: busy }" :size="17" />{{ t('生成支付链接', 'Generate checkout link') }}</button></form></template><template v-else><div class="result-icon"><Icon name="check" :size="28" /></div><h2>{{ t('支付链接已生成', 'Checkout link created') }}</h2><p>{{ t('订单已保存，当前尚未确认付款。完成付款后，回调会自动更新订单。', 'Your order is saved. Payment is not yet confirmed. The notification will update it after payment.') }}</p><div class="payment-summary"><strong>¥{{ result.amount }}</strong><span>{{ result.subject }}</span><code>{{ result.out_trade_no }}</code></div><a class="button full-width" :href="result.pay_url" target="_blank" rel="noopener noreferrer" @click="checkoutOpened(result)">{{ t('打开支付宝收银台', 'Open Alipay checkout') }}<Icon name="external" :size="17" /></a><button class="button button-secondary full-width" @click="paymentDialog.close(); tab = 'logs'; search = result.out_trade_no">{{ t('查看订单日志', 'Follow order events') }}</button></template></dialog>

  <dialog ref="keyDialog" class="dialog" :aria-label="t('管理 API 密钥', 'Manage API key')" @close="newKey = ''" @cancel="!keyBusy || $event.preventDefault()"><div class="dialog-header"><span class="section-kicker">API KEY</span><button class="icon-button" :disabled="keyBusy" @click="closeKey" :aria-label="t('关闭', 'Close')"><Icon name="x" /></button></div><h2>{{ newKey ? t('保存你的 API 密钥', 'Save your API key') : auth.key ? t('重新生成 API 密钥', 'Rotate your API key') : t('生成 API 密钥', 'Generate your API key') }}</h2><p>{{ newKey ? t('密钥只显示这一次，请复制并妥善保存。', 'This key is shown once. Copy it and store it securely.') : auth.key ? t('旧密钥会立即失效，使用旧密钥的应用需要更新。', 'The old key will stop working immediately. Update applications that use it.') : t('使用此密钥从服务器调用冰云支付接口。', 'Use this key to call Pay from your server.') }}</p><p v-if="error" class="error-box" role="alert">{{ error }}</p><template v-if="newKey"><textarea class="key-secret" readonly :value="newKey" :aria-label="t('新的 API 密钥', 'New API key')"></textarea><button class="button full-width" @click="copy(newKey)"><Icon name="copy" :size="17" />{{ t('复制密钥', 'Copy key') }}</button><p v-if="notice" role="status">{{ notice }}</p></template><button v-else class="button full-width" :disabled="keyBusy" @click="makeKey"><Icon v-if="keyBusy" class="spin" name="loader" :size="17" />{{ t('确认生成', 'Generate key') }}</button></dialog>
</template>
