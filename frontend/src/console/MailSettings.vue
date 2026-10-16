<script setup>
import { onMounted,onBeforeUnmount,reactive,ref,watch } from 'vue'
import { api,errorText } from '../state'
import Icon from '../components/Icon.vue'
const cfg=reactive({}),password=ref(''),busy=ref(false),error=ref(''),notice=ref(''),recipient=ref(''),event=ref('verification'),language=ref('zh'),template=reactive({subject:'',html:''}),preview=ref(''),previewSubject=ref(''),placeholders=ref([]),jobs=ref([]),htmlEditor=ref(null)
let timer,sequence=0,disposed=false
async function load(){try{const data=await api('/api/admin/mail');Object.assign(cfg,data.config);placeholders.value=data.placeholders;jobs.value=data.jobs;await loadTemplate()}catch(e){error.value=errorText(e)}}
async function loadTemplate(){try{Object.assign(template,await api(`/api/admin/mail/template?event=${event.value}&language=${language.value}`));await render()}catch(e){error.value=errorText(e)}}
async function render(reset=false){const n=++sequence;try{const data=await api('/api/admin/mail/preview',{method:'POST',body:JSON.stringify({event:event.value,language:language.value,template:{...template},reset})});if(disposed||n!==sequence)return;if(reset)Object.assign(template,data.template);preview.value=data.html;previewSubject.value=data.subject}catch(e){if(!disposed)error.value=errorText(e)}}
watch([event,language],loadTemplate)
watch(()=>[template.subject,template.html],()=>{clearTimeout(timer);timer=setTimeout(()=>render(),400)})
async function perform(action){busy.value=true;error.value='';notice.value='';try{if(action==='smtp'){const {password_configured,...saved}=cfg;Object.assign(cfg,await api('/api/admin/mail',{method:'PUT',body:JSON.stringify({...saved,password:password.value})}));password.value='';notice.value='邮箱配置已保存'}else if(action==='template'){await api('/api/admin/mail/template',{method:'PUT',body:JSON.stringify({event:event.value,language:language.value,template:{...template}})});notice.value='模板已保存'}else{await api('/api/admin/mail/test',{method:'POST',body:JSON.stringify({recipient:recipient.value,template:{...template},event:event.value,language:language.value})});notice.value='测试邮件已提交 SMTP 服务器'} }catch(e){error.value=errorText(e)}finally{busy.value=false}}
function insert(key){const input=htmlEditor.value,text=`{{${key}}}`;const start=input.selectionStart,end=input.selectionEnd;template.html=template.html.slice(0,start)+text+template.html.slice(end);input.focus()}
onMounted(load);onBeforeUnmount(()=>{disposed=true;clearTimeout(timer)})
</script>
<template>
<div class="operation-settings">
<p v-if="error" class="error-box" role="alert">{{ error }}</p><p v-if="notice" role="status" class="field-hint">{{ notice }}</p>
<form class="hig-card settings-card" @submit.prevent="perform('smtp')"><div class="card-heading"><h3>邮箱配置</h3><button class="button" :disabled="busy">保存邮箱配置</button></div>
<div class="policy-form-grid"><label class="check-field"><input v-model="cfg.enabled" type="checkbox"/>启用邮件服务</label><label>SMTP 服务器<input v-model="cfg.host" placeholder="smtp.qq.com" required/></label><label>端口<input v-model.number="cfg.port" type="number" min="1" max="65535" required/></label><label>连接加密<select v-model="cfg.security"><option value="ssl">SSL / TLS</option><option value="starttls">STARTTLS</option></select></label><label>SMTP 账号<input v-model="cfg.username" autocomplete="off" placeholder="your@qq.com"/></label><label>SMTP 授权码<input v-model="password" type="password" autocomplete="new-password" :placeholder="cfg.password_configured?'已保存，留空不修改':'QQ 邮箱授权码'"/></label><label>发件邮箱<input v-model="cfg.sender" type="email" placeholder="your@qq.com"/></label><label>发件名称<input v-model="cfg.sender_name"/></label></div>
<p class="field-hint">QQ 邮箱默认 smtp.qq.com · 465 · SSL/TLS，使用邮箱设置中的 SMTP 授权码</p>
<div class="policy-checks"><label><input type="checkbox" v-model="cfg.registration_code"/>注册时验证邮箱</label><label><input type="checkbox" v-model="cfg.notify_registration"/>发送注册通知</label><label><input type="checkbox" v-model="cfg.notify_payments"/>发送支付成功提醒</label><label><input type="checkbox" v-model="cfg.notify_risk"/>发送风控暂停提醒</label></div>
</form>
<section class="hig-card settings-card"><div class="card-heading"><h3>邮件模板</h3><div class="inline-actions"><button class="button button-secondary" :disabled="busy" @click="render(true)">恢复默认模板</button><button class="button" :disabled="busy" @click="perform('template')">保存模板</button></div></div>
<div class="policy-form-grid"><label>事件<select v-model="event"><option value="verification">邮箱验证码</option><option value="registration">注册通知</option><option value="reminder">业务提醒</option></select></label><label>语言<select v-model="language"><option value="zh">中文</option><option value="en">English</option></select></label></div>
<div class="email-template-workbench"><div><label>主题<input v-model="template.subject" maxlength="200"/></label><label>HTML 模板<textarea ref="htmlEditor" v-model="template.html" class="email-code" spellcheck="false" maxlength="60000" aria-label="邮件 HTML 模板"/></label><div class="template-tokens"><b>可用占位符</b><button v-for="key in placeholders" :key="key" type="button" @click="insert(key)">{{ '{'+'{'+key+'}'+'}' }}</button></div></div><div class="email-preview"><div><b>实时预览</b><small>{{ previewSubject }}</small></div><iframe sandbox="" :srcdoc="preview" title="邮件实时预览" referrerpolicy="no-referrer"/></div></div>
<form class="mail-test-row" @submit.prevent="perform('test')"><label>测试收件邮箱<input v-model="recipient" type="email" required placeholder="接收测试邮件的地址"/></label><button class="button button-secondary" :disabled="busy||!cfg.enabled"><Icon name="mail" :size="16"/>发送测试邮件</button></form>
</section>
<section class="hig-card settings-card"><h3>最近邮件投递</h3><p v-if="!jobs.length" class="field-hint">暂无投递记录</p><div v-for="job in jobs" :key="job.id" class="setting-value-row"><span>#{{ job.id }} · {{ ({pending:'等待投递',sent:'已发送',failed:'发送失败'})[job.status] }}</span><small>{{ job.attempts }} 次 · {{ job.error || '—' }}</small></div></section>
</div>
</template>
