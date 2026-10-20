<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRoute } from 'vue-router'
import CheckoutReceipt from '../components/CheckoutReceipt.vue'
const route=useRoute(),order=ref(null),error=ref(''),busy=ref(false),qrVersion=ref(0),qrSvg=ref(''),now=ref(Date.now())
const zh=computed(()=>String(navigator.language||'zh').toLowerCase().startsWith('zh'))
const t=(cn,en)=>zh.value?cn:en
const base=computed(()=>`/api/cashier/${encodeURIComponent(route.params.token)}`)
const phase=computed(()=>{
  if(order.value?.paid)return 'paid'
  if(order.value?.status==='CONFIRMING')return 'confirming'
  if(order.value?.status==='EXPIRED'||(order.value?.status==='CREATED'&&order.value.expires_at*1000<=now.value))return 'expired'
  if(order.value?.status==='CLOSED'||order.value?.status==='REFUNDED'||order.value?.status==='REFUNDING')return 'closed'
  return 'waiting'
})
const waiting=computed(()=>phase.value==='waiting')
const qrSource=computed(()=>qrSvg.value?`data:image/svg+xml;charset=utf-8,${encodeURIComponent(qrSvg.value)}`:`${base.value}/qr.svg?v=${qrVersion.value}`)
let timer,posted=false,polling=false,returnTimer
async function request(path='',payload){
  const response=await fetch(base.value+path,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
  const data=await response.json()
  if(!response.ok)throw new Error(({cashier_not_found:t('收银台不存在或已过期','This checkout does not exist or has expired'),cashier_qr_unavailable:t('二维码暂未获取，请重试','The QR code is not ready — try again'),payment_order_expired:t('订单已过期，请返回商户重新下单','This order expired — order again from the merchant'),rate_limited:t('刷新较频繁，请稍后重试','Too many refreshes — try again shortly')})[data.error]||t('收银台暂不可用，请稍后重试','The checkout is unavailable — try again shortly'))
  return data
}
function keepImage(){if(order.value?.qr_svg)qrSvg.value=order.value.qr_svg;else if(!order.value?.qr_ready)qrSvg.value=''}
async function refresh(first=false){
  if(polling)return
  polling=true
  try{
    const result=await request(first?'?qr=1':'')
    const next=result
    order.value=next
    keepImage()
    if(next.paid&&next.status==='PAID'&&!posted){
      posted=true
      if(timer)clearInterval(timer)
      if(window.parent!==window){const requested=route.query.embed_origin;const parent=next.parent_origins?.includes(requested)?requested:next.parent_origin;if(/^https?:\/\//.test(parent||''))window.parent.postMessage({type:'bypay:paid',trade_no:next.trade_no,out_trade_no:next.out_trade_no},parent)}
      if(!next.risk_hold&&next.return_available&&next.return_href)returnTimer=window.setTimeout(()=>{try{window.top.location.assign(next.return_href)}catch{/* Manual return stays available when embedding blocks navigation. */}},5000)
    }
  }catch(e){error.value=e.message}
  finally{polling=false}
}
async function prepare(){if(busy.value||phase.value!=='waiting')return;busy.value=true;error.value='';try{order.value=await request('/prepare',{refresh:Boolean(order.value?.qr_ready)});keepImage();qrVersion.value++}catch(e){error.value=e.message}finally{busy.value=false}}
onMounted(async()=>{await refresh(true);if(order.value&&waiting.value&&!order.value.qr_ready)await prepare();if(phase.value!=='paid')timer=window.setInterval(()=>{now.value=Date.now();if(!document.hidden)refresh()},2500)})
onBeforeUnmount(()=>{clearInterval(timer);clearTimeout(returnTimer)})
</script>
<template><CheckoutReceipt :order="order" :qr-source="qrSource" :busy="busy" :error="error" :zh="zh" @refresh="prepare"/></template>
