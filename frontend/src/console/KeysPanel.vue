<script setup>
import { inject, onMounted, ref } from 'vue'
import { api, auth, copyText, errorText, t } from '../state'
import Icon from '../components/Icon.vue'
const c = inject('console'), dialog = ref(null), token = ref(''), busy = ref(false), error = ref(''), copied = ref(false)
onMounted(async () => { try { auth.key = (await api('/api/auth/me')).api_key } catch (e) { c.report(e) } })
async function generate() {
  busy.value = true; error.value = ''
  try { const data = await api('/api/keys', { method: 'POST' }); token.value = data.api_key; auth.key = { suffix: token.value.slice(-4) } }
  catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
async function copy() { try { await copyText(token.value); copied.value = true } catch { error.value = t('请选中密钥手动复制。', 'Select and copy the key manually.') } }
function close() { token.value = ''; dialog.value.close() }
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('API 密钥', 'API keys') }}</h2><p>{{ t('从你的服务器安全地连接冰云支付。', 'Securely connect to Pay from your server.') }}</p></div></div>
  <div class="keys-layout"><section class="hig-card key-card"><div class="card-heading"><div><h3>{{ t('我的访问密钥', 'My access key') }}</h3><p>{{ auth.user?.email }}</p></div><span class="key-icon"><Icon name="key" :size="23" /></span></div><div class="key-display"><span>{{ t('密钥', 'Secret key') }}</span><code>{{ auth.key ? `byp_••••••••••••••••••••${auth.key.suffix}` : t('尚未生成 API 密钥', 'No API key generated') }}</code><span class="state-badge" :class="auth.key ? 'delivered' : 'neutral'">{{ auth.key ? t('已配置', 'Configured') : t('未配置', 'Not set') }}</span></div><p class="field-hint">{{ t('完整密钥仅在生成时显示一次。轮换后，旧密钥立即失效。', 'A complete key is shown once. Rotating a key revokes the previous one immediately.') }}</p><button class="button" @click="token = ''; error = ''; copied = false; dialog.showModal()"><Icon name="key" :size="16" />{{ auth.key ? t('轮换 API 密钥', 'Rotate API key') : t('生成 API 密钥', 'Generate API key') }}</button></section><section class="hig-card key-guidance"><Icon name="shield" :size="25" /><h3>{{ t('在服务器上使用', 'Use on your server') }}</h3><p>{{ t('将密钥保存在服务器环境变量 BYPAY_API_KEY 中。浏览器代码、公开仓库和日志都不应包含完整密钥。', 'Store the key in the BYPAY_API_KEY server environment variable. Keep it out of browser code, public repositories, and logs.') }}</p><code>Authorization: Bearer $BYPAY_API_KEY</code><p>{{ t('每个普通用户只能访问自己的订单与回调。', 'Each user can access only their own orders and callbacks.') }}</p></section></div>
  <dialog ref="dialog" class="console-dialog" :aria-label="t('管理 API 密钥', 'Manage API key')" @close="token = ''" @cancel.prevent="!busy && close()"><div class="dialog-heading"><h2>{{ token ? t('保存你的新密钥', 'Save your new key') : auth.key ? t('轮换 API 密钥？', 'Rotate API key?') : t('生成 API 密钥', 'Generate API key') }}</h2><button class="icon-button" :disabled="busy" :aria-label="t('关闭', 'Close')" @click="close"><Icon name="x" /></button></div><p>{{ token ? t('此窗口关闭后将无法再次查看完整密钥。', 'You cannot view the complete key after closing this window.') : auth.key ? t('旧密钥会立即失效，请同步更新使用它的应用。', 'The old key will stop working immediately. Update applications that use it.') : t('为当前账户创建一枚独立的访问密钥。', 'Create an individual access key for this account.') }}</p><p v-if="error" class="error-box" role="alert">{{ error }}</p><template v-if="token"><textarea class="key-secret" :value="token" readonly :aria-label="t('新的 API 密钥', 'New API key')"></textarea><div class="dialog-actions"><button class="button" @click="copy"><Icon :name="copied ? 'check' : 'copy'" :size="16" />{{ copied ? t('已复制', 'Copied') : t('复制密钥', 'Copy key') }}</button></div></template><div v-else class="dialog-actions"><button class="button button-secondary" :disabled="busy" @click="close">{{ t('取消', 'Cancel') }}</button><button class="button" :disabled="busy" @click="generate"><Icon v-if="busy" class="spin" name="loader" :size="16" />{{ t('确认生成', 'Generate key') }}</button></div></dialog>
</template>
