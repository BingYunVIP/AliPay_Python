<script setup>
import { computed, ref } from 'vue'
import { copyText, t } from '../state'
import Brand from '../components/Brand.vue'
import Icon from '../components/Icon.vue'
const props = defineProps({ order: { type: Object, required: true } })
const message = ref('')
const content = computed(() => props.order.payment_payload?.kind === 'app' ? props.order.payment_payload.order_string : `my.tradePay({\n  tradeNO: ${JSON.stringify(props.order.payment_payload?.trade_no || props.order.trade_no)}\n})`)
async function copy() { try { await copyText(content.value); message.value = t('已复制', 'Copied') } catch { message.value = t('请选中参数手动复制', 'Select the parameters to copy') } }
</script>
<template>
  <section class="hig-card native-payment-card">
    <div class="qr-receipt-top"><span class="qr-channel"><Brand name="alipay" /><b>{{ order.product === 'app' ? t('APP 支付', 'App payment') : t('小程序支付', 'Mini program payment') }}</b></span><strong><small>¥</small>{{ order.amount }}</strong></div>
    <div class="qr-receipt-meta"><div><span>{{ t('商品名称', 'Item') }}</span><b>{{ order.subject }}</b></div><div><span>{{ t('订单号', 'Order') }}</span><code>{{ order.out_trade_no }}</code></div></div>
    <p>{{ order.product === 'app' ? t('将以下 orderString 交给 APP 内的支付宝 SDK 调起付款', 'Pass this orderString to the Alipay SDK in your app') : t('在支付宝小程序内使用以下交易号调起付款', 'Use this trade number to launch payment inside your Alipay mini program') }}</p>
    <pre tabindex="0">{{ content }}</pre><div class="native-payment-actions"><span role="status">{{ message }}</span><button type="button" class="button button-secondary" @click="copy"><Icon name="copy" :size="15" />{{ t('复制支付参数', 'Copy payment parameters') }}</button></div>
  </section>
</template>
