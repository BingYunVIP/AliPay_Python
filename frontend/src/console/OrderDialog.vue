<script setup>
import { computed, inject, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api, errorText, t } from '../state'
import { fullDate, logMessage, productName, status } from './format'
import Icon from '../components/Icon.vue'
import PaymentCard from './PaymentCard.vue'

// Opens over the console content area (not the sidebar), centred, for any order picked in the console.
const c = inject('console')
const dialog = ref(null), tab = ref('details'), events = ref([]), busy = ref(false), error = ref(''), notice = ref('')
const order = computed(() => c.selectedOrder.value)
const channelName = computed(() => ({ alipay: t('支付宝', 'Alipay'), wechat: t('微信支付', 'WeChat Pay'), paypal: 'PayPal', stripe: 'Stripe' })[order.value?.channel] || '')
const tabs = computed(() => [{ id: 'details', label: t('订单详情', 'Order details') }, { id: 'timeline', label: t('订单时间线', 'Order timeline') }])
let sequence = 0, disposed = false, poll, pressedOutside = false

async function refresh() {
  const current = order.value, version = ++sequence
  if (!current) return
  const number = encodeURIComponent(current.out_trade_no)
  try {
    const [latest, logs] = await Promise.all([
      api(`/api/v1/payments/${number}${current.deleted_at ? '?deleted=1' : ''}`),
      tab.value === 'timeline' ? api(`/api/console/events?search=${number}&page_size=50`) : null,
    ])
    if (disposed || version !== sequence || order.value?.out_trade_no !== current.out_trade_no) return
    Object.assign(order.value, latest.order)
    if (logs) events.value = logs.events.slice().reverse()
    error.value = latest.verification.error ? errorText(new Error(latest.verification.error)) : ''
  } catch (e) { if (version === sequence) error.value = errorText(e) }
}
function close() { if (!busy.value) c.selectedOrder.value = null }
function overlayDown(event) { pressedOutside = event.target === event.currentTarget }
function overlayClick(event) { if (pressedOutside && event.target === event.currentTarget) close(); pressedOutside = false }
async function sync() {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const data = await api(`/api/v1/payments/${encodeURIComponent(order.value.out_trade_no)}/sync`, { method: 'POST' })
    Object.assign(order.value, data.order)
    notice.value = data.message === 'awaiting_checkout' ? t('支付宝暂未查到交易，后台会继续查询', 'No Alipay trade yet — verification continues in the background') : data.message === 'scheduled' ? t('正在核验，请稍候', 'Verification is in progress') : t('已同步支付宝交易状态', 'Alipay status synchronized')
    await refresh(); c.refresh(true)
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
watch(order, async value => {
  sequence++; clearInterval(poll)
  if (!value) { dialog.value?.close(); return }
  tab.value = 'details'; error.value = ''; notice.value = ''; events.value = []
  await nextTick()
  if (!dialog.value.open) dialog.value.showModal()
  await refresh()
  // A waiting order is re-read often so the card switches to "confirmed" as soon as the worker verifies it.
  poll = setInterval(() => { if (!document.hidden && !busy.value && order.value?.status === 'CREATED') refresh() }, 3000)
})
watch(tab, () => { if (tab.value === 'timeline') refresh() })
watch(c.revision, () => { if (!busy.value) refresh() })
onBeforeUnmount(() => { disposed = true; sequence++; clearInterval(poll) })
</script>
<template>
  <dialog ref="dialog" class="order-dialog" :aria-label="t('订单详情', 'Order details')" @cancel.prevent="close" @mousedown="overlayDown" @click="overlayClick">
    <div v-if="order" class="order-modal">
      <header class="order-modal-bar">
        <div class="order-tabs" role="tablist">
          <button v-for="item in tabs" :id="`order-tab-${item.id}`" :key="item.id" type="button" role="tab" :aria-selected="tab === item.id" aria-controls="order-panel" @click="tab = item.id">{{ item.label }}</button>
        </div>
        <div class="order-modal-actions">
          <button class="button button-secondary" type="button" :disabled="busy || !!order.deleted_at" @click="sync"><Icon :name="busy ? 'loader' : 'refresh'" :class="{ spin: busy }" :size="16" />{{ t(`查询${channelName}`, `Query ${channelName}`) }}</button>
          <button class="icon-button" type="button" :disabled="busy" :aria-label="t('关闭详情', 'Close details')" @click="close"><Icon name="x" /></button>
        </div>
      </header>
      <div id="order-panel" class="order-modal-body" role="tabpanel" :aria-labelledby="`order-tab-${tab}`">
        <template v-if="tab === 'details'">
          <div class="fact-grid">
            <div class="fact-row">
              <div class="fact"><span>{{ t('商品名称', 'Item') }}</span><b>{{ order.subject }}</b></div>
              <div class="fact fact-amount"><b><small>￥</small>{{ order.amount }}</b></div>
              <div class="fact"><span class="state-badge" :class="order.status"><i></i>{{ status(order.status) }}</span></div>
            </div>
            <div class="fact-row">
              <div class="fact"><span>{{ t('商户订单号', 'Order number') }}</span><code>{{ order.out_trade_no }}</code></div>
              <div class="fact"><span>{{ t('支付产品', 'Payment product') }}</span><b>{{ productName(order.product) }}</b></div>
              <div class="fact"><span>{{ t(`${channelName}交易号`, `${channelName} trade number`) }}</span><code>{{ order.trade_no || '—' }}</code></div>
            </div>
            <div class="fact-row">
              <div class="fact"><span>{{ t('创建时间', 'Created at') }}</span><b>{{ fullDate(order.created_at) }}</b></div>
              <div class="fact"><span>{{ t('支付时间', 'Paid at') }}</span><b>{{ fullDate(order.paid_at) }}</b></div>
              <div class="fact"><span>{{ t('付款截止时间', 'Payment deadline') }}</span><b>{{ fullDate(order.expires_at) }}</b></div>
            </div>
            <div class="fact-row">
              <div class="fact"><span>{{ t('已退款金额', 'Refunded amount') }}</span><b>￥{{ order.refunded_amount || '0.00' }}</b></div>
              <div class="fact"><span>{{ t(`${channelName}状态`, `${channelName} status`) }}</span><b>{{ order.provider_status || '—' }}</b></div>
              <div class="fact"><span>{{ t('商户回调地址', 'Merchant callback') }}</span><code>{{ order.notify_url || t('未设置', 'Not set') }}</code></div>
            </div>
          </div>
          <p v-if="error" role="alert" class="error-box">{{ error }}</p>
          <p v-if="notice" role="status" class="notice-box">{{ notice }}</p>
          <PaymentCard v-if="order.pay_url || order.payment_payload?.kind" :key="order.out_trade_no" :order="order" class="order-modal-card" />
        </template>
        <ol v-else class="order-timeline">
          <li v-for="event in events" :key="event.id"><span class="timeline-dot" :class="{ accepted: event.accepted }"></span><div><b>{{ logMessage(event.reason) }}</b><small>{{ fullDate(event.created_at) }} · {{ event.stage }}</small></div></li>
          <li v-if="!events.length" class="muted">{{ t('暂无关联事件', 'No associated events') }}</li>
        </ol>
      </div>
    </div>
  </dialog>
</template>
