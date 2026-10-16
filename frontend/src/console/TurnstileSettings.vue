<script setup>
import { inject, nextTick, onMounted, ref } from 'vue'
import { api, errorText, t } from '../state'
import { fromWords, toLines, useDraft } from './useDraft'
import FormGroup from './FormGroup.vue'
import FormRow from './FormRow.vue'
import SaveBar from './SaveBar.vue'
const c = inject('console')
const { draft, dirty, load } = useDraft()
const busy = ref(false), error = ref(''), hostError = ref(''), secretSaved = ref(false)
c.guard(() => dirty.value)
function apply(config) { load({ enabled: !!config.enabled, site_key: config.site_key || '', secret_key: '', hostnames: toLines(config.hostnames), version: config.version }); secretSaved.value = !!config.secret_configured }
async function reload() {
  try { apply(await api('/api/admin/turnstile')); error.value = '' }
  catch (e) { error.value = errorText(e) }
}
async function save() {
  if (busy.value || !dirty.value) return
  busy.value = true; error.value = ''; hostError.value = ''
  try {
    apply(await api('/api/admin/turnstile', { method: 'PUT', body: JSON.stringify({ enabled: draft.enabled, site_key: draft.site_key, secret_key: draft.secret_key, hostnames: fromWords(draft.hostnames), version: draft.version }) }))
    c.toast(t('Turnstile 配置已保存', 'Turnstile settings saved'))
  } catch (e) { if (e.message === 'invalid_turnstile_hostnames') { hostError.value = errorText(e); error.value = t('请检查标出的设置', 'Check the highlighted setting') } else error.value = errorText(e) }
  finally { busy.value = false }
  if (hostError.value) { await nextTick(); document.getElementById('human-hostnames')?.focus() }
}
onMounted(reload)
</script>
<template>
  <form class="settings-form" @submit.prevent="save">
    <div class="form-groups">
      <FormGroup title="Cloudflare Turnstile" :note="t('启用后登录、注册和发送邮箱验证码都经过服务端验证，请将本地开发域名也加入 Cloudflare 配置', 'Once enabled, sign-in, sign-up and email codes are verified on the server. Add local development domains to the Cloudflare configuration too')">
        <FormRow field="human-enabled" :label="t('启用人机验证', 'Enable human check')"><input id="human-enabled" name="enabled" v-model="draft.enabled" type="checkbox" role="switch" class="form-switch" :disabled="busy" /></FormRow>
        <FormRow field="human-site_key" label="Site Key"><input id="human-site_key" name="site_key" v-model="draft.site_key" class="field" autocomplete="off" spellcheck="false" :disabled="busy" /></FormRow>
        <FormRow field="human-secret_key" label="Secret Key"><input id="human-secret_key" name="secret_key" v-model="draft.secret_key" class="field" type="password" autocomplete="new-password" :placeholder="secretSaved ? t('已保存，留空不修改', 'Saved — leave blank to keep') : t('填写 Secret Key', 'Enter the Secret Key')" :disabled="busy" /></FormRow>
        <FormRow field="human-hostnames" stacked :label="t('允许验证的域名', 'Allowed domains')" :hint="t('每行一个，不含协议和端口', 'One per line, without scheme or port')" :error="hostError"><textarea id="human-hostnames" name="hostnames" v-model="draft.hostnames" class="field mono" rows="4" placeholder="pay.bingyun.vip&#10;localhost&#10;127.0.0.1" autocomplete="off" spellcheck="false" :disabled="busy" :aria-invalid="hostError ? true : null" :aria-describedby="hostError ? 'human-hostnames-error' : null"></textarea></FormRow>
      </FormGroup>
    </div>
    <SaveBar :dirty="dirty" :busy="busy" :label="t('保存配置', 'Save settings')" :error="error" @discard="reload" />
  </form>
</template>
