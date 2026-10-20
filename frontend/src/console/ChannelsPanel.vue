<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api, errorText, t } from '../state'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import CryptoNetworkPanel from './CryptoNetworkPanel.vue'

const c = inject('console')
const newType = ref('alipay'), configName = ref(''), configEnabled = ref(false), adding = ref(false)
const records = ref([]), loading = ref(true), loadError = ref(''), confirming = ref(false), toggling = ref(false)
const assets = ref([]), page=ref(1), total=ref(0), deleting=ref(null), deletingBusy=ref(false), networkPanel=ref(null)
const pages=computed(()=>Math.max(1,Math.ceil(total.value/9)))
function identifier(ch){const value=ch.fields[ch.id==='crypto'?'wallet_addresses':ch.identifier];return String(value||'').split(/[\s,]+/).filter(Boolean)[0]||'−'}
async function removeConfig(){deletingBusy.value=true;try{await api(`/api/admin/channel-configs/${deleting.value.config_id}`,{method:'DELETE'});deleting.value=null;await load();if(!records.value.length&&page.value>1)page.value--;c.toast('配置已删除')}catch(e){loadError.value=errorText(e)}finally{deletingBusy.value=false}}
const dialog = ref(null), editing = ref(null), fields = reactive({}), saving = ref(false), error = ref(''), errorField = ref('')
let disposed = false, requestId = 0
const definitions = computed(() => [
  { id: 'alipay', name: t('支付宝', 'Alipay'), identifier: 'app_id', identifierLabel: 'APP ID' },
  { id: 'wechat', name: t('微信支付', 'WeChat Pay'), identifier: 'app_id', identifierLabel: 'APP ID' },
  { id: 'paypal', name: 'PayPal', identifier: 'client_id', identifierLabel: 'Client ID' },
  { id: 'stripe', name: 'Stripe', identifier: 'publishable_key', identifierLabel: t('公开密钥', 'Publishable key') },
  { id: 'crypto', name: 'Crypto', identifier: 'wallet_addresses', identifierLabel: t('钱包地址', 'Wallet address') },
])
const channels = computed(() => records.value.map(ch => ({ ...definitions.value.find(d => d.id === ch.id), ...ch })))
const schemas = computed(() => ({
  alipay: [
    { key: 'env', label: t('运行环境', 'Environment'), type: 'env' },
    { key: 'seller_id', label: t('收款方支付宝 UID', 'Alipay seller UID'), placeholder: t('2088 开头的 16 位 UID', '16-digit UID starting with 2088') },
    { key: 'app_id', label: 'APP ID', placeholder: t('支付宝应用 APP ID', 'Alipay application ID') },
    { key: 'alipay_public_key', label: t('支付宝公钥', 'Alipay public key'), secret: true, multiline: true },
    { key: 'app_private_key', label: t('应用私钥 RSA2', 'App private key RSA2'), secret: true, multiline: true },
  ],
  wechat: [
    { key: 'mch_id', label: t('微信支付商户号', 'WeChat merchant ID') },
    { key: 'app_id', label: 'APP ID', placeholder: 'wx…' },
    { key: 'certificate_serial', label: t('商户证书序列号', 'Merchant certificate serial number') },
    { key: 'platform_public_key_id', label: t('微信支付公钥 ID', 'WeChat Pay public key ID'), placeholder: 'PUB_KEY_ID_…' },
    { key: 'platform_public_key', label: t('微信支付公钥', 'WeChat Pay public key'), secret: true, multiline: true },
    { key: 'api_v3_key', label: t('API v3 密钥', 'API v3 key'), secret: true, placeholder: t('32 个字符', '32 characters') },
    { key: 'merchant_private_key', label: t('商户私钥', 'Merchant private key'), secret: true, multiline: true },
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
  crypto: [
    { key: 'network', label: t('网络', 'Network'), type: 'network', options: ['BSC','TRON','APTOS','ETH','PLASMA','SOL','ARBITRUM','POLYGON','AVAXC'] },
    { key: 'asset', label: t('币种', 'Asset'), type: 'asset', options: fields.network==='TRON'?['USDT']:['USDT','USDC'] },
    { key: 'wallet_addresses', label: t('收款地址钱包池', 'Receiving wallet addresses'), type: 'wallets', placeholder: t('每行填写一个公开收款地址，不要填写私钥或助记词', 'One public receiving address per line. Never enter private keys or seed phrases') },
  ],
}))
const editorFields = computed(() => editing.value ? schemas.value[editing.value.id] : [])
const cryptoConfigName = computed(() => editing.value?.id === 'crypto' && fields.network && fields.asset ? `${fields.network}_${fields.asset}` : '')
const selectedAsset = computed(() => assets.value.find(asset => asset.code === cryptoConfigName.value))
watch([() => fields.network, () => fields.asset], () => { if(fields.network==='TRON')fields.asset='USDT'; if (editing.value?.id === 'crypto' && cryptoConfigName.value) configName.value = cryptoConfigName.value })
const checking = ref(0), checks = reactive({})
// 正式环境徽章只在“配置完整且最近一次检查没有失败”时出现
function environment(channel) {
  if (!channel.configured || checks[channel.config_id]?.ok === false) return null
  const env = channel.fields.env || 'production'
  return env === 'production' ? { tone: 'production', label: t('正式环境', 'Production') } : { tone: 'neutral', label: t('沙箱环境', 'Sandbox') }
}
function checkResult(channel, result) {
  if (channel.id === 'crypto') return result.verified === true
    ? { ok: true, text: t(`${result.network} ${result.asset} 地址与 RPC 检查通过`, `${result.network} ${result.asset} wallet and RPC verified`) }
    : { ok: false, text: errorText(new Error(result.provider_code || 'crypto_check_failed')) }
  if (channel.id === 'alipay') return result.verified===true
    ? { ok: true, text: t('支付宝签名查询与响应验签通过，仍需真实付款验收', 'Signed query verified; a real payment is still required') }
    : { ok: false, text: t(`支付宝检查未通过：${result.provider_code||'请核对商户参数与密钥'}`, `Alipay verification failed: ${result.provider_code||'check credentials'}`) }
  if (channel.id !== 'wechat') return {ok:false,text:t('此渠道仅保留资料，支付功能关闭','Payment is not enabled for this channel')}
  if (!result.authenticated) return { ok: false, text: errorText(new Error(result.code || 'payment_provider_error')) }
  if (result.verified === true) return { ok: true, text: t('商户号、证书序列号、商户私钥和微信支付公钥均验证通过', 'Merchant number, certificate serial, private key and WeChat Pay public key all verified') }
  if (result.verified === false) return { ok: false, text: t('商户信息正确，但微信支付公钥或公钥 ID 不对，请重新下载公钥', 'Merchant details are right, but the WeChat Pay public key or its ID is wrong — download the public key again') }
  return { ok: false, text: t('微信支付没有返回可验证签名，尚不能启用该配置', 'WeChat Pay returned no verifiable signature; this configuration cannot be enabled yet') }
}
async function check(channel) {
  checking.value = channel.config_id; delete checks[channel.config_id]
  try {
    checks[channel.config_id] = checkResult(channel, await api(`/api/admin/channel-configs/${channel.config_id}/check`, { method: 'POST' }))
  } catch (e) { checks[channel.config_id] = { ok: false, text: errorText(e) } }
  finally { checking.value = 0; await load() }
}
async function load() {
  const id = ++requestId
  try {
    const data = await api(`/api/admin/channels?page=${page.value}&page_size=9`)
    if (!disposed && id === requestId) { records.value = data.channels; total.value=data.total; loadError.value = '' }
  } catch (e) { if (!disposed) loadError.value = errorText(e) }
  finally { if (!disposed) loading.value = false }
}
async function openEditor(channel) {
  adding.value = false; editing.value = channel; configName.value = channel.name; configEnabled.value = channel.enabled; error.value = ''; errorField.value = ''
  Object.keys(fields).forEach(key => delete fields[key])
  editorFields.value.forEach(field => { fields[field.key] = field.secret ? '' : channel.fields[field.key] || (field.type === 'env' ? 'sandbox' : field.type === 'network' ? 'BSC' : field.type === 'asset' ? 'USDT' : '') })
  await nextTick(); dialog.value.showModal()
}
function closeEditor() {
  if (saving.value) return
  dialog.value.close(); editing.value = null
  Object.keys(fields).forEach(key => delete fields[key])
}
async function addConfig() {
  adding.value = true; editing.value = { id: newType.value, name: '', fields: {}, secrets: {}, version: 0 }; configName.value = ''; configEnabled.value = false; error.value = ''; errorField.value = ''
  Object.keys(fields).forEach(key => delete fields[key])
  editorFields.value.forEach(field => { fields[field.key] = field.type === 'env' ? 'sandbox' : field.type === 'network' ? 'BSC' : field.type === 'asset' ? 'USDT' : '' })
  await nextTick(); dialog.value.showModal()
}
function changeType() {
  editing.value.id = newType.value
  Object.keys(fields).forEach(key => delete fields[key])
  editorFields.value.forEach(field => { fields[field.key] = field.type === 'env' ? 'sandbox' : field.type === 'network' ? 'BSC' : field.type === 'asset' ? 'USDT' : '' })
}
async function save() {
  saving.value = true; error.value = ''; errorField.value = ''
  try {
    if (editing.value.id === 'crypto' && cryptoConfigName.value) configName.value = cryptoConfigName.value
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
    if (!ch.enabled && (!ch.configured || (ch.integrated && !ch.checked))) throw new Error(ch.integrated && ch.configured ? 'channel_check_required' : 'channel_configuration_incomplete')
    await api(`/api/admin/channel-configs/${ch.config_id}`, { method: 'PUT', body: JSON.stringify({ fields: {}, version: ch.version, revision: ch.revision, enabled: !ch.enabled }) })
    await Promise.all([load(), c.refresh(true)]); confirming.value = false; c.toast(t('配置状态已更新', 'Configuration status updated'))
  } catch (e) { error.value = errorText(e) }
  finally { toggling.value = false }
}
onMounted(load)
onMounted(async()=>{try{assets.value=(await api('/api/admin/crypto-assets')).assets}catch{assets.value=[]}})
watch(c.revision, load)
watch(page,load)
onBeforeUnmount(() => { disposed = true; Object.keys(fields).forEach(key => delete fields[key]) })
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('收款渠道', 'Payment channels') }}</h2><p>{{ t('配置商户参数与密钥，管理收款入口', 'Configure merchant credentials and payment availability') }}</p></div><div class="channel-heading-actions"><button class="button button-secondary" aria-haspopup="dialog" aria-controls="crypto-network-settings" @click="networkPanel.open()"><Icon name="network" :size="16" />{{ t('Crypto 网络设置','Crypto network settings') }}</button><button class="button button-secondary" @click="addConfig"><Icon name="plus" :size="16" />{{ t('添加配置', 'Add configuration') }}</button></div></div>
  <p v-if="loadError" class="error-box" role="alert">{{ loadError }}</p>
  <div class="channel-card-grid" :aria-busy="loading"><article v-for="ch in channels" :key="ch.config_id" class="hig-card payment-channel-card">
    <div class="channel-card-top"><span class="channel-mark" :class="ch.id"><Brand :name="ch.id==='crypto'?`network-${ch.fields.network}`:ch.id"/></span><h3>{{ch.name}}</h3><div class="channel-top-actions"><button class="hig-switch" role="switch" :aria-checked="ch.enabled" :aria-label="`启用配置 ${ch.name}`" :disabled="!ch.integrated||(!ch.enabled&&(!ch.configured||!ch.checked))" @click="confirming=ch"><span></span></button><button type="button" class="icon-button channel-delete" :aria-label="`删除配置 ${ch.name}`" @click="deleting=ch"><Icon name="trash" :size="17"/></button></div></div>
    <div class="channel-status-line"><span v-if="environment(ch)" class="state-badge" :class="environment(ch).tone">{{environment(ch).label}}</span><div class="channel-meta"><span class="state-badge" :class="ch.checked?'production':'neutral'">{{ch.checked?'参数已验证':ch.configured?'参数待验证':'参数未配置'}}</span><button type="button" class="channel-identifier" :aria-label="`${ch.identifierLabel}：${identifier(ch)}`" @click="openEditor(ch)"><small>{{ch.identifierLabel}}</small><code>{{identifier(ch)}}</code></button></div></div>
    <div class="channel-card-actions"><button class="button button-secondary" @click="openEditor(ch)"><Icon name="settings" :size="15"/>{{ch.configured?'修改配置':'配置渠道'}}</button><button v-if="ch.configured&&ch.integrated" class="button button-secondary" :disabled="checking===ch.config_id" @click="check(ch)"><Icon :name="checking===ch.config_id?'loader':'check'" :class="{spin:checking===ch.config_id}" :size="15"/>检查配置</button></div>
    <p v-if="checks[ch.config_id]" class="channel-check" :class="checks[ch.config_id].ok?'ok':'bad'" role="status">{{checks[ch.config_id].text}}</p>
  </article></div>
  <nav v-if="pages>1" class="channel-pagination" aria-label="渠道配置分页"><button class="button button-secondary" :disabled="page<=1" @click="page--">上一页</button><span>{{page}} / {{pages}} · 共 {{total}} 份配置</span><button class="button button-secondary" :disabled="page>=pages" @click="page++">下一页</button></nav>
  <ConfirmDialog :open="!!deleting" :busy="deletingBusy" destructive title="删除收款配置" :description="`确定删除“${deleting?.name||''}”？新订单将无法使用这份配置，相关订单的处理凭据仍保留`" label="确认删除" @cancel="deleting=null" @confirm="removeConfig"/>
  <ConfirmDialog :open="!!confirming" :busy="toggling" :title="confirming?.enabled ? t('停用这份配置', 'Disable this configuration') : t('启用这份配置', 'Enable this configuration')" :description="t('仅影响新订单创建，已有订单的查询与回调继续处理', 'Applies to new orders; existing queries and callbacks continue')" @cancel="confirming = false" @confirm="toggle"><p v-if="error" class="error-box" role="alert">{{ error }}</p></ConfirmDialog>
  <dialog ref="dialog" class="console-dialog channel-editor" :aria-label="t('编辑渠道配置', 'Edit channel configuration')" @cancel.prevent="closeEditor" @close="!saving && Object.keys(fields).forEach(key => delete fields[key])">
    <template v-if="editing"><div class="dialog-heading"><div class="channel-editor-title"><span class="brand-small" :class="editing.id"><Brand :name="editing.id" /></span><h2>{{ adding ? t('添加收款配置', 'Add payment configuration') : editing.name }}</h2></div><button class="icon-button" :disabled="saving" :aria-label="t('关闭配置', 'Close configuration')" @click="closeEditor"><Icon name="x" /></button></div>
    <p>{{ ['alipay', 'wechat'].includes(editing.id) ? t('保存时自动验证，验证通过后启用配置；失败不会覆盖原配置', 'Save validates credentials and enables only after a successful check') : editing.id==='crypto' ? t('只填写公开收款地址。网络 RPC 在 Crypto 网络设置中管理；私钥和助记词不能填写在这里', 'Enter public receiving addresses only. Set RPC endpoints under Crypto network settings. Never enter private keys or seed phrases.') : t('可保存渠道资料，支付功能在独立适配器完成前保持关闭', 'Save channel details; payments stay off until a native adapter is available') }}</p>
    <form @submit.prevent="save"><template v-if="adding"><label for="channel-type">{{ t('收款渠道', 'Payment channel') }}</label><select id="channel-type" v-model="newType" @change="changeType" :disabled="saving"><option v-for="ch in definitions" :key="ch.id" :value="ch.id">{{ ch.name }}</option></select></template><label for="config-name">{{ t('配置名称', 'Configuration name') }}</label><input id="config-name" v-model="configName"  required maxlength="64" :disabled="saving" :placeholder="t('例如：支付宝主账户', 'e.g. Alipay primary account')" /><div v-for="field in editorFields" :key="field.key" class="channel-field"><label :for="`channel-${field.key}`">{{ field.label }}<span v-if="field.secret && editing.secrets[field.key]" class="saved-secret"><Icon name="check" :size="12" />{{ t('已保存', 'Saved') }}</span></label><select v-if="['env','network','asset'].includes(field.type)" :id="`channel-${field.key}`" v-model="fields[field.key]" :disabled="saving"><option v-for="option in field.options || (field.type === 'asset' ? ['USDT','USDC'] : ['sandbox','production'])" :key="option" :value="option">{{ option }}</option></select><textarea v-else-if="field.multiline || field.type === 'wallets'" :id="`channel-${field.key}`" v-model="fields[field.key]" :disabled="saving" :rows="field.type === 'wallets' ? 5 : 3" maxlength="12000" spellcheck="false" autocomplete="off" :aria-invalid="errorField === field.key" :placeholder="field.placeholder || (editing.secrets[field.key] ? t('留空保留，粘贴新密钥可替换', 'Leave blank to keep; paste a new key to replace') : t('粘贴 PEM 或 Base64 密钥', 'Paste a PEM or Base64 key'))"></textarea><input v-else :id="`channel-${field.key}`" v-model="fields[field.key]" :type="field.secret ? 'password' : 'text'" :disabled="saving" :maxlength="field.secret ? 12000 : 256" :autocomplete="field.secret ? 'new-password' : 'off'" spellcheck="false" :aria-invalid="errorField === field.key" :placeholder="field.secret && editing.secrets[field.key] ? t('留空保留，输入新值可替换', 'Leave blank to keep; enter a new value to replace') : field.placeholder || ''" /></div>
      <aside v-if="editing.id==='crypto'&&selectedAsset" class="crypto-asset-identity"><div><b>{{ selectedAsset.identity }}</b><span>{{ t('注册表标识','Registry identity') }} · {{ selectedAsset.issuer }}</span></div><code>{{ selectedAsset.contract }}</code><a :href="selectedAsset.source" target="_blank" rel="noreferrer">{{ t('查看代币来源','View token source') }}<Icon name="arrow" :size="13"/></a></aside>
      <p v-if="error" class="error-box" role="alert">{{ errorField ? `${editorFields.find(f => f.key === errorField)?.label || errorField}：` : '' }}{{ error }}</p>
      <div class="dialog-actions"><button type="button" class="button button-secondary" :disabled="saving" @click="closeEditor">{{ t('取消', 'Cancel') }}</button><button class="button" :disabled="saving"><Icon :name="saving ? 'loader' : 'check'" :class="{ spin: saving }" :size="16" />{{ t('保存配置', 'Save configuration') }}</button></div>
    </form></template>
  </dialog>
  <CryptoNetworkPanel ref="networkPanel" embedded @changed="load"/>
