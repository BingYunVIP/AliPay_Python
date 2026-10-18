<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute } from 'vue-router'
import Brand from '../components/Brand.vue'
import Icon from '../components/Icon.vue'
const route = useRoute(), order = ref(null), error = ref(''), busy = ref(false), qrVersion = ref(0), qrSvg = ref(''), now = ref(Date.now())
// 收银台是独立页面：按浏览器语言显示中英文，不需要登录
const zh = computed(() => String(navigator.language || 'zh').toLowerCase().startsWith('zh'))
const t = (cn, en) => zh.value ? cn : en
const base = computed(() => `/api/cashier/${encodeURIComponent(route.params.token)}`)
const waiting = computed(() => order.value?.status === 'CREATED' && order.value.expires_at * 1000 > now.value)
// the finished image comes with the order when it is ready, so no second request is needed to show it
const qrSource = computed(() => qrSvg.value ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(qrSvg.value)}` : `${base.value}/qr.svg?v=${qrVersion.value}`)
const layout = computed(() => {
  const ratio = String(order.value?.layout?.cashier_ratio || '9:16').split(':').map(Number)
  const width = Number(order.value?.layout?.cashier_width) || 392
  return { '--cashier-w': `${width}px`, '--cashier-ratio': `${ratio[0]} / ${ratio[1]}`, '--cashier-height-factor': ratio[1] / ratio[0] }
})
const provider = computed(() => order.value?.channel === 'wechat' ? t('微信支付', 'WeChat Pay') : t('支付宝', 'Alipay'))
const scanName = computed(() => order.value?.channel === 'wechat' ? t('微信', 'WeChat') : t('支付宝', 'Alipay'))
let timer, posted = false, polling = false
async function request(path = '', payload) {
  const response = await fetch(base.value + path, payload === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
  const data = await response.json()
  if (!response.ok) throw new Error(({
    cashier_not_found: t('收银台不存在或已过期', 'This checkout does not exist or has expired'),
    cashier_qr_unavailable: t('二维码暂未获取，请重试', 'The QR code is not ready — try again'),
    payment_order_expired: t('订单已过期，请返回商户重新下单', 'This order expired — order again from the merchant'),
    rate_limited: t('刷新较频繁，请稍后重试', 'Too many refreshes — try again shortly'),
  })[data.error] || t('收银台暂不可用，请稍后重试', 'The checkout is unavailable — try again shortly'))
  return data
}
async function refresh(first = false) {
  if (polling) return
  polling = true
  try {
    order.value = await request(first ? '?qr=1' : '')
    keepImage()
    if (order.value.paid && !posted) {
      posted = true
      if (timer) window.clearInterval(timer)
      const requestedParent = route.query.embed_origin
      const parentOrigin = order.value.parent_origins?.includes(requestedParent) ? requestedParent : order.value.parent_origin
      if (window.parent !== window) window.parent.postMessage({type:'bypay:paid',trade_no:order.value.trade_no,out_trade_no:order.value.out_trade_no},parentOrigin)
    }
  } catch (e) { error.value = e.message }
  finally { polling = false }
}
function keepImage() {
  if (order.value?.qr_svg) qrSvg.value = order.value.qr_svg
  else if (!order.value?.qr_ready) qrSvg.value = ''
}
async function prepare() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { order.value = await request('/prepare',{refresh:Boolean(order.value?.qr_ready)}); keepImage(); qrVersion.value++ }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(async () => {
  await refresh(true)
  if (waiting.value && !order.value.qr_ready) await prepare()
  if (!order.value?.paid) timer = window.setInterval(() => { now.value = Date.now(); if (!document.hidden) refresh() }, 2500)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>
<template>
  <main class="pay-cashier" :style="layout">
    <header class="cashier-head">
      <div class="cashier-brand"><Brand name="bingyun"/><span>{{ t('冰云支付', 'BingYun Pay') }}</span></div>
      <a v-if="order?.paid && order.return_available" class="cashier-action" :href="`${base}/return`" target="_top">{{ t('返回站点', 'Back to site') }}<Icon name="arrow" :size="14"/></a>
      <button v-else-if="order" type="button" class="cashier-action" :disabled="busy" @click="prepare"><Icon name="refresh" :class="{ spin: busy }" :size="14"/>{{ t('刷新二维码', 'Refresh') }}</button>
    </header>
    <template v-if="order">
      <p class="cashier-subject">{{ order.subject }}</p>
      <p class="cashier-number">{{ order.out_trade_no }}</p>
      <p v-if="!order.paid && waiting" class="cashier-amount">¥ {{ order.amount }}</p>
      <div v-if="order.paid" class="cashier-done" role="status">
        <span class="cashier-check"><Icon name="check" :size="38"/></span>
        <h1>{{ t('支付成功', 'Payment successful') }}</h1>
        <p>{{ t('支付结果已由冰云支付核验', 'Verified by BingYun Pay') }}</p>
      </div>
      <template v-else-if="waiting">
        <div class="cashier-qr" :aria-busy="busy">
          <img v-if="order.qr_ready && !busy" :src="qrSource" :alt="t(`${scanName}付款二维码`, `${provider} payment QR code`)" width="228" height="228" />
          <div v-else class="cashier-placeholder"><Icon v-if="busy" name="loader" class="spin" :size="26"/><span>{{ busy ? t(`正在获取${provider}二维码`, `Loading the ${provider} QR code`) : t('二维码暂未就绪', 'QR code not ready') }}</span></div>
        </div>
        <p class="cashier-tip">{{ t(`打开${scanName}扫一扫`, `Scan with ${provider}`) }}</p>
      </template>
      <div v-else class="cashier-done">
        <h1>{{ order.status === 'EXPIRED' ? t('订单已过期', 'Order expired') : t('订单已关闭', 'Order closed') }}</h1>
        <p>{{ t('请返回商户重新下单', 'Return to the merchant and order again') }}</p>
      </div>
    </template>
    <p v-else-if="!error" class="cashier-tip">{{ t('正在加载收银台', 'Loading the checkout') }}</p>
    <p v-if="error" class="error-box" role="alert">{{ error }}</p>
  </main>
</template>
<style scoped>
/* 版心宽度与比例来自站点设置，内容较长时自然撑开，窄屏保持可读 */
.pay-cashier{--cashier-w:392px;box-sizing:border-box;width:100%;max-width:var(--cashier-w);aspect-ratio:var(--cashier-ratio,9 / 16);min-height:0;margin:0 auto;padding:20px 24px 30px;display:flex;flex-direction:column;color:var(--text,#f1f5fc);font-family:var(--font-body,inherit)}
.cashier-head{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:36px}
.cashier-brand{display:flex;align-items:center;gap:9px;font-size:14px;font-weight:500;letter-spacing:-.01em}
.cashier-brand :deep(svg),.cashier-brand :deep(img){width:24px;height:24px}
.cashier-action{display:inline-flex;align-items:center;gap:5px;min-height:32px;padding:0 12px;border:1px solid var(--console-border,rgba(255,255,255,.14));border-radius:999px;background:none;color:var(--muted,#9aa8bd);font-size:12px;text-decoration:none}
.cashier-action:hover{color:var(--text,#f1f5fc);border-color:color-mix(in srgb,var(--accent,#1677ff) 45%,transparent)}
.cashier-action:disabled{opacity:.6}
.cashier-subject{margin:34px 0 0;font-size:19px;line-height:1.45;font-weight:500;text-align:center;letter-spacing:-.01em;overflow-wrap:anywhere}
.cashier-number{margin:8px 0 0;color:var(--faint,#7d8ba1);font-size:12px;text-align:center;word-break:break-all;font-variant-numeric:tabular-nums}
.cashier-amount{margin:18px 0 0;font-size:32px;font-weight:600;text-align:center;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.cashier-qr{width:252px;max-width:100%;aspect-ratio:1;margin:26px auto 0;padding:12px;box-sizing:border-box;border-radius:18px;background:#fff;display:grid;place-items:center;box-shadow:0 10px 34px #0000002e}
.cashier-qr img{display:block;max-width:100%;height:auto}
.cashier-placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;color:#1b3a64;font-size:12px;text-align:center}
.cashier-tip{margin:16px 0 0;color:var(--faint,#7d8ba1);font-size:12px;text-align:center}
.cashier-done{display:flex;flex-direction:column;align-items:center;gap:10px;margin:56px 0 0;text-align:center}
.cashier-check{display:grid;place-items:center;width:76px;height:76px;border-radius:50%;background:color-mix(in srgb,#30b969 16%,transparent);color:#30b969}
.cashier-done h1{margin:4px 0 0;font-size:22px;font-weight:600;letter-spacing:-.02em}
.cashier-done p{margin:0;color:var(--faint,#7d8ba1);font-size:12px}
.pay-cashier .error-box{text-align:left;overflow-wrap:anywhere;margin-top:20px}
@media (max-width:460px){.pay-cashier{padding:18px 18px 26px}.cashier-subject{margin-top:26px;font-size:17px}.cashier-amount{font-size:28px}.cashier-qr{width:min(252px,78vw);margin-top:22px}}
</style>
