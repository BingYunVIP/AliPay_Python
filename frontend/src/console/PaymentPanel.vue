<script setup>
import { useRoute, useRouter } from 'vue-router'
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, copyText, errorText, t } from '../state'
import { status } from './format'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import PaymentCard from './PaymentCard.vue'
const c = inject('console'), route = useRoute(), router = useRouter()
const configId = ref(Number(route.query.config) || null)
const configurations = computed(() => (c.caps.value?.payment_configs || []).filter(ch => ch.id === 'alipay' && ch.enabled && ch.configured))
watch(configurations, list => { if (!list.some(ch => ch.config_id === configId.value)) configId.value = list[0]?.config_id || null }, { immediate: true })
const selectedConfig = computed(() => configurations.value.find(ch => ch.config_id === configId.value))
const product = ref('page'), amount = ref('0.01'), subject = ref(t('冰云支付测试订单', 'BingYun Pay test order'))
const buyerType = ref('buyer_open_id'), buyerIdentity = ref(''), draftNumber = ref('')
const busy = ref(false), error = ref(''), result = ref(null), terminalTab = ref('request'), copied = ref(false)
let disposed = false, copyTimer, poll
const products = [{ id: 'page', zh: '网页支付', en: 'Web', icon: 'monitor' }, { id: 'wap', zh: '手机支付', en: 'Mobile', icon: 'phone' }, { id: 'app', zh: 'APP 支付', en: 'App', icon: 'app' }, { id: 'mini', zh: '小程序支付', en: 'Mini program', icon: 'grid' }]
const requestBody = computed(() => ({ out_trade_no: draftNumber.value || result.value?.out_trade_no || 'PAY_TEST_ORDER', subject: subject.value, amount: amount.value, channel: 'alipay', config_id: configId.value, product: product.value, ...(product.value === 'mini' ? { [buyerType.value]: buyerIdentity.value.trim() } : {}) }))
watch([product, amount, subject, buyerType, buyerIdentity, configId], () => { draftNumber.value = ''; error.value = '' })
const responseBody = computed(() => result.value ? { out_trade_no: result.value.out_trade_no, amount: result.value.amount, status: result.value.status, channel: result.value.channel, product: result.value.product } : {})
const hasPayment = computed(() => Boolean(result.value?.pay_url || result.value?.payment_payload?.kind))

