<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api, errorText, t } from '../state'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const c = inject('console')
const newType = ref('alipay'), configName = ref(''), configEnabled = ref(true), adding = ref(false)
const records = ref([]), loading = ref(true), loadError = ref(''), confirming = ref(false), toggling = ref(false)
const dialog = ref(null), editing = ref(null), fields = reactive({}), saving = ref(false), error = ref(''), errorField = ref('')
let disposed = false, requestId = 0
const definitions = computed(() => [
  { id: 'alipay', name: t('支付宝', 'Alipay'), identifier: 'app_id', identifierLabel: 'APP ID' },
  { id: 'wechat', name: t('微信支付', 'WeChat Pay'), identifier: 'mch_id', identifierLabel: t('商户号', 'Merchant ID') },
  { id: 'paypal', name: 'PayPal', identifier: 'client_id', identifierLabel: 'Client ID' },
  { id: 'stripe', name: 'Stripe', identifier: 'publishable_key', identifierLabel: t('公开密钥', 'Publishable key') },
])
const channels = computed(() => records.value.map(ch => ({ ...definitions.value.find(d => d.id === ch.id), ...ch })))
const schemas = computed(() => ({
  alipay: [
    { key: 'env', label: t('运行环境', 'Environment'), type: 'env' },
    { key: 'app_id', label: 'APP ID', placeholder: t('支付宝应用 APP ID', 'Alipay application ID') },
    { key: 'seller_id', label: t('收款方支付宝 UID', 'Alipay seller UID'), placeholder: t('2088 开头的 16 位 UID', '16-digit UID starting with 2088') },
    { key: 'app_private_key', label: t('应用私钥 RSA2', 'App private key RSA2'), secret: true, multiline: true },
    { key: 'alipay_public_key', label: t('支付宝公钥', 'Alipay public key'), secret: true, multiline: true },
  ],
  wechat: [
    { key: 'app_id', label: 'App ID', placeholder: 'wx…' },
    { key: 'mch_id', label: t('微信支付商户号', 'WeChat merchant ID') },
    { key: 'certificate_serial', label: t('商户证书序列号', 'Merchant certificate serial number'), placeholder: t('40 位十六进制，可直接粘贴 serial= 后面的内容', '40 hex characters; the part after serial= is fine') },
    { key: 'api_v3_key', label: t('API v3 密钥', 'API v3 key'), secret: true, placeholder: t('32 个字符', '32 characters') },
    { key: 'merchant_private_key', label: t('商户私钥', 'Merchant private key'), secret: true, multiline: true, placeholder: '-----BEGIN PRIVATE KEY-----' },
    { key: 'platform_public_key_id', label: t('微信支付公钥 ID', 'WeChat Pay public key ID'), placeholder: 'PUB_KEY_ID_…' },
    { key: 'platform_public_key', label: t('微信支付公钥', 'WeChat Pay public key'), secret: true, multiline: true, placeholder: '-----BEGIN PUBLIC KEY-----' },
  ],
  paypal: [
    { key: 'env', label: t('运行环境', 'Environment'), type: 'env' },
    { key: 'client_id', label: 'Client ID' },
    { key: 'client_secret', label: 'Client Secret', secret: true },
    { key: 'webhook_id', label: 'Webhook ID' },
  ],
  stripe: [
    { key: 'publishable_key', label: t('公开密钥', 'Publishable key'), placeholder: 'pk_test_… / pk_live_…' },
    { key: 'secret_key', label: t('服务端密钥', 'Secret key'), secret: true, placeholder: 'sk_test_… / sk_live_…' },
    { key: 'webhook_secret', label: t('Webhook 签名密钥', 'Webhook signing secret'), secret: true, placeholder: 'whsec_…' },
  ],
}))
const editorFields = computed(() => editing.value ? schemas.value[editing.value.id] : [])
const checking = ref(0), checks = reactive({})
// 正式环境徽章只在“配置完整且最近一次检查没有失败”时出现
function environment(channel) {
  if (!channel.configured || checks[channel.config_id]?.ok === false) return null
  const env = channel.fields.env || 'production'
  return env === 'production' ? { tone: 'production', label: t('正式环境', 'Production') } : { tone: 'neutral', label: t('沙箱环境', 'Sandbox') }
}
function checkResult(channel, result) {
  if (channel.id !== 'wechat') return result.authenticated
    ? { ok: true, text: t('配置完整，密钥格式校验通过', 'Configuration complete and key formats verified') }
    : { ok: false, text: t('配置不完整，请补全必填参数后再检查', 'Configuration is incomplete — fill in every required field') }
  if (!result.authenticated) return { ok: false, text: errorText(new Error(result.code || 'payment_provider_error')) }
  if (result.verified === true) return { ok: true, text: t('商户号、证书序列号、商户私钥和微信支付公钥均验证通过', 'Merchant number, certificate serial, private key and WeChat Pay public key all verified') }
  if (result.verified === false) return { ok: false, text: t('商户信息正确，但微信支付公钥或公钥 ID 不对，请重新下载公钥', 'Merchant details are right, but the WeChat Pay public key or its ID is wrong — download the public key again') }
  return { ok: true, text: t('商户信息正确；微信支付没有返回签名，暂时无法确认公钥', 'Merchant details are right; WeChat Pay sent no signature, so the public key could not be confirmed') }
}
async function check(channel) {
  checking.value = channel.config_id; delete checks[channel.config_id]
  try {
    checks[channel.config_id] = checkResult(channel, await api(`/api/admin/channel-configs/${channel.config_id}/check`, { method: 'POST' }))
  } catch (e) { checks[channel.config_id] = { ok: false, text: errorText(e) } }
  finally { checking.value = 0 }
}
async function load() {
  const id = ++requestId
  try {
    const data = await api('/api/admin/channels')
    if (!disposed && id === requestId) { records.value = data.channels; loadError.value = '' }
  } catch (e) { if (!disposed) loadError.value = errorText(e) }
  finally { if (!disposed) loading.value = false }
}
async function openEditor(channel) {
  adding.value = false; editing.value = channel; configName.value = channel.name; configEnabled.value = channel.enabled; error.value = ''; errorField.value = ''
  Object.keys(fields).forEach(key => delete fields[key])
  editorFields.value.forEach(field => { fields[field.key] = field.secret ? '' : channel.fields[field.key] || (field.type === 'env' ? 'sandbox' : '') })
  await nextTick(); dialog.value.showModal()
}
function closeEditor() {
  if (saving.value) return
  dialog.value.close(); editing.value = null
  Object.keys(fields).forEach(key => delete fields[key])
}
async function addConfig() {
  adding.value = true; editing.value = { id: newType.value, name: '', fields: {}, secrets: {}, version: 0 }; configName.value = ''; configEnabled.value = true; error.value = ''; errorField.value = ''
  Object.keys(fields).forEach(key => delete fields[key])
  editorFields.value.forEach(field => { fields[field.key] = field.type === 'env' ? 'sandbox' : '' })
  await nextTick(); dialog.value.showModal()
}
function changeType() {
  editing.value.id = newType.value
  Object.keys(fields).forEach(key => delete fields[key])
  editorFields.value.forEach(field => { fields[field.key] = field.type === 'env' ? 'sandbox' : '' })
}
async function save() {
  saving.value = true; error.value = ''; errorField.value = ''
  try {
    await api(adding.value ? '/api/admin/channels' : `/api/admin/channel-configs/${editing.value.config_id}`, { method: adding.value ? 'POST' : 'PUT', body: JSON.stringify({ channel: editing.value.id, name: configName.value, enabled: configEnabled.value, revision: editing.value.revision, version: editing.value.version, fields: { ...fields } }) })
    saving.value = false; closeEditor()
    await Promise.all([load(), c.refresh(true)])
    c.toast(t('渠道配置已保存', 'Channel configuration saved'))
  } catch (e) { error.value = errorText(e); errorField.value = e.field || '' }
  finally { saving.value = false }
}
async function toggle() {
  toggling.value = true; error.value = ''
  try {
    const ch = confirming.value
    await api(`/api/admin/channel-configs/${ch.config_id}`, { method: 'PUT', body: JSON.stringify({ fields: {}, version: ch.version, revision: ch.revision, enabled: !ch.enabled }) })
    await Promise.all([load(), c.refresh(true)]); confirming.value = false; c.toast(t('配置状态已更新', 'Configuration status updated'))
  } catch (e) { error.value = errorText(e) }
  finally { toggling.value = false }
}
onMounted(load)
watch(c.revision, load)
onBeforeUnmount(() => { disposed = true; Object.keys(fields).forEach(key => delete fields[key]) })
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('收款渠道', 'Payment channels') }}</h2><p>{{ t('配置商户参数与密钥，管理收款入口', 'Configure merchant credentials and payment availability') }}</p></div><button class="button button-secondary" @click="addConfig"><Icon name="plus" :size="16" />{{ t('添加配置', 'Add configuration') }}</button></div>
  <p v-if="loadError" class="error-box" role="alert">{{ loadError }}</p>
  <div class="channel-card-grid" :aria-busy="loading"><article v-for="ch in channels" :key="ch.config_id" class="hig-card payment-channel-card">
    <div class="channel-card-top"><span class="channel-mark" :class="ch.id"><Brand :name="ch.id" /></span><div class="channel-card-title"><h3>{{ ch.name }}</h3><span v-if="environment(ch)" class="state-badge" :class="environment(ch).tone">{{ environment(ch).label }}</span></div><button class="hig-switch" role="switch" :aria-checked="ch.enabled" :aria-label="`${t('启用配置', 'Enable configuration')} ${ch.name}`" @click="confirming = ch; error = ''"><span></span></button></div>
    <div class="channel-meta"><div><span>{{ t('参数状态', 'Configuration') }}</span><b :class="ch.configured ? 'text-green' : 'muted'">{{ ch.configured ? t('已配置', 'Configured') : ch.version ? t('部分已保存', 'Partially saved') : t('待配置', 'Not configured') }}</b></div><div><span>{{ ch.integrated ? t('单笔测试上限', 'Test limit') : t('支付接口', 'Payment adapter') }}</span><b>{{ ch.integrated ? `¥ ${c.caps.value?.max_amount || '—'}` : t('尚未接入', 'Not integrated') }}</b></div><div><span>{{ ch.identifierLabel }}</span><code :title="ch.fields[ch.identifier]">{{ ch.fields[ch.identifier] || '—' }}</code></div></div>
    <div v-if="ch.configured" class="channel-card-actions"><span class="channel-storage-note"><Icon name="lock" :size="13" />{{ t('密钥加密保存', 'Encrypted credentials') }}</span><button class="button button-secondary" :disabled="loading || !!loadError" @click="openEditor(ch)"><Icon name="settings" :size="15" />{{ t('修改配置', 'Edit configuration') }}</button><button class="text-link" :disabled="checking === ch.config_id" @click="check(ch)"><Icon v-if="checking === ch.config_id" name="loader" class="spin" :size="13" />{{ t('检查配置', 'Check') }}</button><button class="text-link" :disabled="!ch.integrated" @click="c.navigate('testpay', { config: ch.config_id })">{{ t('测试支付', 'Test payment') }}<Icon name="arrow" :size="14" /></button></div>
    <div v-else class="channel-card-actions"><button class="button button-secondary" :disabled="loading || !!loadError" @click="openEditor(ch)"><Icon name="settings" :size="15" />{{ t('配置渠道', 'Configure channel') }}</button></div>
    <p v-if="checks[ch.config_id]" class="channel-check" :class="checks[ch.config_id].ok ? 'ok' : 'bad'" role="status"><Icon :name="checks[ch.config_id].ok ? 'success' : 'alert'" :size="15" />{{ checks[ch.config_id].text }}</p>
  </article></div>
  <ConfirmDialog :open="!!confirming" :busy="toggling" :title="confirming?.enabled ? t('停用这份配置', 'Disable this configuration') : t('启用这份配置', 'Enable this configuration')" :description="t('仅影响新订单创建，已有订单的查询与回调继续处理', 'Applies to new orders; existing queries and callbacks continue')" @cancel="confirming = false" @confirm="toggle"><p v-if="error" class="error-box" role="alert">{{ error }}</p></ConfirmDialog>
  <dialog ref="dialog" class="console-dialog channel-editor" :aria-label="t('编辑渠道配置', 'Edit channel configuration')" @cancel.prevent="closeEditor" @close="!saving && Object.keys(fields).forEach(key => delete fields[key])">
    <template v-if="editing"><div class="dialog-heading"><div class="channel-editor-title"><span class="brand-small" :class="editing.id"><Brand :name="editing.id" /></span><h2>{{ adding ? t('添加收款配置', 'Add payment configuration') : editing.name }}</h2></div><button class="icon-button" :disabled="saving" :aria-label="t('关闭配置', 'Close configuration')" @click="closeEditor"><Icon name="x" /></button></div>
    <p>{{ ['alipay', 'wechat'].includes(editing.id) ? t('保存后立即用于新订单，已有订单保留原配置用于查询和验签', 'New orders use the saved credentials immediately; existing orders retain their original configuration') : t('可保存和修改接入参数，支付功能将在渠道接口接入后启用', 'Save and edit credentials now; payments become available when the adapter is integrated') }}</p>
    <form @submit.prevent="save"><template v-if="adding"><label for="channel-type">{{ t('收款渠道', 'Payment channel') }}</label><select id="channel-type" v-model="newType" @change="changeType" :disabled="saving"><option v-for="ch in definitions" :key="ch.id" :value="ch.id">{{ ch.name }}</option></select></template><label for="config-name">{{ t('配置名称', 'Configuration name') }}</label><input id="config-name" v-model="configName" required maxlength="64" :disabled="saving" :placeholder="t('例如：支付宝主账户', 'e.g. Alipay primary account')" /><div v-for="field in editorFields" :key="field.key" class="channel-field"><label :for="`channel-${field.key}`">{{ field.label }}<span v-if="field.secret && editing.secrets[field.key]" class="saved-secret"><Icon name="check" :size="12" />{{ t('已保存', 'Saved') }}</span></label><select v-if="field.type === 'env'" :id="`channel-${field.key}`" v-model="fields[field.key]" :disabled="saving"><option value="sandbox">{{ t('沙箱环境', 'Sandbox') }}</option><option value="production">{{ t('正式环境', 'Production') }}</option></select><textarea v-else-if="field.multiline" :id="`channel-${field.key}`" v-model="fields[field.key]" :disabled="saving" rows="3" maxlength="12000" spellcheck="false" autocomplete="off" :aria-invalid="errorField === field.key" :placeholder="editing.secrets[field.key] ? t('留空保留，粘贴新密钥可替换', 'Leave blank to keep; paste a new key to replace') : t('粘贴 PEM 或 Base64 密钥', 'Paste a PEM or Base64 key')"></textarea><input v-else :id="`channel-${field.key}`" v-model="fields[field.key]" :type="field.secret ? 'password' : 'text'" :disabled="saving" :maxlength="field.secret ? 12000 : 256" :autocomplete="field.secret ? 'new-password' : 'off'" spellcheck="false" :aria-invalid="errorField === field.key" :placeholder="field.secret && editing.secrets[field.key] ? t('留空保留，输入新值可替换', 'Leave blank to keep; enter a new value to replace') : field.placeholder || ''" /></div>
      <div v-if="editing.id === 'alipay'" class="channel-callback-address"><Icon name="webhook" :size="16" /><span>{{ t('支付宝异步通知地址', 'Alipay notification URL') }}<code>{{ c.settings.value?.notify_url || t('未配置公网入口，已启用自动查单', 'No public endpoint — automatic verification enabled') }}</code></span></div>
      <div v-if="editing.id === 'wechat'" class="channel-callback-address"><Icon name="webhook" :size="16" /><span>{{ t('微信支付通知地址', 'WeChat Pay notification URL') }}<code>{{ c.settings.value?.origin?.startsWith('https://') ? `${c.settings.value.origin}/payments/notify/${t('订单号', 'order-number')}` : t('需要公网 HTTPS 站点地址', 'A public HTTPS site address is required') }}</code></span></div>
      <p v-if="error" class="error-box" role="alert">{{ errorField ? `${editorFields.find(f => f.key === errorField)?.label || errorField}：` : '' }}{{ error }}</p>
      <div class="dialog-actions"><button type="button" class="button button-secondary" :disabled="saving" @click="closeEditor">{{ t('取消', 'Cancel') }}</button><button class="button" :disabled="saving"><Icon :name="saving ? 'loader' : 'check'" :class="{ spin: saving }" :size="16" />{{ t('保存配置', 'Save configuration') }}</button></div>
    </form></template>
  </dialog>
</template>
