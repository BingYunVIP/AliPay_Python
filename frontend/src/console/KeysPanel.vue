<script setup>
import { inject, nextTick, onMounted, ref, computed, watch } from 'vue'
import { api, copyText, t } from '../state'
import Icon from '../components/Icon.vue'
import FormGroup from './FormGroup.vue'
import FormRow from './FormRow.vue'
import SaveBar from './SaveBar.vue'
// Shown inside the merchant API dialog: another merchant's credentials for the administrator, your own otherwise
const props=defineProps({merchantId:[String,Number]})
const selectedVersion=ref('v1')
const payment=ref(null), paymentDraft=ref(null), paymentBusy=ref(false), paymentLoading=ref(false), paymentError=ref(''), paymentNotice=ref(''), paymentErrors=ref({})
const c = inject('console'), profile = ref(null), publicKey = ref(''), privateKey = ref(''), reveal = ref(false), busy = ref(false), error = ref(''), notice = ref(''), action = ref(''), dialog = ref(null)
const endpoint = computed(() => '/api/merchant/config' + (props.merchantId ? '?merchant_id='+encodeURIComponent(props.merchantId) : ''))
const paymentEndpoint=computed(()=>'/api/merchant/payment-settings'+(props.merchantId?'?merchant_id='+encodeURIComponent(props.merchantId):''))
const errors = {invalid_rsa_key:'请输入有效的 RSA 公钥',merchant_public_key_required:'请先配置商户 RSA 公钥'}
async function loadPayment({preserveDraft=false}={}){paymentLoading.value=true;paymentError.value='';paymentErrors.value={};try{const loaded=await api(paymentEndpoint.value);payment.value=loaded;if(!preserveDraft||!paymentDraft.value)paymentDraft.value=structuredClone(loaded.settings)}catch(e){paymentError.value=t('无法读取交易参数','Unable to load transaction settings');if(!preserveDraft)paymentDraft.value=null;c?.report?.(e)}finally{paymentLoading.value=false}}
async function load() { try { profile.value = await api(endpoint.value); publicKey.value = profile.value.merchant_public_key; await loadPayment() } catch (e) { c?.report?.(e) } }
const paymentDirty=computed(()=>Boolean(payment.value&&paymentDraft.value&&JSON.stringify(paymentDraft.value)!==JSON.stringify(payment.value.settings)))
c?.guard?.(()=>Boolean(paymentDirty.value))
function fieldDescription(key,id,hasHint=false){return [hasHint?`${id}-hint`:null,paymentErrors.value[key]?`${id}-error`:null].filter(Boolean).join(' ')||undefined}
function fieldInvalid(key){return Boolean(paymentErrors.value[key])}
const amountPattern=/^\d+(?:\.\d{1,2})?$/
function validatePayment(){const d=paymentDraft.value, errors={}, cap=Number(payment.value.platform_maximum_amount), maxTimeout=Number(payment.value.platform_order_timeout_minutes);const amount=k=>amountPattern.test(String(d[k]??''))?Number(d[k]):NaN;const integer=(k,min,max)=>Number.isInteger(d[k])&&d[k]>=min&&d[k]<=max;const minimum=amount('minimum_amount'),maximum=amount('maximum_amount'),daily=amount('daily_limit'),fee=Number(d.fee_rate);if(!Number.isFinite(minimum)||minimum<=0)errors.minimum_amount=t('请输入大于 0、最多两位小数的金额','Enter an amount greater than 0 with at most two decimals');if(!Number.isFinite(maximum)||maximum<=0)errors.maximum_amount=t('请输入有效金额','Enter a valid amount');else if(Number.isFinite(minimum)&&maximum<minimum)errors.maximum_amount=t('不能低于最低支付金额','Must not be below the minimum payment');else if(maximum>cap)errors.maximum_amount=t('不能超过平台单笔上限','Must not exceed the platform cap');if(!Number.isFinite(daily)||daily<0)errors.daily_limit=t('请输入大于或等于 0 的金额','Enter an amount greater than or equal to 0');if(!Number.isFinite(fee)||fee<0||fee>100)errors.fee_rate=t('手续费率须在 0 到 100 之间','Fee rate must be between 0 and 100');if(!integer('timeout_minutes',1,maxTimeout))errors.timeout_minutes=t(`须为 1 到 ${maxTimeout} 的整数`,`Enter a whole number from 1 to ${maxTimeout}`);if(!integer('max_pending_orders',0,100000))errors.max_pending_orders=t('须为 0 到 100000 的整数','Enter a whole number from 0 to 100000');if(!['first','round_robin','minimum_amount'].includes(d.load_balance))errors.load_balance=t('请选择有效的收款配置策略','Choose a valid configuration strategy');if(!integer('cancel_limit',0,100000))errors.cancel_limit=t('须为 0 到 100000 的整数','Enter a whole number from 0 to 100000');if(!integer('cancel_window_minutes',1,10080))errors.cancel_window_minutes=t('须为 1 到 10080 的整数','Enter a whole number from 1 to 10080');return errors}
const paymentFieldIds={minimum_amount:'merchant-minimum',maximum_amount:'merchant-maximum',daily_limit:'merchant-daily',fee_rate:'merchant-fee',timeout_minutes:'merchant-timeout',max_pending_orders:'merchant-pending',load_balance:'merchant-balance',cancel_limit:'merchant-cancel',cancel_window_minutes:'merchant-cancel-window'}
async function focusPaymentError(){await nextTick();document.getElementById(paymentFieldIds[Object.keys(paymentErrors.value)[0]])?.focus()}
async function savePayment(){if(!paymentDirty.value||paymentBusy.value||paymentLoading.value)return;paymentErrors.value=validatePayment();if(Object.keys(paymentErrors.value).length){paymentError.value=t('请修正标红字段','Correct the highlighted fields');await focusPaymentError();return}paymentBusy.value=true;paymentError.value='';paymentNotice.value='';try{const saved=await api(paymentEndpoint.value,{method:'PUT',body:JSON.stringify({...paymentDraft.value,version:payment.value.version})});payment.value={...payment.value,...saved};paymentDraft.value=structuredClone(saved.settings);paymentErrors.value={};paymentNotice.value=t('交易参数已保存','Transaction settings saved')}catch(e){if(e.message==='merchant_payment_settings_conflict'){await loadPayment({preserveDraft:true});paymentError.value=t('设置已在其他位置更新。当前草稿已保留，请核对后再次保存','Settings changed elsewhere. Your draft is preserved; review it before saving again')}else{paymentError.value=t('请检查商户交易参数','Check the merchant transaction settings');const field=e.message==='merchant_amount_below_minimum'?'minimum_amount':e.message==='merchant_amount_above_maximum'?'maximum_amount':null;if(field){paymentErrors.value={[field]:paymentError.value};await focusPaymentError()}}}finally{paymentBusy.value=false}}
onMounted(load)
watch(endpoint,load)
defineExpose({hasPaymentChanges:()=>Boolean(paymentDirty.value)})
async function copy(value) { try { await copyText(String(value)); notice.value = '已复制' } catch { error.value = '请选中内容手动复制' } }
function toPem(buffer,label) { const encoded = btoa(String.fromCharCode(...new Uint8Array(buffer))); return `-----BEGIN ${label}-----\n${encoded.match(/.{1,64}/g).join('\n')}\n-----END ${label}-----\n` }
async function update(payload) {
  busy.value = true; error.value = ''; notice.value = ''
  try { profile.value = await api(endpoint.value,{method:'POST',body:JSON.stringify(payload)}); publicKey.value=profile.value.merchant_public_key; notice.value='配置已保存'; return true }
  catch (e) { error.value=errors[e.message] || '保存失败，请检查密钥内容后重试'; return false }
  finally { busy.value=false }
}
function confirm(kind) { action.value=kind;privateKey.value='';error.value='';dialog.value.showModal() }
async function generate() {
  if (action.value==='md5') { if (await update({rotate_md5:true})) dialog.value.close(); return }
  busy.value=true;error.value=''
  try {
    if (!window.crypto?.subtle) throw new Error('请使用 localhost 或 HTTPS 打开控制台生成密钥')
    const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify'])
    const pub=toPem(await crypto.subtle.exportKey('spki',pair.publicKey),'PUBLIC KEY')
    const secret=toPem(await crypto.subtle.exportKey('pkcs8',pair.privateKey),'PRIVATE KEY')
    if (await update({merchant_public_key:pub})) privateKey.value=secret
  } catch (e) { error.value=e.message }
  finally { busy.value=false }
}
function download() { const url=URL.createObjectURL(new Blob([privateKey.value],{type:'application/x-pem-file'}));const link=document.createElement('a');link.href=url;link.download=`merchant-${profile.value.merchant_id}-private.pem`;link.click();URL.revokeObjectURL(url) }
</script>
<template>
  <p v-if="error" class="error-box" role="alert">{{ error }}</p><p v-if="notice" class="field-hint" role="status">{{ notice }}</p>
  <template v-if="profile">
    <div class="merchant-summary"><section class="hig-card merchant-identity"><label>接口地址<div class="credential-row"><input :value="profile.interface_url" readonly /><button class="icon-button" aria-label="复制接口地址" @click="copy(profile.interface_url)"><Icon name="copy" :size="17"/></button></div></label><label>商户 ID<div class="credential-row"><input :value="profile.merchant_id" readonly/><button class="icon-button" aria-label="复制商户 ID" @click="copy(profile.merchant_id)"><Icon name="copy" :size="17"/></button></div></label></section><section class="hig-card merchant-mode"><label>签名方式<select :value="profile.signature_mode" :disabled="busy" @change="update({signature_mode:$event.target.value})"><option value="both">V1 MD5 + V2 RSA</option><option value="rsa">仅 V2 RSA</option></select></label></section></div>
    <div class="merchant-version-tabs" role="tablist" aria-label="接口版本"><button type="button" role="tab" :aria-selected="selectedVersion==='v1'" @click="selectedVersion='v1'">V1 · MD5</button><button type="button" role="tab" :aria-selected="selectedVersion==='v2'" @click="selectedVersion='v2'">V2 · RSA</button></div><div class="merchant-keys-grid">
      <section v-if="selectedVersion==='v1'" class="hig-card merchant-key-card">
        <label>商户 MD5 密钥<div class="credential-row"><input :type="reveal?'text':'password'" :value="profile.md5_key" readonly autocomplete="off"/><button class="icon-button" :aria-label="reveal?'隐藏密钥':'显示密钥'" @click="reveal=!reveal"><Icon :name="reveal?'eye-off':'eye'" :size="17"/></button><button class="icon-button" aria-label="复制 MD5 密钥" @click="copy(profile.md5_key)"><Icon name="copy" :size="17"/></button></div></label>
        <button class="button button-secondary" :disabled="busy" @click="confirm('md5')">重置 MD5 密钥</button>
      </section>
      <section v-else class="hig-card merchant-key-card">
        <label>平台公钥<textarea :value="profile.platform_public_key" readonly rows="4" spellcheck="false"/></label><button class="button button-secondary" @click="copy(profile.platform_public_key)">复制平台公钥</button>
        <label>商户公钥<textarea v-model="publicKey" rows="4" spellcheck="false" placeholder="粘贴已有 RSA 公钥，或生成密钥对"/></label>
        <div class="merchant-key-actions"><button class="button button-secondary" :disabled="busy || !publicKey.trim()" @click="update({merchant_public_key:publicKey})">保存商户公钥</button><button class="button" :disabled="busy" @click="confirm('rsa')">{{ profile.merchant_public_key?'重新生成密钥对':'生成 RSA 密钥对' }}</button></div>
      </section>
    </div>
    <div v-if="paymentLoading && !paymentDraft" class="payment-settings-state" role="status">
      {{ t('正在读取交易参数…','Loading transaction settings…') }}
    </div>
    <div v-else-if="paymentError && (!payment || !paymentDraft)" class="payment-settings-state error-box" role="alert">
      <span>{{ paymentError }}</span>
      <button type="button" class="button button-secondary" @click="loadPayment">{{ t('重试','Retry') }}</button>
    </div>
    <form v-else-if="payment && paymentDraft" class="settings-form merchant-payment-settings" @submit.prevent="savePayment">
      <FormGroup :title="t('商户交易参数','Merchant transaction settings')">
        <FormRow field="merchant-minimum" :label="t('最低支付金额','Minimum payment')" :error="paymentErrors.minimum_amount"><div class="unit-field"><input id="merchant-minimum" v-model="paymentDraft.minimum_amount" class="field" inputmode="decimal" :disabled="paymentBusy || paymentLoading" :aria-invalid="fieldInvalid('minimum_amount')" :aria-describedby="fieldDescription('minimum_amount','merchant-minimum')"/><span class="unit">¥</span></div></FormRow>
        <FormRow field="merchant-maximum" :label="t('最高支付金额','Maximum payment')" :hint="t(`平台上限 ¥${payment?.platform_maximum_amount || ''}`, `Platform cap ¥${payment?.platform_maximum_amount || ''}`)" :error="paymentErrors.maximum_amount"><div class="unit-field"><input id="merchant-maximum" v-model="paymentDraft.maximum_amount" class="field" inputmode="decimal" :disabled="paymentBusy || paymentLoading" :aria-invalid="fieldInvalid('maximum_amount')" :aria-describedby="fieldDescription('maximum_amount','merchant-maximum',true)"/><span class="unit">¥</span></div></FormRow>
        <FormRow field="merchant-daily" :label="t('每日限额','Daily limit')" :hint="t('0 表示不限额','0 means no limit')" :error="paymentErrors.daily_limit"><div class="unit-field"><input id="merchant-daily" v-model="paymentDraft.daily_limit" class="field" inputmode="decimal" :disabled="paymentBusy || paymentLoading" :aria-invalid="fieldInvalid('daily_limit')" :aria-describedby="fieldDescription('daily_limit','merchant-daily',true)"/><span class="unit">¥</span></div></FormRow>
        <FormRow field="merchant-fee" :label="t('商户手续费率','Merchant fee rate')" :error="paymentErrors.fee_rate"><div class="unit-field"><input id="merchant-fee" v-model="paymentDraft.fee_rate" class="field" inputmode="decimal" :disabled="paymentBusy || paymentLoading" :aria-invalid="fieldInvalid('fee_rate')" :aria-describedby="fieldDescription('fee_rate','merchant-fee')"/><span class="unit">%</span></div></FormRow>
        <FormRow field="merchant-timeout" :label="t('订单有效期','Order timeout')" :hint="t(`不超过平台 ${payment?.platform_order_timeout_minutes || ''} 分钟`, `At most ${payment?.platform_order_timeout_minutes || ''} platform minutes`)" :error="paymentErrors.timeout_minutes"><div class="unit-field"><input id="merchant-timeout" v-model.number="paymentDraft.timeout_minutes" class="field" type="number" min="1" :max="payment?.platform_order_timeout_minutes" :disabled="paymentBusy || paymentLoading" :aria-invalid="fieldInvalid('timeout_minutes')" :aria-describedby="fieldDescription('timeout_minutes','merchant-timeout',true)"/><span class="unit">{{ t('分钟','min') }}</span></div></FormRow>
        <FormRow field="merchant-pending" :label="t('最大待支付订单数','Maximum pending orders')" :hint="t('0 表示不限量','0 means unlimited')" :error="paymentErrors.max_pending_orders"><input id="merchant-pending" v-model.number="paymentDraft.max_pending_orders" class="field" type="number" min="0" :disabled="paymentBusy || paymentLoading" :aria-invalid="fieldInvalid('max_pending_orders')" :aria-describedby="fieldDescription('max_pending_orders','merchant-pending',true)"/></FormRow>
        <FormRow field="merchant-balance" :label="t('收款配置策略','Configuration selection')" :error="paymentErrors.load_balance"><select id="merchant-balance" v-model="paymentDraft.load_balance" class="field" :disabled="paymentBusy || paymentLoading" :aria-invalid="fieldInvalid('load_balance')" :aria-describedby="fieldDescription('load_balance','merchant-balance')"><option value="first">{{ t('首个可用配置','First available') }}</option><option value="round_robin">{{ t('顺序轮询','Round robin') }}</option><option value="minimum_amount">{{ t('最低累计收款','Lowest collected amount') }}</option></select></FormRow>
        <FormRow field="merchant-cancel" :label="t('取消次数上限','Cancellation limit')" :hint="t('0 表示不限量，仅统计渠道确认关闭的主动取消','0 means unlimited; only merchant-requested provider-confirmed closures count')" :error="paymentErrors.cancel_limit"><input id="merchant-cancel" v-model.number="paymentDraft.cancel_limit" class="field" type="number" min="0" :disabled="paymentBusy || paymentLoading" :aria-invalid="fieldInvalid('cancel_limit')" :aria-describedby="fieldDescription('cancel_limit','merchant-cancel',true)"/></FormRow>
        <FormRow field="merchant-cancel-window" :label="t('取消统计窗口','Cancellation window')" :error="paymentErrors.cancel_window_minutes"><div class="unit-field"><input id="merchant-cancel-window" v-model.number="paymentDraft.cancel_window_minutes" class="field" type="number" min="1" max="10080" :disabled="paymentBusy || paymentLoading" :aria-invalid="fieldInvalid('cancel_window_minutes')" :aria-describedby="fieldDescription('cancel_window_minutes','merchant-cancel-window')"/><span class="unit">{{ t('分钟','min') }}</span></div></FormRow>
      </FormGroup>
      <p v-if="paymentLoading" class="field-hint" role="status">{{ t('正在刷新服务端版本…','Refreshing the server version…') }}</p>
      <p v-if="paymentError" class="error-box" role="alert">{{ paymentError }}</p>
      <p v-if="paymentNotice" class="field-hint" role="status">{{ paymentNotice }}</p>
      <SaveBar :dirty="paymentDirty" :busy="paymentBusy || paymentLoading" :label="t('保存交易参数','Save transaction settings')" :error="paymentError" @discard="loadPayment({preserveDraft:false})" />
    </form>
  </template>
  <dialog ref="dialog" class="console-dialog" aria-label="管理商户密钥" @close="privateKey=''" @cancel.prevent="!busy && dialog.close()"><div class="dialog-heading"><h2>{{ privateKey?'保存商户私钥':action==='md5'?'重置 MD5 密钥':'生成商户 RSA 密钥对' }}</h2><button class="icon-button" :disabled="busy" aria-label="关闭" @click="dialog.close()"><Icon name="x"/></button></div>
    <template v-if="privateKey"><p>私钥仅在当前窗口显示，请保存后填写到测试站的 V2 配置</p><textarea class="key-secret" :value="privateKey" readonly rows="9" aria-label="商户私钥"/><div class="dialog-actions"><button class="button button-secondary" @click="copy(privateKey)">复制私钥</button><button class="button" @click="download">下载私钥</button></div></template>
    <template v-else><p>生成后请同步更新使用该商户接口的应用配置</p><p v-if="error" class="error-box" role="alert">{{ error }}</p><div class="dialog-actions"><button class="button button-secondary" :disabled="busy" @click="dialog.close()">取消</button><button class="button" :disabled="busy" @click="generate"><Icon v-if="busy" name="loader" class="spin" :size="16"/>确认生成</button></div></template>
  </dialog>
