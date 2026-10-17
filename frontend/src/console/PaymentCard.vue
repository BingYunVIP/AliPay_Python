<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, copyText, t } from '../state'
import { fullDate, status as statusName } from './format'
import Brand from '../components/Brand.vue'
import Icon from '../components/Icon.vue'

// One receipt card for every payment product: page and wap show a scannable code,
// app and mini show the call parameters. The code never outlives the order.
const props = defineProps({ order: { type: Object, required: true } })
const now = ref(Date.now()), ready = ref(Boolean(props.order.cashier_qr) || props.order.product !== 'page')
const preparing = ref(false), failed = ref(false), attempt = ref(0), copied = ref(false), zoom = ref(null), inline = ref('')
let tick, copyTimer, disposed = false

const channelName = computed(() => ({ alipay: t('支付宝', 'Alipay'), wechat: t('微信支付', 'WeChat Pay'), paypal: 'PayPal', stripe: 'Stripe' })[props.order.channel] || props.order.channel)
const remaining = computed(() => Math.max(0, Math.ceil(props.order.expires_at - now.value / 1000)))
const countdown = computed(() => `${Math.floor(remaining.value / 60)}:${String(remaining.value % 60).padStart(2, '0')}`)
const phase = computed(() => {
  const order = props.order
  if (order.paid_at) return 'paid'
  if (order.status === 'EXPIRED' || (order.status === 'CREATED' && !order.deleted_at && remaining.value <= 0)) return 'expired'
  if (order.status !== 'CREATED' || order.deleted_at) return 'closed'
  return 'waiting'
})
const badge = computed(() => phase.value === 'paid' ? { cls: props.order.status, text: statusName(props.order.status) }
  : phase.value === 'expired' ? { cls: 'EXPIRED', text: statusName('EXPIRED') }
  : { cls: 'CLOSED', text: props.order.deleted_at ? t('已删除', 'Deleted') : statusName('CLOSED') })
const showsCode = computed(() => ['page', 'wap'].includes(props.order.product))
// what a client has to call to open the payment, per provider
const payload = computed(() => props.order.payment_payload || {})
const parameters = computed(() => payload.value.kind === 'wechat_app' ? JSON.stringify(payload.value.params, null, 2)
  : payload.value.kind === 'wechat_jsapi' ? `wx.requestPayment(${JSON.stringify(payload.value.params, null, 2)})`
  : props.order.product === 'app' ? payload.value.order_string || ''
  : `my.tradePay({\n  tradeNO: ${JSON.stringify(payload.value.trade_no || props.order.trade_no || '')}\n})`)
