<script setup>
import {onMounted,ref} from 'vue'
import {api,errorText} from '../state'
const state=ref(null),error=ref(''),busy=ref(false)
async function load(){try{state.value=await api('/api/admin/tasks')}catch(e){error.value=errorText(e)}}
async function run(){busy.value=true;try{await api('/api/admin/tasks/run',{method:'POST'});await load()}catch(e){error.value=errorText(e)}finally{busy.value=false}}
onMounted(load)
</script>
<template><section class="hig-card settings-card"><div class="card-heading"><h3>计划任务</h3><button class="button" :disabled="busy" @click="run">执行一次平台任务</button></div><p class="field-hint">由 bypay 工作进程持续执行，支付核验、回调和退款任务保持运行，邮件及平台任务开关在商户规则中设置</p><p v-if="error" class="error-box" role="alert">{{ error }}</p><template v-if="state"><div class="setting-value-row"><span>账本借贷平衡检查</span><b>{{ state.ledger_balanced?'通过':'发现不平衡，请停止结算并排查' }}</b></div><div class="setting-value-row"><span>当前识别的客户端 IP</span><code>{{ state.client_ip }}</code></div><div v-for="h in state.heartbeats" :key="h.name" class="setting-value-row"><span>{{ h.name }} 心跳</span><time>{{ new Date(h.last_seen*1000).toLocaleString() }}</time></div><div v-for="task in state.tasks" :key="task.name" class="setting-value-row"><b>{{ task.name }}</b><span>{{ task.status }} · {{ task.error||'—' }}</span><time>{{ task.finished_at?new Date(task.finished_at*1000).toLocaleString():'尚未执行' }}</time></div></template></section></template>
