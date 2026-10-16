<script setup>
import { inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, errorText, t } from '../state'
import { useDraft } from './useDraft'
import { fullDate } from './format'
import Icon from '../components/Icon.vue'
import FormGroup from './FormGroup.vue'
import FormRow from './FormRow.vue'
import SaveBar from './SaveBar.vue'
const c = inject('console')
const { draft, dirty, load: setSmtp } = useDraft()
const { draft: template, dirty: templateDirty, load: setTemplate } = useDraft()
const smtpBusy = ref(false), templateBusy = ref(false), testing = ref(false), smtpError = ref(''), templateError = ref(''), testError = ref(''), loadError = ref('')
const event = ref('verification'), language = ref('zh'), applied = { event: 'verification', language: 'zh' }
const recipient = ref(''), preview = ref(''), previewSubject = ref(''), placeholders = ref([]), jobs = ref([]), htmlEditor = ref(null)
const passwordSaved = ref(false), smtpEnabled = ref(false)
let timer, sequence = 0, disposed = false
c.guard(() => dirty.value || templateDirty.value)

const smtpKeys = ['enabled', 'host', 'port', 'security', 'username', 'sender', 'sender_name', 'registration_code', 'notify_registration', 'notify_payments', 'notify_risk', 'version']
const readSmtp = config => ({ ...Object.fromEntries(smtpKeys.map(key => [key, config[key]])), password: '' })
function applySmtp(config) { setSmtp(readSmtp(config)); passwordSaved.value = !!config.password_configured; smtpEnabled.value = !!config.enabled }
async function load() {
  try {
    const data = await api('/api/admin/mail')
    applySmtp(data.config); placeholders.value = data.placeholders; jobs.value = data.jobs; loadError.value = ''
    await loadTemplate()
  } catch (e) { loadError.value = errorText(e) }
}
async function discardSmtp() {
  try { applySmtp((await api('/api/admin/mail')).config); smtpError.value = '' }
  catch (e) { smtpError.value = errorText(e) }
}
async function loadTemplate() {
  try { setTemplate(await api(`/api/admin/mail/template?event=${event.value}&language=${language.value}`)); templateError.value = ''; await render() }
  catch (e) { templateError.value = errorText(e) }
}
async function render(reset = false) {
  const n = ++sequence
  try {
    const data = await api('/api/admin/mail/preview', { method: 'POST', body: JSON.stringify({ event: event.value, language: language.value, template: { ...template }, reset }) })
    if (disposed || n !== sequence) return
    if (reset) Object.assign(template, data.template)  // a draft until it is saved
    preview.value = data.html; previewSubject.value = data.subject
  } catch (e) { if (!disposed) templateError.value = errorText(e) }
}
// Another template replaces the draft, so unsaved edits are only dropped when the administrator agrees
function switchTemplate() {
  if (templateDirty.value && !window.confirm(t('当前模板有未保存的修改，切换后会丢失，继续吗？', 'This template has unsaved changes that will be lost. Continue?'))) { event.value = applied.event; language.value = applied.language; return }
  applied.event = event.value; applied.language = language.value
  loadTemplate()
}
watch(() => [template.subject, template.html], () => { clearTimeout(timer); timer = setTimeout(() => render(), 400) })

