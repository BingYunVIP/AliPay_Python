<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, t } from '../state'
import Brand from '../components/Brand.vue'
import Icon from '../components/Icon.vue'
const props = defineProps({ order: { type: Object, required: true } })
const dialog = ref(null), now = ref(Date.now()), failed = ref(false), preparing = ref(false), prepared = ref(false)
const qrVersion = ref(0), nativeUrl = ref('')
let timer
const remaining = computed(() => Math.max(0, Math.ceil((props.order.expires_at || 0) - now.value / 1000)))
const active = computed(() => !props.order.deleted_at && props.order.status === 'CREATED' && remaining.value > 0)
const countdown = computed(() => `${Math.floor(remaining.value / 60)}:${String(remaining.value % 60).padStart(2, '0')}`)
const source = computed(() => `/api/v1/payments/${encodeURIComponent(props.order.out_trade_no)}/qr.svg?v=${prepared.value || props.order.cashier_qr ? 'cashier' : 'link'}-${qrVersion.value}`)
const channelName = computed(() => ({ alipay: t('支付宝', 'Alipay'), wechat: t('微信支付', 'WeChat Pay'), paypal: 'PayPal', stripe: 'Stripe' })[props.order.channel])
watch(() => props.order.out_trade_no, () => { failed.value = false; dialog.value?.close() })
watch(active, value => { if (!value) dialog.value?.close() })
async function prepare(refresh = false) {
  if (!active.value || props.order.product !== 'page' || (nativeUrl.value && !refresh)) return
  preparing.value = true; failed.value = false
  try { const data = await api(`/api/v1/payments/${props.order.out_trade_no}/prepare-qr`, { method: 'POST', body: JSON.stringify({ refresh, native: true }) }); nativeUrl.value = data.cashier_url || ''; prepared.value = true; qrVersion.value++ }
  catch { failed.value = true }
  finally { preparing.value = false }
}
onMounted(() => { timer = setInterval(() => { now.value = Date.now() }, 1000); prepare() })
onBeforeUnmount(() => clearInterval(timer))
</script>
<template>
  <section class="payment-qr-card" :class="`qr-${order.channel}`" :aria-label="t('订单收款码', 'Payment QR code')">
    <div class="qr-receipt-top"><span class="qr-channel"><Brand :name="order.channel" /><b>{{ channelName }}</b></span><strong><small>¥</small>{{ order.amount }}</strong></div>
    <div class="qr-receipt-meta"><div><span>{{ t('商品名称', 'Item') }}</span><b>{{ order.subject }}</b></div><div><span>{{ t('订单号', 'Order') }}</span><code>{{ order.out_trade_no }}</code></div></div>
    <div class="qr-image-wrap">
      <iframe v-if="active && nativeUrl && !preparing" class="native-alipay-qr" :src="nativeUrl" :key="qrVersion" :title="t('支付宝原生收款二维码', 'Alipay checkout QR')" referrerpolicy="no-referrer"></iframe><button v-else-if="active && !failed && !preparing" type="button" class="qr-expand" :aria-label="t('放大收款二维码', 'Enlarge payment QR')" @click="dialog.showModal()"><img :src="source" :alt="t('使用支付宝扫一扫付款', 'Scan with Alipay to pay')" @error="failed = true" /></button>
      <div v-else-if="preparing" class="qr-state" role="status"><Icon name="loader" class="spin" :size="28" /><span>{{ t('正在获取支付宝二维码', 'Loading Alipay QR') }}</span></div><div v-else class="qr-state" role="status"><Icon :name="order.status === 'PAID' ? 'success' : 'timer'" :size="38" /><b>{{ order.status === 'PAID' ? t('支付成功', 'Payment successful') : ['REFUNDING', 'PARTIALLY_REFUNDED', 'REFUNDED'].includes(order.status) ? t('订单已有退款记录', 'Refund activity recorded') : order.status === 'CLOSED' ? t('订单已关闭', 'Order closed') : failed ? t('获取二维码失败', 'QR unavailable') : t('二维码已过期', 'QR expired') }}</b><span>{{ order.status === 'PAID' ? t('已核验到账', 'Payment verified') : t('请查看订单详情确认当前状态', 'Check order details for the current status') }}</span><button v-if="failed && active" type="button" class="button button-secondary" @click="prepare(true)">{{ t('重新获取', 'Retry') }}</button></div>
    </div>
    <div v-if="active && !failed && !preparing" class="qr-receipt-foot"><span>{{ nativeUrl ? t('支付宝原生二维码', 'Alipay original QR') : t('支付宝扫一扫', 'Scan with Alipay') }}</span><span><Icon name="timer" :size="13" />{{ countdown }}</span></div>
    <button v-if="active && order.product === 'page'" type="button" class="qr-refresh-link text-link" :disabled="preparing" @click="prepare(true)">{{ t('二维码失效时点此刷新', 'Refresh an expired QR') }}</button>
    <dialog ref="dialog" class="qr-zoom-dialog" :aria-label="t('收款二维码', 'Payment QR code')"><div class="dialog-heading"><b>{{ channelName }} · ¥ {{ order.amount }}</b><button type="button" class="icon-button" :aria-label="t('关闭二维码', 'Close QR')" @click="dialog.close()"><Icon name="x" /></button></div><img v-if="active && !failed && !preparing" :src="source" :alt="t('支付宝收款二维码', 'Alipay payment QR')" /><p>{{ order.subject }}</p><code>{{ order.out_trade_no }}</code></dialog>
  </section>
</template>
