<script setup>
import { inject, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api, copyText, errorText, t } from '../state'
import { fullDate, status, productName, logMessage, time } from './format'
import Icon from '../components/Icon.vue'
import PaymentQrCard from './PaymentQrCard.vue'
import PaymentInstructions from './PaymentInstructions.vue'
import OrderActions from './OrderActions.vue'
const c = inject('console'), dialog = ref(null), events = ref([]), busy = ref(false), error = ref(''), notice = ref('')
let sequence = 0, disposed = false
async function refreshDetails() {
  const order = c.selectedOrder.value, version = ++sequence
  if (!order) return
  try {
    const [data, latest] = await Promise.all([api(`/api/console/events?search=${encodeURIComponent(order.out_trade_no)}&page_size=50`), api(`/api/v1/payments/${order.out_trade_no}${order.deleted_at ? '?deleted=1' : ''}`)])
    if (!disposed && version === sequence && c.selectedOrder.value?.out_trade_no === order.out_trade_no) {
      events.value = data.events.slice().reverse()
      Object.assign(c.selectedOrder.value, latest.order)
      error.value = latest.verification.error ? errorText(new Error(latest.verification.error)) : ''
    }
  } catch (e) { if (version === sequence) error.value = errorText(e) }
}
watch(c.selectedOrder, async order => {
  sequence++
  if (!order) { dialog.value?.close(); return }
  await nextTick(); if (!dialog.value.open) dialog.value.showModal()
  error.value = ''; notice.value = ''; events.value = []
  await refreshDetails()
})
watch(c.revision, () => { if (!busy.value) refreshDetails() })
onBeforeUnmount(() => { disposed = true; sequence++ })
async function sync() {
  busy.value = true; error.value = ''
  try {
    const data = await api(`/api/v1/payments/${c.selectedOrder.value.out_trade_no}/sync`, { method: 'POST' })
    Object.assign(c.selectedOrder.value, data.order)
    await refreshDetails()
    notice.value = data.message === 'awaiting_checkout' ? t('支付宝暂未查到交易，后台会继续查询', 'No Alipay trade yet — verification continues in the background') : data.message === 'scheduled' ? t('正在核验，请稍候', 'Verification is in progress') : t('已同步支付宝交易状态', 'Alipay status synchronized')
    c.refresh(true)
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
async function copy(value) { try { await copyText(value); notice.value = t('已复制。', 'Copied.') } catch { notice.value = t('请选中文字手动复制。', 'Select the text to copy.') } }
function openCheckout() { api(`/api/v1/payments/${c.selectedOrder.value.out_trade_no}/checkout-opened`, { method: 'POST' }).catch(() => {}) }
</script>
<template>
  <dialog ref="dialog" class="order-drawer" :aria-label="t('订单详情', 'Order details')" @cancel.prevent="!busy && (c.selectedOrder.value = null)">
    <template v-if="c.selectedOrder.value"><div class="drawer-header"><span>{{ t('订单详情', 'Order details') }}</span><button class="icon-button" :disabled="busy" :aria-label="t('关闭详情', 'Close details')" @click="c.selectedOrder.value = null"><Icon name="x" /></button></div><div class="drawer-body"><div class="order-amount"><span class="state-badge" :class="c.selectedOrder.value.status"><i></i>{{ status(c.selectedOrder.value.status) }}</span><h2><small>¥</small>{{ c.selectedOrder.value.amount }}</h2><p>{{ c.selectedOrder.value.subject }}</p></div><dl class="detail-list"><div><dt>{{ t('商户订单号', 'Order number') }}</dt><dd><code>{{ c.selectedOrder.value.out_trade_no }}</code><button class="icon-button" @click="copy(c.selectedOrder.value.out_trade_no)" :aria-label="t('复制订单号', 'Copy order number')"><Icon name="copy" :size="14" /></button></dd></div><div><dt>{{ t('支付宝交易号', 'Alipay trade number') }}</dt><dd><code>{{ c.selectedOrder.value.trade_no || '—' }}</code></dd></div><div><dt>{{ t('支付产品', 'Payment product') }}</dt><dd>{{ productName(c.selectedOrder.value.product) }}</dd></div><div><dt>{{ t('创建时间', 'Created') }}</dt><dd>{{ fullDate(c.selectedOrder.value.created_at) }}</dd></div><div><dt>{{ t('付款截止时间', 'Payment deadline') }}</dt><dd>{{ fullDate(c.selectedOrder.value.expires_at) }}</dd></div><div><dt>{{ t('支付时间', 'Paid') }}</dt><dd>{{ fullDate(c.selectedOrder.value.paid_at) }}</dd></div><div><dt>{{ t('已退款金额', 'Refunded amount') }}</dt><dd>¥ {{ c.selectedOrder.value.refunded_amount || '0.00' }}</dd></div><div><dt>{{ t('支付宝状态', 'Alipay status') }}</dt><dd>{{ c.selectedOrder.value.provider_status || '—' }}</dd></div><div><dt>{{ t('商户回调地址', 'Merchant callback') }}</dt><dd class="break-all">{{ c.selectedOrder.value.notify_url || t('未设置', 'Not set') }}</dd></div></dl><p v-if="error" role="alert" class="error-box">{{ error }}</p><p v-if="notice" role="status" class="notice-box">{{ notice }}</p><div class="drawer-actions"><button class="button button-secondary" :disabled="busy || !!c.selectedOrder.value.deleted_at" @click="sync"><Icon :name="busy ? 'loader' : 'refresh'" :class="{ spin: busy }" :size="16" />{{ t('查询支付宝', 'Query Alipay') }}</button><a v-if="c.selectedOrder.value.pay_url && !c.selectedOrder.value.deleted_at && c.selectedOrder.value.status === 'CREATED'" class="button" :href="c.selectedOrder.value.pay_url" target="_blank" rel="noopener noreferrer" @click="openCheckout">{{ t('打开收银台', 'Open checkout') }}<Icon name="external" :size="15" /></a></div><PaymentQrCard v-if="c.selectedOrder.value.pay_url" :key="c.selectedOrder.value.out_trade_no" :order="c.selectedOrder.value" /><PaymentInstructions v-else-if="c.selectedOrder.value.payment_payload?.kind" :order="c.selectedOrder.value" /><OrderActions :order="c.selectedOrder.value" :details="false" /><div class="timeline-heading"><h3>{{ t('订单时间线', 'Order timeline') }}</h3><button class="text-link" @click="c.navigate('logs', { q: c.selectedOrder.value.out_trade_no }); c.selectedOrder.value = null">{{ t('完整日志', 'Full log') }}<Icon name="chevron" :size="13" /></button></div><ol class="order-timeline"><li v-for="event in events" :key="event.id"><span class="timeline-dot" :class="{ accepted: event.accepted }"></span><div><b>{{ logMessage(event.reason) }}</b><small>{{ time(event.created_at) }} · {{ event.stage }}</small></div></li><li v-if="!events.length" class="muted">{{ t('暂无关联事件', 'No associated events') }}</li></ol></div></template>
  </dialog>
</template>
