<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { copyText, t } from '../state'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import FlowWire from '../components/FlowWire.vue'
import { usePaymentFlow } from '../composables/usePaymentFlow'

const copied = ref(false)
const copyFailed = ref(false)
const motionRoot = ref(null)
usePaymentFlow(motionRoot)
const product = ref('page')
let copyTimer
const origin = window.location.origin
const products = [
  { key: 'page', zh: '网页支付', en: 'Web payment', detail: 'Web checkout', icon: 'monitor' },
  { key: 'wap', zh: '手机支付', en: 'Mobile payment', detail: 'Mobile checkout', icon: 'phone' },
  { key: 'app', zh: 'APP 支付', en: 'App payment', detail: 'Native apps', icon: 'app' },
  { key: 'mini', zh: '小程序支付', en: 'Mini program', detail: 'Mini programs', icon: 'grid' },
]
const channels = [
  { key: 'alipay', zh: '支付宝', en: 'Alipay', detail: 'Alipay', color: '#1677ff' },
  { key: 'wechat', zh: '微信支付', en: 'WeChat Pay', detail: 'WeChat Pay', color: '#21bf69' },
  { key: 'paypal', zh: 'PayPal', en: 'PayPal', detail: 'Global payments', color: '#4a9af7' },
  { key: 'stripe', zh: 'Stripe', en: 'Stripe', detail: 'Global payments', color: '#9c8aff' },
]
const code = computed(() => [
  { text: 'PID="1000"; KEY="$MERCHANT_MD5_KEY"', type: 'header' },
  { text: 'N="demo_$(date +%s)"', type: 'header' },
  { text: 'URL="https://test.bingyun.vip/notify"', type: 'header' },
  { text: 'DATA="clientip=127.0.0.1&money=0.01&name=ByPay&notify_url=${URL}&out_trade_no=${N}&pid=${PID}&type=alipay"', type: 'body' },
  { text: 'SIGN=$(printf "%s" "${DATA}${KEY}" | openssl dgst -md5 -r | cut -d " " -f1)', type: 'body' },
  { text: `curl '${origin}/mapi.php' \\`, type: 'command' },
  { text: '  --data "${DATA}&sign_type=MD5&sign=${SIGN}"', type: 'body' },
])
async function copy() {
  try { await copyText(code.value.map(l => l.text).join('\n')); copied.value = true; copyFailed.value = false }
  catch { copyFailed.value = true }
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => { copied.value = false }, 2400)
}
onBeforeUnmount(() => clearTimeout(copyTimer))
</script>
<template>
  <div ref="motionRoot" class="pay-home" data-home-motion>
    <section class="byh-hero byh-wrap">
      <h1 class="byh-h1"><span>{{ t('一个接口，', 'One integration. ') }}</span><span>{{ t('连接每一笔支付', 'Every payment.') }}</span></h1>
      <ul class="byh-eyebrow">
        <li><Icon name="card" :size="16" /><span>{{ t('仅 1.5% 手续费', 'Just 1.5% per transaction') }}</span></li>
        <li><Icon name="timer" :size="16" /><span>{{ t('T+1 到账', 'T+1 settlement') }}</span></li>
        <li><Icon name="zap" :size="16" /><span>{{ t('秒级对账', 'Reconcile in seconds') }}</span></li>
        <li><Icon name="code" :size="16" /><span>{{ t('一次接入，多端收款', 'One API, every platform') }}</span></li>
        <li><Icon name="shield" :size="16" /><span>{{ t('独立密钥', 'Individual keys') }}</span></li>
        <li><Icon name="network" :size="16" /><span>{{ t('统一接口', 'Unified API') }}</span></li>
      </ul>
    </section>
    <section class="byh-flow-sec byh-wrap" :aria-label="t('统一支付接入示意', 'Unified payment integration')">
      <div class="byh-flow">
        <div class="byh-col byh-col-in">
          <h2 class="byh-col-h">{{ t('产品名称', 'Payment products') }}</h2>
          <div class="byh-list">
            <div v-for="item in products" :key="item.key" class="byh-node"><Icon :name="item.icon" :size="29" /><b>{{ t(item.zh, item.en) }}</b></div>
          </div>
        </div>
        <FlowWire />
        <div class="byh-term">
          <div class="byh-bar">
            <span class="byh-lights" aria-hidden="true"><i></i><i></i><i></i></span>
            <span class="byh-title"><Icon name="terminal" :size="16" />Terminal</span>
            <div class="terminal-tools">
              <button class="terminal-copy" @click="copy" :aria-label="t('复制 curl 代码', 'Copy curl command')" :title="t('复制代码', 'Copy code')"><Icon :name="copied ? 'check' : 'copy'" :size="14" /></button>
            </div>
          </div>
          <div class="byh-code" tabindex="0" :aria-label="t('创建订单的 curl 示例，只展示不执行', 'Create payment curl example. Display only.')"><pre><code><span v-for="(line, i) in code" :key="i" class="code-line" :class="line.type">{{ line.text }}
</span></code></pre></div>
          <span class="sr-only" aria-live="polite">{{ copied ? t('代码已复制', 'Code copied') : copyFailed ? t('请选中代码手动复制', 'Select code to copy') : '' }}</span>
        </div>
        <FlowWire outgoing />
        <div class="byh-col byh-col-out">
          <h2 class="byh-col-h">{{ t('收款渠道', 'Payment channels') }}</h2>
          <div class="byh-list"><div v-for="channel in channels" :key="channel.key" class="byh-node"><span class="channel-icon" :class="channel.key" :style="{ color: channel.color }"><Brand :name="channel.key" /></span><b>{{ t(channel.zh, channel.en) }}</b></div></div>
        </div>
      </div>
    </section>
  </div>
</template>