async function saveSmtp() {
  if (smtpBusy.value || !dirty.value) return
  smtpBusy.value = true; smtpError.value = ''
  try { applySmtp(await api('/api/admin/mail', { method: 'PUT', body: JSON.stringify({ ...draft }) })); c.toast(t('邮箱配置已保存', 'Email settings saved')) }
  catch (e) { smtpError.value = errorText(e) }
  finally { smtpBusy.value = false }
}
async function saveTemplate() {
  if (templateBusy.value || !templateDirty.value) return
  templateBusy.value = true; templateError.value = ''
  try { await api('/api/admin/mail/template', { method: 'PUT', body: JSON.stringify({ event: event.value, language: language.value, template: { ...template } }) }); setTemplate({ ...template }); c.toast(t('模板已保存', 'Template saved')) }
  catch (e) { templateError.value = errorText(e) }
  finally { templateBusy.value = false }
}
async function sendTest() {
  if (!document.getElementById('mail-recipient').reportValidity()) return
  testing.value = true; testError.value = ''
  try { await api('/api/admin/mail/test', { method: 'POST', body: JSON.stringify({ recipient: recipient.value, template: { ...template }, event: event.value, language: language.value }) }); c.toast(t('测试邮件已提交 SMTP 服务器', 'Test email handed to the SMTP server')) }
  catch (e) { testError.value = errorText(e) }
  finally { testing.value = false }
}
function insert(key) {
  const input = htmlEditor.value, text = `{{${key}}}`, start = input.selectionStart, end = input.selectionEnd
  template.html = template.html.slice(0, start) + text + template.html.slice(end)
  input.focus()
}
const jobStatus = status => ({ pending: t('等待投递', 'Pending'), sent: t('已发送', 'Sent'), failed: t('发送失败', 'Failed') })[status] || status
const jobClass = status => ({ pending: 'wait', sent: 'ok', failed: 'bad' })[status] || ''
onMounted(load)
onBeforeUnmount(() => { disposed = true; clearTimeout(timer) })
</script>
<template>
  <div class="settings-stack">
    <p v-if="loadError" class="error-box" role="alert">{{ loadError }}</p>
    <form class="settings-form" @submit.prevent="saveSmtp">
      <div class="form-groups">
        <FormGroup :title="t('邮箱服务', 'Email service')" :note="t('QQ 邮箱默认 smtp.qq.com · 465 · SSL/TLS，使用邮箱设置中的 SMTP 授权码', 'QQ Mail defaults to smtp.qq.com · 465 · SSL/TLS and uses the SMTP authorization code from its settings')">
          <FormRow field="mail-enabled" :label="t('启用邮件服务', 'Enable email')"><input id="mail-enabled" name="enabled" v-model="draft.enabled" type="checkbox" role="switch" class="form-switch" :disabled="smtpBusy" /></FormRow>
          <FormRow field="mail-host" :label="t('SMTP 服务器', 'SMTP server')"><input id="mail-host" name="host" v-model="draft.host" class="field" placeholder="smtp.qq.com" autocomplete="off" spellcheck="false" required :disabled="smtpBusy" /></FormRow>
          <FormRow field="mail-port" :label="t('端口', 'Port')"><input id="mail-port" name="port" v-model.number="draft.port" class="field" type="number" min="1" max="65535" required :disabled="smtpBusy" /></FormRow>
          <FormRow field="mail-security" :label="t('连接加密', 'Encryption')"><select id="mail-security" name="security" v-model="draft.security" class="field" :disabled="smtpBusy"><option value="ssl">SSL / TLS</option><option value="starttls">STARTTLS</option></select></FormRow>
          <FormRow field="mail-username" :label="t('SMTP 账号', 'SMTP account')"><input id="mail-username" name="username" v-model="draft.username" class="field" autocomplete="off" spellcheck="false" placeholder="your@qq.com" :disabled="smtpBusy" /></FormRow>
          <FormRow field="mail-password" :label="t('SMTP 授权码', 'SMTP authorization code')"><input id="mail-password" name="password" v-model="draft.password" class="field" type="password" autocomplete="new-password" :placeholder="passwordSaved ? t('已保存，留空不修改', 'Saved — leave blank to keep') : t('QQ 邮箱授权码', 'Authorization code')" :disabled="smtpBusy" /></FormRow>
          <FormRow field="mail-sender" :label="t('发件邮箱', 'Sender address')"><input id="mail-sender" name="sender" v-model="draft.sender" class="field" type="email" autocomplete="off" spellcheck="false" placeholder="your@qq.com" :disabled="smtpBusy" /></FormRow>
          <FormRow field="mail-sender_name" :label="t('发件名称', 'Sender name')"><input id="mail-sender_name" name="sender_name" v-model="draft.sender_name" class="field" autocomplete="off" :disabled="smtpBusy" /></FormRow>
        </FormGroup>
        <FormGroup :title="t('邮件通知', 'Notifications')">
          <FormRow field="mail-registration_code" :label="t('注册时验证邮箱', 'Verify email at sign-up')"><input id="mail-registration_code" name="registration_code" v-model="draft.registration_code" type="checkbox" role="switch" class="form-switch" :disabled="smtpBusy" /></FormRow>
          <FormRow field="mail-notify_registration" :label="t('发送注册通知', 'Send sign-up notices')"><input id="mail-notify_registration" name="notify_registration" v-model="draft.notify_registration" type="checkbox" role="switch" class="form-switch" :disabled="smtpBusy" /></FormRow>
          <FormRow field="mail-notify_payments" :label="t('发送支付成功提醒', 'Send payment alerts')"><input id="mail-notify_payments" name="notify_payments" v-model="draft.notify_payments" type="checkbox" role="switch" class="form-switch" :disabled="smtpBusy" /></FormRow>
          <FormRow field="mail-notify_risk" :label="t('发送风控暂停提醒', 'Send risk-hold alerts')"><input id="mail-notify_risk" name="notify_risk" v-model="draft.notify_risk" type="checkbox" role="switch" class="form-switch" :disabled="smtpBusy" /></FormRow>
        </FormGroup>
      </div>
      <SaveBar :dirty="dirty" :busy="smtpBusy" :label="t('保存邮箱配置', 'Save email settings')" :error="smtpError" @discard="discardSmtp" />
    </form>

    <form class="settings-form" novalidate @submit.prevent="saveTemplate">
      <FormGroup :title="t('邮件模板', 'Email template')">
        <div class="admin-toolbar">
          <div class="toolbar-field"><label for="mail-event">{{ t('事件', 'Event') }}</label><select id="mail-event" name="event" v-model="event" class="field" @change="switchTemplate"><option value="verification">{{ t('邮箱验证码', 'Verification code') }}</option><option value="registration">{{ t('注册通知', 'Sign-up notice') }}</option><option value="reminder">{{ t('业务提醒', 'Business reminder') }}</option></select></div>
          <div class="toolbar-field"><label for="mail-language">{{ t('语言', 'Language') }}</label><select id="mail-language" name="language" v-model="language" class="field" @change="switchTemplate"><option value="zh">中文</option><option value="en">English</option></select></div>
          <button type="button" class="button button-secondary push" :disabled="templateBusy" @click="render(true)">{{ t('恢复默认模板', 'Restore default') }}</button>
        </div>
        <div class="mail-workbench">
          <div>
            <label for="mail-subject">{{ t('主题', 'Subject') }}</label>
            <input id="mail-subject" name="subject" v-model="template.subject" class="field" maxlength="200" autocomplete="off" />
            <label for="mail-html">{{ t('HTML 模板', 'HTML template') }}</label>
            <textarea id="mail-html" name="html" ref="htmlEditor" v-model="template.html" class="field mail-code" spellcheck="false" maxlength="60000" :aria-label="t('邮件 HTML 模板', 'Email HTML template')"></textarea>
            <div class="template-tokens"><span>{{ t('可用占位符', 'Placeholders') }}</span><button v-for="key in placeholders" :key="key" type="button" @click="insert(key)">{{ '{' + '{' + key + '}' + '}' }}</button></div>
          </div>
          <div class="mail-preview">
            <header><b>{{ t('实时预览', 'Live preview') }}</b><small>{{ previewSubject }}</small></header>
            <iframe sandbox="" :srcdoc="preview" :title="t('邮件实时预览', 'Email live preview')" referrerpolicy="no-referrer"></iframe>
          </div>
        </div>
        <div class="mail-test">
          <div><label for="mail-recipient">{{ t('测试收件邮箱', 'Test recipient') }}</label><input id="mail-recipient" name="recipient" v-model="recipient" class="field" type="email" required autocomplete="off" spellcheck="false" :placeholder="t('接收测试邮件的地址', 'Address to receive the test email')" @keydown.enter.prevent="sendTest" /></div>
          <button type="button" class="button button-secondary" :disabled="testing || !smtpEnabled" @click="sendTest"><Icon :name="testing ? 'loader' : 'mail'" :class="{ spin: testing }" :size="16" />{{ t('发送测试邮件', 'Send test email') }}</button>
        </div>
        <p v-if="testError" class="error-box" role="alert">{{ testError }}</p>
      </FormGroup>
      <SaveBar :dirty="templateDirty" :busy="templateBusy" :label="t('保存模板', 'Save template')" :error="templateError" @discard="loadTemplate" />
    </form>

    <FormGroup :title="t('最近邮件投递', 'Recent deliveries')">
      <div v-if="jobs.length" class="table-scroll">
        <table class="console-table admin-table">
          <thead><tr><th>{{ t('时间', 'Time') }}</th><th>{{ t('状态', 'Status') }}</th><th class="num">{{ t('尝试次数', 'Attempts') }}</th><th>{{ t('说明', 'Detail') }}</th></tr></thead>
          <tbody><tr v-for="job in jobs" :key="job.id"><td class="date-cell">{{ fullDate(job.created_at) }}</td><td><span class="state-badge" :class="jobClass(job.status)"><i></i>{{ jobStatus(job.status) }}</span></td><td class="num">{{ job.attempts }}</td><td>{{ job.error || '—' }}</td></tr></tbody>
        </table>
      </div>
      <p v-else class="status-row"><span class="status-text"><small>{{ t('暂无投递记录', 'No deliveries yet') }}</small></span></p>
    </FormGroup>
  </div>
</template>
