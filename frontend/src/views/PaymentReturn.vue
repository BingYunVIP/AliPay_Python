<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, auth, errorText, t } from '../state'
import Icon from '../components/Icon.vue'

const route = useRoute()
const token = new URLSearchParams(route.hash.slice(1)).get('state') || ''
const order = ref(null), busy = ref(false), error = ref(''), paused = ref(false)
const provider = computed(() => order.value?.channel === 'wechat' ? t('微信支付', 'WeChat Pay') : t('支付宝', 'Alipay'))
const paid = computed(() => ['PAID','PARTIALLY_REFUNDED'].includes(order.value?.status))
const closed = computed(() => ['CLOSED','EXPIRED','REFUNDED'].includes(order.value?.status))
const title = computed(() => order.value?.status === 'REFUNDED' ? t('订单已退款', 'Payment refunded') : order.value?.status === 'REFUNDING' ? t('退款正在确认中', 'Confirming your refund') : paid.value ? t('支付成功', 'Payment successful')
  : closed.value ? t('订单已关闭', 'Order closed')
  : !token ? t('查看支付结果', 'Check your payment')
  : error.value ? t('暂时无法确认支付', 'Unable to confirm yet')
  : paused.value ? t('支付仍在确认中', 'Still confirming payment')
  : t('正在确认支付', 'Confirming your payment'))
const description = computed(() => order.value?.status === 'REFUNDED' ? t(`退款结果已由${provider.value}确认`, `${provider.value} has confirmed the refund`) : order.value?.status === 'REFUNDING' ? t('可在订单详情中查看退款进度', 'Check order details for refund progress') : paid.value ? t(`已核验${provider.value}支付结果`, `Your payment has been verified with ${provider.value}`)
  : closed.value ? t(`${provider.value}已关闭这笔交易`, `${provider.value} has closed this transaction`)
  : !token ? t('此页面缺少订单信息，请前往订单列表查看', 'Order details are missing from this link — check your orders')
  : error.value || (paused.value ? t('后台会继续核验，你可以稍后查看订单', 'Verification continues in the background — check your order later')
    : t(`正在向${provider.value}核对订单，请勿重复付款`, `Checking your order with ${provider.value} — please do not pay again`)))
let timer, disposed = false, deadline = 0, controller

async function check(restart = false) {
  if (!token || busy.value || disposed) return
  if (restart) { deadline = Date.now() + 120000; paused.value = false }
  clearTimeout(timer)
  busy.value = true
  controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 20000)
  try {
    const result = await api('/api/payment-result', { method: 'POST', body: JSON.stringify({ state: token }), signal: controller.signal })
    if (disposed) return
    order.value = result.order
    error.value = result.verification.error ? errorText(new Error(result.verification.error)) : ''
  } catch (e) {
    if (disposed) return
    error.value = errorText(e)
    if (e.status === 404) deadline = 0
  } finally {
    clearTimeout(timeout)
    busy.value = false
    if (!disposed && !paid.value && !closed.value) {
      if (Date.now() < deadline) timer = setTimeout(() => check(), 5000)
      else paused.value = true
    }
  }
}
onMounted(() => check(true))
onBeforeUnmount(() => { disposed = true; clearTimeout(timer); controller?.abort() })
</script>

<template>
  <section class="return-shell byh-wrap">
    <div class="panel return-card" :aria-busy="busy">
      <div class="return-icon" :class="{ confirmed: paid, uncertain: error || closed }" aria-hidden="true">
        <Icon :name="paid ? 'success' : closed || error ? 'help' : token && !paused ? 'loader' : 'receipt'" :class="{ spin: token && !paid && !closed && !error && !paused }" :size="32" />
      </div>
      <div aria-live="polite" aria-atomic="true"><h1>{{ title }}</h1><p>{{ description }}</p></div>
      <dl v-if="order" class="return-details">
        <div><dt>{{ t('金额', 'Amount') }}</dt><dd class="return-amount">¥ {{ order.amount }}</dd></div>
        <div><dt>{{ t('商品', 'Item') }}</dt><dd>{{ order.subject }}</dd></div>
        <div><dt>{{ t('订单号', 'Order') }}</dt><dd class="return-number">{{ order.out_trade_no }}</dd></div>
      </dl>
      <div class="return-actions">
        <button v-if="token && !paid && !closed" class="button" :disabled="busy" @click="check(true)">{{ busy ? t('正在核验', 'Verifying') : t('重新查询', 'Check again') }}</button>
        <RouterLink class="button" :class="{ 'button-secondary': token && !paid && !closed }" :to="`/${auth.user?.role||'user'}/orders`">{{ t('查看订单', 'View orders') }}<Icon name="arrow" :size="17" /></RouterLink>
      </div>
    </div>
  </section>
</template>
