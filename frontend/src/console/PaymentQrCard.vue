<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, t } from '../state'
import Brand from '../components/Brand.vue'
import Icon from '../components/Icon.vue'
const props=defineProps({order:{type:Object,required:true}})
const now=ref(Date.now()),failed=ref(false),preparing=ref(false),ready=ref(Boolean(props.order.cashier_qr)),dialog=ref(null)
let timer,disposed=false
const remaining=computed(()=>Math.max(0,Math.ceil(props.order.expires_at-now.value/1000)))
const active=computed(()=>!props.order.deleted_at&&props.order.status==='CREATED'&&remaining.value>0)
const countdown=computed(()=>`${Math.floor(remaining.value/60)}:${String(remaining.value%60).padStart(2,'0')}`)
const name=computed(()=>({alipay:t('支付宝','Alipay'),wechat:t('微信支付','WeChat Pay'),paypal:'PayPal',stripe:'Stripe'})[props.order.channel])
const source=computed(()=>`/api/v1/payments/${encodeURIComponent(props.order.out_trade_no)}/qr.svg`)
async function prepare(){
  if(!active.value||preparing.value)return
  preparing.value=true;failed.value=false
  try{
    if(props.order.product==='page'){const result=await api(`/api/v1/payments/${props.order.out_trade_no}/prepare-qr`,{method:'POST',body:'{}'});if(!disposed)ready.value=Boolean(result.order?.cashier_qr)}
    else if(!disposed)ready.value=true
    if(!ready.value&&!disposed)failed.value=true
  }catch{if(!disposed)failed.value=true}
  finally{if(!disposed)preparing.value=false}
}
watch(active,value=>{if(!value)dialog.value?.close()})
onMounted(()=>{timer=setInterval(()=>now.value=Date.now(),1000);if(!ready.value)prepare()})
onBeforeUnmount(()=>{disposed=true;clearInterval(timer)})
</script>
<template>
<section class="payment-qr-card" :class="`qr-${order.channel}`" aria-label="订单收款码">
  <header class="qr-receipt-header"><span class="qr-channel"><Brand :name="order.channel"/><b>{{ name }}</b></span><strong>￥{{ order.amount }}</strong><span class="qr-countdown" :aria-label="`剩余 ${countdown}`"><Icon name="timer" :size="14"/>{{ countdown }}</span></header>
  <div class="qr-image-wrap">
    <button v-if="active&&ready&&!failed&&!preparing" type="button" class="qr-expand" aria-label="放大收款二维码" @click="dialog.showModal()"><img :src="source" :alt="`${name}付款二维码`" width="230" height="230" @error="failed=true"/></button>
    <div v-else-if="preparing&&active" class="qr-state" role="status"><Icon name="loader" class="spin" :size="28"/><span>正在获取付款码</span></div>
    <div v-else class="qr-state" role="status"><Icon :name="order.paid_at?'success':'timer'" :size="32"/><b>{{ order.paid_at?'已确认支付':!active?'订单已过期或关闭':'付款码获取失败' }}</b><button v-if="failed&&active" class="button button-secondary" type="button" @click="prepare">重新获取</button></div>
  </div>
  <dialog ref="dialog" class="qr-zoom-dialog" aria-label="收款二维码"><div class="dialog-heading"><b>{{ name }} · ￥{{ order.amount }}</b><button type="button" class="icon-button" aria-label="关闭二维码" @click="dialog.close()"><Icon name="x"/></button></div><img v-if="active&&ready" :src="source" :alt="`${name}付款二维码`"/></dialog>
</section>
</template>