// The payment card switches to "confirmed" as soon as the verified status arrives
async function refreshOrder() {
  const current = result.value
  if (!current) return
  try {
    const data = await api(`/api/v1/payments/${encodeURIComponent(current.out_trade_no)}`)
    if (!disposed && result.value?.out_trade_no === current.out_trade_no) result.value = data.order
  } catch { /* the console's own refresh reports connection problems */ }
}
watch(c.revision, refreshOrder)
onMounted(async () => {
  poll = setInterval(() => { if (!document.hidden && result.value?.status === 'CREATED') refreshOrder() }, 3000)
  if (typeof route.query.order !== 'string') return
  try {
    const data = await api(`/api/v1/payments/${encodeURIComponent(route.query.order)}`)
    if (disposed) return
    result.value = data.order; terminalTab.value = 'response'; product.value = data.order.product; amount.value = data.order.amount; subject.value = data.order.subject; configId.value = data.order.payment_config_id
  } catch (e) { if (!disposed) error.value = errorText(e) }
})
async function create() {
  busy.value = true; error.value = ''
  try {
    if (!draftNumber.value) draftNumber.value = `PAY_${Date.now()}_${crypto.randomUUID().slice(0, 8)}`
    const data = await api('/api/v1/payments', { method: 'POST', body: JSON.stringify({ ...requestBody.value, out_trade_no: draftNumber.value }) })
    result.value = data.order; terminalTab.value = 'response'
    await router.replace({ query: { ...route.query, order: data.order.out_trade_no } })
    draftNumber.value = ''
    c.refresh(true)
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
async function copy() {
  try { await copyText(JSON.stringify(terminalTab.value === 'request' ? requestBody.value : responseBody.value, null, 2)); copied.value = true; clearTimeout(copyTimer); copyTimer = setTimeout(() => { copied.value = false }, 2000) }
  catch { c.toast(t('请选中报文手动复制', 'Select the payload to copy')) }
}
function checkout() { api(`/api/v1/payments/${result.value.out_trade_no}/checkout-opened`, { method: 'POST' }).catch(() => {}) }
onBeforeUnmount(() => { disposed = true; clearInterval(poll); clearTimeout(copyTimer) })
</script>
<template>
  <div class="console-page-heading"><h2>{{ t('测试支付', 'Test a payment') }}</h2><span class="state-badge" :class="selectedConfig?.environment === 'production' ? 'production' : 'neutral'">{{ selectedConfig?.environment === 'production' ? t('正式环境', 'Production') : t('沙箱环境', 'Sandbox') }}</span></div>
  <div class="payment-workbench">
    <form class="hig-card payment-form" @submit.prevent="create">
      <div class="card-heading"><h3>{{ t('创建测试订单', 'Create a test order') }}</h3><Icon name="test" :size="20" /></div>
      <fieldset>
        <legend>{{ t('产品形态', 'Payment product') }}</legend>
        <div class="product-choices"><button v-for="item in products" :key="item.id" type="button" :disabled="item.disabled || busy" :aria-pressed="product === item.id" :class="{ selected: product === item.id }" @click="product = item.id"><Icon :name="item.icon" :size="19" /><span>{{ t(item.zh, item.en) }}</span><Icon v-if="product === item.id" name="check" :size="13" /></button></div>
      </fieldset>
      <div v-if="product === 'mini'" class="mini-buyer-fields">
        <label for="buyer-type">{{ t('付款用户标识类型', 'Payer identity type') }}</label>
        <select id="buyer-type" v-model="buyerType" :disabled="busy"><option value="buyer_open_id">OpenID</option><option value="buyer_id">{{ t('支付宝 UID', 'Alipay UID') }}</option></select>
        <label for="buyer-identity">{{ buyerType === 'buyer_open_id' ? 'OpenID' : t('付款用户支付宝 UID', 'Payer Alipay UID') }}</label>
        <input id="buyer-identity" v-model="buyerIdentity" required maxlength="128" :disabled="busy" autocomplete="off" />
        <p class="field-hint">{{ t('使用当前支付宝应用获取的付款用户标识', 'Use the payer identifier obtained through this Alipay app') }}</p>
      </div>
      <label>{{ t('收款渠道', 'Payment channel') }}</label>
      <div class="payment-config-select"><Brand :name="selectedConfig?.id || 'alipay'" /><select v-model="configId" :disabled="busy" :aria-label="t('选择收款配置', 'Select payment configuration')"><option v-for="ch in configurations" :key="ch.config_id" :value="ch.config_id">{{ ch.name }}</option></select></div>
      <div class="form-grid">
        <div><label for="test-amount">{{ t('金额（CNY）', 'Amount (CNY)') }}</label><div class="currency-input"><span>¥</span><input id="test-amount" v-model="amount" inputmode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" required :disabled="busy" /></div></div>
        <div><label for="test-subject">{{ t('商品名称', 'Item name') }}</label><input id="test-subject" v-model="subject" required maxlength="128" :disabled="busy" /></div>
      </div>
      <p class="field-hint">{{ t('单笔测试上限', 'Test limit per order') }} ¥ {{ c.caps.value?.max_amount || '—' }}</p>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <button class="button full-width" :disabled="busy"><Icon :name="busy ? 'loader' : 'plus'" :class="{ spin: busy }" :size="17" />{{ busy ? t('正在创建…', 'Creating…') : t('创建支付订单', 'Create payment order') }}</button>
      <div v-if="result" class="created-payment">
        <div><Icon name="success" :size="18" /><b>{{ t('订单已创建', 'Order created') }}</b><span class="state-badge" :class="result.status">{{ status(result.status) }}</span></div>
        <code>{{ result.out_trade_no }}</code>
        <a v-if="result.pay_url && result.status === 'CREATED'" class="button full-width" :href="result.pay_url" target="_blank" rel="noopener noreferrer" @click="checkout">{{ t('打开支付宝收银台', 'Open Alipay checkout') }}<Icon name="external" :size="16" /></a>
        <button type="button" class="text-link" @click="c.selectedOrder.value = result">{{ t('查看订单详情与查询状态', 'View order details and query status') }}<Icon name="chevron" :size="14" /></button>
      </div>
    </form>
    <div class="payment-monitor">
      <PaymentCard v-if="hasPayment" :key="result.out_trade_no" :order="result" />
      <section class="hig-card gateway-terminal">
        <div class="console-terminal-bar"><span class="byh-lights" aria-hidden="true"><i></i><i></i><i></i></span><span><Icon name="terminal" :size="15" /> Pay API</span><button class="icon-button" @click="copy" :aria-label="t('复制报文', 'Copy payload')"><Icon :name="copied ? 'check' : 'copy'" :size="15" /></button></div>
        <div class="terminal-tab-row"><button :class="{ active: terminalTab === 'request' }" @click="terminalTab = 'request'">{{ t('请求', 'Request') }}</button><button :class="{ active: terminalTab === 'response' }" :disabled="!result" @click="terminalTab = 'response'">{{ t('返回', 'Response') }}</button><code>POST /api/v1/payments</code></div>
        <pre tabindex="0">{{ JSON.stringify(terminalTab === 'request' ? requestBody : responseBody, null, 2) }}</pre>
      </section>
    </div>
  </div>
</template>
