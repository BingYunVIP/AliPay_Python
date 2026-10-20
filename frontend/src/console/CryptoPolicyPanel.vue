<script setup>
import {computed,inject,onMounted,reactive,ref} from 'vue'
import {api,errorText} from '../state'
import FormGroup from './FormGroup.vue'
import FormRow from './FormRow.vue'
import SaveBar from './SaveBar.vue'
const c=inject('console'),form=reactive({}),saved=ref(''),busy=ref(false),error=ref('')
const dirty=computed(()=>saved.value&&JSON.stringify(form)!==saved.value)
c.guard(()=>Boolean(dirty.value))
async function load(){try{Object.assign(form,(await api('/api/admin/crypto-policy')).settings);saved.value=JSON.stringify(form)}catch(e){error.value=errorText(e)}}
async function save(){busy.value=true;error.value='';try{Object.assign(form,(await api('/api/admin/crypto-policy',{method:'PUT',body:JSON.stringify(form)})).settings);saved.value=JSON.stringify(form);c.toast('Crypto 参数已保存')}catch(e){error.value=errorText(e)}finally{busy.value=false}}
onMounted(load)
</script>
<template><form v-if="saved" @submit.prevent="save"><FormGroup title="扫描与确认"><FormRow field="crypto-gap" label="区块最大差值" hint="落后时分批补扫，不跳过未处理交易"><input id="crypto-gap" v-model.number="form.block_max_gap" type="number" min="1" max="100000" required/></FormRow><FormRow field="crypto-offset" label="区块偏移确认" hint="关闭额外等待仍须通过有效性和最终性核验"><label class="checkbox-row"><input id="crypto-offset" v-model="form.offset_confirm" type="checkbox"/>按网络配置等待确认数</label><p v-if="!form.offset_confirm" class="form-error">关闭会降低额外确认等待，请确认风险</p></FormRow><FormRow field="crypto-lookback" label="订单回溯时间" hint="小时；订单按创建时规则保留核验窗口"><input id="crypto-lookback" v-model.number="form.lookback_hours" type="number" min="1" max="168" required/></FormRow></FormGroup><FormGroup title="订单与金额匹配"><FormRow field="crypto-min" label="单笔最小金额（CNY）"><input id="crypto-min" v-model="form.min_amount" type="number" min="0.01" step="0.01" required/></FormRow><FormRow field="crypto-max" label="单笔最大金额（CNY）" hint="与平台订单上限共用同一配置"><input id="crypto-max" v-model="form.max_amount" type="number" min="0.01" step="0.01" required/></FormRow><FormRow field="crypto-match" label="金额匹配模式"><select id="crypto-match" v-model="form.match_mode"><option value="exact">精确匹配（默认）</option><option value="prefix">前缀匹配</option><option value="round">修约匹配</option></select><p v-if="form.match_mode!=='exact'" class="form-error">非精确模式可能接受金额差异，规则只影响新订单</p></FormRow><FormRow field="crypto-nodes" label="区块节点"><RouterLink id="crypto-nodes" to="/admin/settings/nodes" class="text-link">管理九条网络的 RPC 与确认数</RouterLink></FormRow></FormGroup><SaveBar :dirty="Boolean(dirty)" :busy="busy" :error="error" label="保存 Crypto 参数" @discard="Object.assign(form,JSON.parse(saved))"/></form><p v-else-if="error" class="error-box">{{error}}</p></template>