</template>
<style scoped>
.merchant-payment-settings{margin-top:24px}.merchant-summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin-bottom:20px;align-items:stretch}.merchant-identity,.merchant-key-card,.merchant-mode{padding:24px}.merchant-identity{display:grid;grid-template-columns:1fr;gap:18px;align-content:start}.merchant-keys-grid{display:grid;align-items:start;grid-template-columns:1fr;gap:20px}.merchant-key-card label,.merchant-mode label,.merchant-identity label{display:block;font-size:13px}.merchant-key-card label{margin-top:20px}.credential-row{display:flex;align-items:center;gap:8px;margin-top:9px}.credential-row input{min-width:0;flex:1;width:100%}.merchant-key-card textarea{width:100%;box-sizing:border-box;margin:10px 0;font:12px/1.5 monospace;resize:vertical}.merchant-key-card .button{margin-top:12px}.merchant-key-actions{display:flex;gap:10px;flex-wrap:wrap}.merchant-mode{display:flex;flex-direction:column;justify-content:flex-start}.merchant-mode select{display:block;width:100%;margin-top:12px}@media(max-width:850px){.merchant-summary,.merchant-keys-grid{grid-template-columns:1fr}}.payment-settings-state{display:flex;align-items:center;justify-content:space-between;gap:14px;min-height:88px;padding:20px;color:var(--muted)}.payment-settings-state.error-box{margin-top:20px}.payment-settings-state .button{flex-shrink:0}@media(max-width:520px){.merchant-payment-settings :deep(.form-row){grid-template-columns:minmax(0,1fr);align-items:start;row-gap:8px}.merchant-payment-settings :deep(.form-control){align-items:stretch}.merchant-payment-settings :deep(.form-control > .field),.merchant-payment-settings :deep(.form-control > .unit-field){width:100%}.merchant-payment-settings :deep(.form-error){text-align:left}.payment-settings-state{align-items:flex-start;flex-direction:column}}
</style>
