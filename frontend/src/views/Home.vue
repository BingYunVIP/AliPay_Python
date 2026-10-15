<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { auth, copyText, t } from '../state'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import FlowWire from '../components/FlowWire.vue'

const copied = ref(false)
const copyFailed = ref(false)
const paused = ref(false)
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
  { text: `curl -X POST '${origin}/api/v1/payments' \\`, type: 'command' },
  { text: '  -H "Authorization: Bearer $BYPAY_API_KEY" \\', type: 'header' },
  { text: '  -H "Content-Type: application/json" \\', type: 'header' },
  { text: "  -d '{", type: 'body' },
  { text: '    "out_trade_no": "PAY_DEMO_20261015_001",', type: 'body' },
  { text: `    "subject": "${t('冰云支付测试订单', 'BingYun Pay test order')}",`, type: 'body' },
  { text: '    "amount": "0.01",', type: 'body' },
  { text: '    "channel": "alipay",', type: 'body' },
  { text: `    "product": "${product.value}"`, type: 'body' },
  { text: "  }'", type: 'body' },
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
  <section class="byh-hero byh-wrap">
    <div class="hero-kicker"><span class="status-dot"></span> BINGYUN PAY <span class="kicker-separator">/</span> {{ t('为每一笔生意而来', 'BUILT FOR EVERY TRANSACTION') }}</div>
    <h1 class="byh-h1">{{ t('一个接口，', 'One integration. ') }}<span>{{ t('连接每一笔支付', 'Every payment.') }}</span></h1>
    <p class="hero-description">{{ t('从网页到小程序，让收款更简单，让生意少一点等待。', 'From websites to mini programs. Simpler payments, less waiting.') }}</p>
    <ul class="byh-eyebrow">
      <li class="highlight"><Icon name="card" :size="16" /><span>{{ t('仅 1.5% 手续费', 'Just 1.5% per transaction') }}</span></li>
      <li><Icon name="timer" :size="16" /><span>{{ t('T+1 到账', 'T+1 settlement') }}</span></li>
      <li><Icon name="zap" :size="16" /><span>{{ t('秒级对账', 'Reconcile in seconds') }}</span></li>
      <li><Icon name="code" :size="16" /><span>{{ t('一次接入，多端收款', 'One API, every platform') }}</span></li>
    </ul>
  </section>

  <section class="byh-flow-sec byh-wrap" :aria-label="t('统一支付接入示意', 'Unified payment integration')">
    <div class="byh-flow">
      <div class="byh-col byh-col-in">
        <h2 class="byh-col-h"><span>01</span>{{ t('产品名称', 'PAYMENT PRODUCTS') }}</h2>
        <div class="byh-list">
          <div v-for="item in products" :key="item.key" class="byh-node product-node"><span class="product-icon"><Icon :name="item.icon" :size="23" /></span><span class="node-text"><b>{{ t(item.zh, item.en) }}</b><small>{{ item.detail }}</small></span><Icon name="chevron" :size="14" class="node-arrow" /></div>
        </div>
      </div>
      <FlowWire :paused="paused" />
      <div class="terminal-col">
        <h2 class="byh-col-h center-col-title"><span>02</span>{{ t('统一支付接口', 'ONE PAYMENT API') }}<span class="api-version">REST API</span></h2>
        <div class="byh-term">
          <div class="byh-bar"><span class="byh-lights" aria-hidden="true"><i></i><i></i><i></i></span><span class="byh-title"><Icon name="terminal" :size="14" /> create-payment.sh</span><button class="terminal-copy" @click="copy" :aria-label="t('复制 curl 代码', 'Copy curl command')" :title="t('复制代码', 'Copy code')"><Icon :name="copied ? 'check' : 'copy'" :size="15" /></button></div>
          <div class="code-tabs" :aria-label="t('示例支付方式', 'Example payment product')"><button :class="{ active: product === 'page' }" :aria-pressed="product === 'page'" @click="product = 'page'">{{ t('网页支付', 'Web') }}</button><button :class="{ active: product === 'wap' }" :aria-pressed="product === 'wap'" @click="product = 'wap'">{{ t('手机支付', 'Mobile') }}</button><span>cURL</span></div>
          <div class="byh-code" tabindex="0" :aria-label="t('创建订单的 curl 示例，只展示不执行', 'Create payment curl example. Display only.')"><pre><code><span v-for="(line, i) in code" :key="i" class="code-line" :class="line.type"><span class="line-number" aria-hidden="true">{{ i + 1 }}</span>{{ line.text }}
</span></code></pre></div>
          <div class="terminal-status"><span><span class="status-dot"></span>{{ t('示例代码 · 不会自动请求', 'Example only · never auto-executed') }}</span><span aria-live="polite">{{ copied ? t('已复制', 'Copied') : copyFailed ? t('请选中代码复制', 'Select code to copy') : 'UTF-8' }}</span></div>
        </div>
      </div>
      <FlowWire outgoing :paused="paused" />
      <div class="byh-col byh-col-out">
        <h2 class="byh-col-h"><span>03</span>{{ t('收款渠道', 'PAYMENT CHANNELS') }}</h2>
        <div class="byh-list"><div v-for="channel in channels" :key="channel.key" class="byh-node channel-node"><span class="channel-icon" :class="channel.key" :style="{ color: channel.color }"><Brand :name="channel.key" /></span><span class="node-text"><b>{{ t(channel.zh, channel.en) }}</b><small>{{ channel.detail }}</small></span><span v-if="channel.key === 'alipay'" class="channel-ready" :title="t('当前可测试', 'Available for testing')"></span></div></div>
      </div>
    </div>
    <div class="flow-caption"><span><Icon name="shield" :size="15" />{{ t('当前开放支付宝网页 / 手机支付测试，其他产品与渠道陆续接入。', 'Alipay web and mobile checkout are ready for testing. More products and channels to follow.') }}</span><button class="subtle-button motion-toggle" @click="paused = !paused" :aria-pressed="paused"><Icon :name="paused ? 'play' : 'pause'" :size="13" />{{ paused ? t('播放动效', 'Play motion') : t('暂停动效', 'Pause motion') }}</button></div>
    <div class="hero-actions"><RouterLink class="button" :to="auth.user ? '/dashboard' : '/register'">{{ t('开始接入', 'Start integrating') }}<Icon name="arrow" :size="17" /></RouterLink><a class="button button-secondary" href="https://docs.bingyun.vip/zh/" target="_blank" rel="noopener noreferrer"><Icon name="book" :size="17" />{{ t('阅读文档', 'Read the docs') }}</a></div>
  </section>

  <section class="value-strip byh-wrap" :aria-label="t('支付体验', 'Built for your business')">
    <article><div class="value-icon"><Icon name="network" :size="21" /></div><div><h3>{{ t('多端业务，一个入口', 'Every platform, one integration') }}</h3><p>{{ t('统一接口设计，轻松衔接你的每一种业务场景。', 'A consistent API designed to fit every part of your business.') }}</p></div></article>
    <article><div class="value-icon"><Icon name="shield" :size="21" /></div><div><h3>{{ t('每笔交易，有迹可循', 'Every transaction, accounted for') }}</h3><p>{{ t('从创建订单到支付回调，状态与日志清晰可查。', 'Follow each order from creation to verified payment notifications.') }}</p></div></article>
    <article><div class="value-icon"><Icon name="code" :size="21" /></div><div><h3>{{ t('为开发者，少走一步', 'Built with developers in mind') }}</h3><p>{{ t('独立 API 密钥，让收款成为业务的简单一环。', 'Individual API keys make payments a simple part of your workflow.') }}</p></div></article>
  </section>
</template>
