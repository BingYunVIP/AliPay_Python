<script setup>
import {onMounted,onBeforeUnmount,watch,ref} from 'vue'
const props=defineProps({siteKey:String,action:String}),emit=defineEmits(['token']),root=ref(null),error=ref('')
let widget,disposed=false
let loading
function script(){if(window.turnstile)return Promise.resolve();if(loading)return loading;loading=new Promise((resolve,reject)=>{let s=document.querySelector('script[data-turnstile]');if(!s){s=document.createElement('script');s.src='https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';s.dataset.turnstile='1';s.async=true;s.defer=true;document.head.append(s)}s.addEventListener('load',resolve,{once:true});s.addEventListener('error',reject,{once:true})});return loading}
async function render(){emit('token','');try{await script();if(disposed)return;if(widget!==undefined)window.turnstile.remove(widget);widget=window.turnstile.render(root.value,{sitekey:props.siteKey,action:props.action,theme:'auto',callback:token=>emit('token',token),'expired-callback':()=>emit('token',''),'error-callback':()=>{emit('token','');error.value='人机验证暂不可用，请重试'}})}catch{error.value='人机验证加载失败，请检查网络'}}
function reset(){emit('token','');if(widget!==undefined)window.turnstile.reset(widget)}
defineExpose({reset});watch(()=>props.action,render);onMounted(render);onBeforeUnmount(()=>{disposed=true;if(widget!==undefined)window.turnstile?.remove(widget)})
</script>
<template><div class="human-check"><div ref="root"></div><p v-if="error" class="error-box" role="alert">{{ error }}</p></div></template>