const parameterLabel = computed(() => ({ wechat_app: 'WeChat APP SDK', wechat_jsapi: 'wx.requestPayment' })[payload.value.kind] || (props.order.product === 'app' ? 'orderString' : 'my.tradePay'))
// the finished image arrives with the answer that says the code is ready, so no second request is needed to show it
const source = computed(() => inline.value ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(inline.value)}` : `/api/v1/payments/${encodeURIComponent(props.order.out_trade_no)}/qr.svg?v=${attempt.value}`)

async function prepare() {
  if (phase.value !== 'waiting' || preparing.value || props.order.product !== 'page' || props.order.channel !== 'alipay') return
  preparing.value = true; failed.value = false
  try {
    const result = await api(`/api/v1/payments/${props.order.out_trade_no}/prepare-qr`, { method: 'POST', body: '{}' })
    if (!disposed) { ready.value = Boolean(result.order?.cashier_qr); inline.value = result.qr_svg || ''; failed.value = !ready.value }
  } catch { if (!disposed) failed.value = true }
  finally { if (!disposed) preparing.value = false }
}
function retry() { attempt.value++; inline.value = ''; failed.value = false; if (!ready.value) prepare() }
async function copy() {
  try { await copyText(parameters.value); copied.value = true; clearTimeout(copyTimer); copyTimer = setTimeout(() => { copied.value = false }, 2000) } catch { /* the text stays selectable */ }
}
watch(() => phase.value === 'waiting', active => {
  clearInterval(tick)
  if (active) { now.value = Date.now(); tick = setInterval(() => { now.value = Date.now() }, 1000); if (!ready.value) prepare() } else zoom.value?.close()
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; clearInterval(tick); clearTimeout(copyTimer) })
</script>
<template>
  <section class="payment-qr-card" :class="[`qr-${order.channel}`, `is-${phase}`]" :aria-label="t('订单收款码', 'Payment code')">
    <header class="qr-receipt-header">
      <span class="qr-channel"><Brand :name="order.channel" /><b>{{ channelName }}</b></span>
      <strong class="qr-amount"><small>￥</small>{{ order.amount }}</strong>
      <span class="qr-header-end">
        <span v-if="phase === 'waiting'" class="qr-countdown" :class="{ urgent: remaining < 60 }" role="timer" :aria-label="t(`剩余 ${countdown}`, `${countdown} remaining`)"><Icon name="timer" :size="14" />{{ countdown }}</span>
        <span v-else class="state-badge" :class="badge.cls"><i></i>{{ badge.text }}</span>
      </span>
    </header>
    <div class="qr-body" :class="{ 'qr-body-code': phase === 'waiting' && !showsCode }">
      <template v-if="phase === 'waiting'">
        <div v-if="showsCode" class="qr-image-wrap">
          <button v-if="ready && !failed && !preparing" type="button" class="qr-expand" :aria-label="t('放大收款二维码', 'Enlarge the payment QR code')" @click="zoom.showModal()"><img :src="source" :alt="t(`${channelName}付款二维码`, `${channelName} payment QR code`)" width="230" height="230" @error="failed = true" /></button>
          <div v-else-if="preparing" class="qr-state" role="status"><Icon name="loader" class="spin" :size="28" /><span>{{ t('正在获取付款码', 'Preparing the payment code') }}</span></div>
          <div v-else class="qr-state" role="status"><Icon name="alert" :size="30" /><b>{{ t('付款码获取失败', 'Payment code unavailable') }}</b><button class="button button-secondary" type="button" @click="retry">{{ t('重新获取', 'Try again') }}</button></div>
        </div>
        <div v-else class="qr-code-block">
          <div class="qr-code-head"><code>{{ parameterLabel }}</code><button type="button" class="icon-button" :aria-label="t('复制支付参数', 'Copy payment parameters')" @click="copy"><Icon :name="copied ? 'check' : 'copy'" :size="15" /></button></div>
          <pre tabindex="0">{{ parameters }}</pre>
          <span class="sr-only" role="status">{{ copied ? t('已复制', 'Copied') : '' }}</span>
        </div>
      </template>
      <div v-else-if="phase === 'paid'" class="qr-state qr-confirmed" role="status">
        <span class="qr-confirmed-mark"><Icon name="check" :size="34" /></span>
        <b>{{ t('已确认支付', 'Payment confirmed') }}</b>
        <span>{{ t('支付时间', 'Paid at') }} {{ fullDate(order.paid_at) }}</span>
      </div>
      <div v-else class="qr-state" role="status"><Icon name="timer" :size="32" /><b>{{ phase === 'expired' ? t('订单已过期', 'Order expired') : order.deleted_at ? t('订单已删除', 'Order deleted') : t('订单已关闭', 'Order closed') }}</b></div>
    </div>
    <dialog v-if="showsCode" ref="zoom" class="qr-zoom-dialog" :aria-label="t('收款二维码', 'Payment QR code')">
      <div class="dialog-heading"><b>{{ channelName }} · ￥{{ order.amount }}</b><button type="button" class="icon-button" :aria-label="t('关闭二维码', 'Close QR code')" @click="zoom.close()"><Icon name="x" /></button></div>
      <img v-if="phase === 'waiting' && ready && !failed" :src="source" :alt="t(`${channelName}付款二维码`, `${channelName} payment QR code`)" />
    </dialog>
  </section>
</template>
