<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute } from 'vue-router'
import Brand from '../components/Brand.vue'
import Icon from '../components/Icon.vue'
const route = useRoute(), order = ref(null), error = ref(''), busy = ref(false), qrVersion = ref(0), now = ref(Date.now())
const base = computed(() => `/api/cashier/${encodeURIComponent(route.params.token)}`)
const waiting = computed(() => order.value?.status === 'CREATED' && order.value.expires_at * 1000 > now.value)
const provider = computed(() => order.value?.channel === 'wechat' ? '微信' : '支付宝')
let timer, posted = false, polling = false
async function request(path = '', payload) {
  const response = await fetch(base.value + path, payload === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
  const data = await response.json()
  if (!response.ok) throw new Error(({cashier_not_found:'收银台不存在或已过期',cashier_qr_unavailable:'二维码暂未获取，请重试',payment_order_expired:'订单已过期，请返回商户重新下单',rate_limited:'刷新较频繁，请稍后重试'})[data.error] || '收银台暂不可用，请稍后重试')
  return data
}
async function refresh() {
  if (polling) return
  polling = true
  try {
    order.value = await request()
    if (order.value.paid && !posted) {
      posted = true
      if (timer) window.clearInterval(timer)
      const requestedParent=route.query.embed_origin
      const parentOrigin=order.value.parent_origins?.includes(requestedParent) ? requestedParent : order.value.parent_origin
      if (window.parent !== window) window.parent.postMessage({type:'bypay:paid',trade_no:order.value.trade_no,out_trade_no:order.value.out_trade_no},parentOrigin)
    }
  } catch (e) { error.value = e.message }
  finally { polling = false }
}
async function prepare() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { order.value = await request('/prepare',{refresh:Boolean(order.value?.qr_ready)}); qrVersion.value++ }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(async () => {
  await refresh()
  if (waiting.value && !order.value.qr_ready) await prepare()
  if (!order.value?.paid) timer = window.setInterval(() => { now.value = Date.now(); if (!document.hidden) refresh() },2500)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>
<template>
  <main class="pay-cashier">
    <div class="cashier-brand"><Brand name="bingyun"/><span>冰云支付 <small>Pay</small></span></div>
    <template v-if="order">
      <div class="cashier-amount" :style="{ '--channel': order.channel === 'wechat' ? '#09a365' : '#1677ff' }"><Brand :name="order.channel"/><strong>￥{{ order.amount }}</strong></div>
      <h1>{{ order.subject }}</h1><p class="cashier-number">{{ order.out_trade_no }}</p>
      <div v-if="order.paid" class="cashier-result" role="status"><Icon name="check" :size="44"/><h2>支付成功</h2><p>支付结果已由冰云支付核验</p></div>
      <template v-else-if="waiting">
        <div class="cashier-qr" :aria-busy="busy">
          <img v-if="order.qr_ready && !busy" :src="`${base}/qr.svg?v=${qrVersion}`" :alt="`${provider}付款二维码`" width="230" height="230" />
          <div v-else class="cashier-placeholder"><Icon v-if="busy" name="loader" class="spin" :size="28"/><span>{{ busy ? `正在获取${provider}二维码` : '二维码暂未就绪' }}</span></div>
        </div>
        <p>打开{{ provider }}扫一扫</p>
        <button class="button button-secondary" :disabled="busy" @click="prepare">{{ busy ? '正在获取' : order.qr_ready ? '刷新二维码' : '重新获取二维码' }}</button>
      </template>
      <div v-else class="cashier-result"><h2>{{ order.status==='EXPIRED' ? '订单已过期' : '订单已关闭' }}</h2><p>请返回商户重新下单</p></div>
      <a v-if="order.paid && order.return_available" class="button" :href="`${base}/return`" target="_top">返回商户</a>
    </template>
    <p v-else-if="!error">正在加载收银台</p>
    <p v-if="error" class="error-box" role="alert">{{ error }}</p>
  </main>
</template>
<style scoped>
.pay-cashier{font-family:var(--font-body,inherit);box-sizing:border-box;max-width:430px;min-height:480px;margin:0 auto;padding:26px 24px;text-align:center;color:var(--text,#f1f5fc)}
.cashier-brand{display:flex;align-items:center;justify-content:center;gap:10px;font-size:15px}.cashier-brand :deep(svg),.cashier-brand :deep(img){width:26px;height:26px}.cashier-brand small{color:#8c9bb0;margin-left:7px}.cashier-amount{display:flex;align-items:center;justify-content:center;gap:10px;margin:24px 0 10px}.cashier-amount :deep(svg),.cashier-amount :deep(img){width:28px;height:28px}.cashier-amount :deep(svg){color:var(--channel,#1677ff)}.cashier-amount strong{font-size:34px;letter-spacing:-1px}.pay-cashier h1{font-size:16px;line-height:1.5;margin:0;overflow-wrap:anywhere}.cashier-number{font-size:12px;color:#97a7bd;word-break:break-all;margin:6px 0 18px}.cashier-qr{margin:0 auto;width:254px;max-width:100%;aspect-ratio:1;background:white;padding:12px;box-sizing:border-box;border-radius:16px;display:grid;place-items:center}.cashier-qr img{max-width:100%;height:auto}.cashier-placeholder{color:#1b3a64;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:16px;font-size:13px}.pay-cashier>p{font-size:13px;line-height:1.7}.pay-cashier .button{margin:12px auto 0;justify-content:center}.cashier-result{margin:32px auto;color:#61d7a5}.cashier-result h2{font-size:23px}.cashier-result p{font-size:13px}.pay-cashier .error-box{text-align:left;overflow-wrap:anywhere}
</style>
