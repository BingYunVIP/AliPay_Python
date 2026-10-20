<script setup>
import { inject, onMounted, reactive, ref } from 'vue'
import { api, errorText, t } from '../state'
import Icon from '../components/Icon.vue'
import FormGroup from './FormGroup.vue'
import FormRow from './FormRow.vue'
import SaveBar from './SaveBar.vue'
const c=inject('console'), groups=ref([]), drafts=ref({}), loading=ref(true), saving=ref(false), error=ref(''), notice=ref(''), createOpen=ref(false), createDraft=reactive({name:'',service_rate:'1.50',cost_rate:'1.00'})
const defaults=[{id:1,name:'普通商户组',service_rate:'1.50',cost_rate:'1.00',billing_mode:'merchant_fee',version:1},{id:2,name:'管理员计费组',service_rate:'0.00',cost_rate:'1.00',billing_mode:'admin_cost_only',version:1}]
function snapshot(rows){return Object.fromEntries(rows.map(group=>[group.id,{version:group.version,service_rate:group.service_rate,cost_rate:group.cost_rate}]))}
function dirty(group){const d=drafts.value[group.id];return !!d&&(d.service_rate!==group.service_rate||d.cost_rate!==group.cost_rate)}
async function load(){loading.value=true;try{groups.value=(await api('/api/admin/merchant-groups')).groups;drafts.value=snapshot(groups.value);error.value=''}catch(e){error.value=errorText(e);if(!groups.value.length){groups.value=defaults;drafts.value=snapshot(defaults)}}finally{loading.value=false}}
async function save(group){saving.value=true;error.value='';notice.value='';try{const result=await api(`/api/admin/merchant-groups/${group.id}`,{method:'PUT',body:JSON.stringify(drafts.value[group.id])});groups.value=groups.value.map(row=>row.id===group.id?result.group:row);drafts.value=snapshot(groups.value);notice.value=t('分组费率已保存','Billing group rates saved')}catch(e){error.value=errorText(e)}finally{saving.value=false}}
async function create(){saving.value=true;error.value='';notice.value='';try{await api('/api/admin/merchant-groups',{method:'POST',body:JSON.stringify({...createDraft})});createOpen.value=false;Object.assign(createDraft,{name:'',service_rate:'1.50',cost_rate:'1.00'});await load();c.refresh(true);notice.value=t('商户分组已创建','Merchant group created')}catch(e){error.value=errorText(e)}finally{saving.value=false}}
onMounted(load)
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('商户分组','Merchant groups') }}</h2><p>{{ t('角色决定管理权限，分组决定每笔订单的服务费与通道成本','Roles control access; billing groups set service fees and channel costs') }}</p></div><button class="button button-secondary" @click="createOpen=true">{{ t('新增分组','Add group') }}</button></div>
  <p v-if="error" class="error-box" role="alert">{{ error }}</p><p v-if="notice" class="notice-box" role="status">{{ notice }}</p>
  <section v-for="group in groups" :key="group.id" class="hig-card settings-card merchant-group-card" :aria-busy="loading">
    <div class="group-heading"><div><h3>{{ group.name }}</h3><p>{{ group.id===2?t('管理员账户及管理员指定的商户只计通道成本','Administrator account and explicitly assigned merchants only record channel cost'):t('新注册和手动新增的商户默认进入此分组','Newly registered and manually created merchants start here') }}</p></div><span class="state-badge" :class="group.id===2?'production':'neutral'">{{ group.billing_mode==='admin_cost_only'?t('仅通道成本','Channel cost only'):t('服务费 + 通道成本','Service fee + channel cost') }}</span></div>
    <form class="settings-form group-form" @submit.prevent="save(group)">
      <FormGroup :title="t('订单费率','Order rates')">
        <FormRow field="group-service" :label="t('平台服务费率','Platform service fee')"><div class="unit-field"><input v-model.trim="drafts[group.id].service_rate" class="field" inputmode="decimal" :disabled="saving || group.billing_mode==='admin_cost_only'"/><span class="unit">%</span></div></FormRow>
        <FormRow field="group-channel-cost" :label="t('官方通道成本','Official channel cost')"><div class="unit-field"><input v-model.trim="drafts[group.id].cost_rate" class="field" inputmode="decimal" :disabled="saving"/><span class="unit">%</span></div></FormRow>
        <FormRow field="group-total" :label="t('商户承担合计','Total deducted from merchant')"><strong>{{ (Number(drafts[group.id]?.service_rate||0)+Number(drafts[group.id]?.cost_rate||0)).toFixed(2) }}%</strong></FormRow>
      </FormGroup>
      <SaveBar :dirty="dirty(group)" :busy="saving" :label="t('保存分组','Save group')" @discard="drafts[group.id]={version:group.version,service_rate:group.service_rate,cost_rate:group.cost_rate}" @save="save(group)" />
    </form>
  </section>
  <section class="hig-card settings-card"><h3>{{ t('费率说明','How billing works') }}</h3><p class="field-hint">{{ t('普通商户默认承担 1.5% 平台服务费及 1% 通道成本。管理员计费组不收平台服务费，只计 1% 通道成本。账本按每笔订单保存费率快照。','The default merchant group pays a 1.5% service fee and 1% channel cost. The administrator billing group has no service fee and records only 1% channel cost. Rates are snapshotted per order.') }}</p></section>
  <dialog class="console-dialog group-create-dialog" :open="createOpen" @cancel.prevent="createOpen=false"><div class="dialog-heading"><h2>{{ t('新增商户分组','Add merchant group') }}</h2><button type="button" class="icon-button" :aria-label="t('关闭','Close')" @click="createOpen=false"><Icon name="x"/></button></div><form @submit.prevent="create"><label for="new-group-name">{{ t('分组名称','Group name') }}</label><input id="new-group-name" v-model.trim="createDraft.name" required maxlength="48"/><label for="new-group-service">{{ t('平台服务费率','Platform service fee') }}</label><div class="unit-field"><input id="new-group-service" v-model.trim="createDraft.service_rate" inputmode="decimal" required/><span class="unit">%</span></div><label for="new-group-cost">{{ t('官方通道成本','Official channel cost') }}</label><div class="unit-field"><input id="new-group-cost" v-model.trim="createDraft.cost_rate" inputmode="decimal" required/><span class="unit">%</span></div><p v-if="error" class="error-box" role="alert">{{ error }}</p><div class="dialog-actions"><button type="button" class="button button-secondary" :disabled="saving" @click="createOpen=false">{{ t('取消','Cancel') }}</button><button class="button" :disabled="saving">{{ t('创建分组','Create group') }}</button></div></form></dialog>
</template>