</template>
<style scoped>
.channel-heading-actions,.channel-top-actions,.channel-status-line,.channel-meta,.channel-card-actions{display:flex;align-items:center;gap:8px}.channel-card-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.payment-channel-card{min-width:0;padding:18px;display:flex;flex-direction:column;gap:16px}.channel-card-top{display:flex;align-items:center;gap:10px;min-width:0}.channel-card-top h3{font-size:15px;line-height:1.4;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:0;flex:1}.channel-top-actions{margin-left:auto;flex:none}.channel-delete{color:var(--muted);width:32px;height:32px}.channel-delete:hover{color:#bf3f3f;background:#bf3f3f10}.channel-status-line{flex-wrap:wrap;gap:6px}.channel-meta{flex:1;min-width:0;gap:7px}.channel-identifier{display:flex;align-items:center;gap:5px;border:0;background:none;color:var(--muted);padding:3px 0;min-width:0;cursor:pointer}.channel-identifier small{white-space:nowrap;font-size:10px}.channel-identifier code{min-width:0;overflow:hidden;text-overflow:ellipsis;font-size:11px}.channel-card-actions{margin-top:auto;flex-wrap:wrap}.channel-card-actions .button{font-size:12px;min-height:34px;padding:6px 10px}.channel-pagination{display:flex;align-items:center;justify-content:flex-end;gap:12px;margin-top:16px;font-size:12px}.channel-check{font-size:12px;margin:0}.channel-check.ok{color:#158056}.channel-check.bad{color:#bb3434}@media(max-width:1150px){.channel-card-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){.channel-card-grid{grid-template-columns:minmax(0,1fr)}.channel-heading-actions{flex-wrap:wrap}}
</style>
