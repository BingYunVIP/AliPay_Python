<script setup>
import { computed, inject, nextTick, onMounted, ref, watch } from 'vue'
import { api, errorText, t } from '../state'
import { fromWords, toLines, useDraft } from './useDraft'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import FormGroup from './FormGroup.vue'
import FormRow from './FormRow.vue'
import SaveBar from './SaveBar.vue'
const c = inject('console')
const { draft, dirty, load } = useDraft()
const version = ref(0), busy = ref(false), error = ref(null), diagnostics = ref(null), checking = ref(false)
c.guard(() => dirty.value)

const read = value => ({
  registration: !!value.registration, max_amount: value.max_amount,
  order_timeout_minutes: value.order_timeout_minutes, poll_interval_seconds: value.poll_interval_seconds, query_window_minutes: value.query_window_minutes,
  callback_first_delay_seconds: value.callback_first_delay_seconds, callback_timeout_seconds: value.callback_timeout_seconds, callback_retry_delays: (value.callback_retry_delays || []).join(', '),
  origin: value.origin, notify_url: value.notify_url, return_url: value.return_url, callback_hosts: toLines(value.callback_hosts),
})
function reload() { if (c.settings.value) { load(read(c.settings.value)); version.value = c.settings.value.version; error.value = null } }
// Background refreshes bring in other people's changes, but never overwrite what is being typed
watch(c.settings, () => { if (!dirty.value && !busy.value) reload() }, { immediate: true })

const invalid = key => error.value?.field === key
const message = key => invalid(key) ? error.value.message : ''
const aria = key => ({ 'aria-invalid': invalid(key) || null, 'aria-describedby': invalid(key) ? `site-${key}-error` : null })
function fillRoutes() {
  const origin = String(draft.origin || '').replace(/\/$/, '')
  draft.origin = origin
  draft.notify_url = origin.startsWith('https://') ? `${origin}/alipay/notify` : ''
  draft.return_url = `${origin}/alipay/return`
}
async function save() {
  if (busy.value || !dirty.value) return
  busy.value = true; error.value = null
  const payload = { ...draft, callback_hosts: fromWords(draft.callback_hosts), callback_retry_delays: fromWords(draft.callback_retry_delays).map(Number), version: version.value }
  try {
    await api('/api/admin/settings', { method: 'PUT', body: JSON.stringify(payload) })
    await c.refresh(true)
    reload()
    c.toast(t('设置已保存并生效', 'Settings saved and applied'))
  } catch (e) {
    error.value = { message: errorText(e), field: e.field }
    if (e.message === 'settings_conflict') await c.refresh(true)
  } finally { busy.value = false }
  // a disabled field cannot take focus, so this waits until the form is enabled again
  if (error.value?.field) { await nextTick(); document.getElementById(`site-${error.value.field}`)?.focus() }
}

const fieldNames = computed(() => ({ app_id: 'APP ID', seller_id: t('收款方 UID', 'Seller UID'), app_private_key: t('应用私钥', 'App private key'), alipay_public_key: t('支付宝公钥', 'Alipay public key'), mch_id: t('商户号', 'Merchant number'),
  certificate_serial: t('证书序列号', 'Certificate serial'), api_v3_key: t('APIv3 密钥', 'APIv3 key'), merchant_private_key: t('商户私钥', 'Merchant private key'), platform_public_key: t('微信支付公钥', 'WeChat Pay public key'), platform_public_key_id: t('微信支付公钥 ID', 'WeChat Pay public key ID') }))
// One line per saved payment configuration, with what is still missing
const configurations = computed(() => (diagnostics.value?.configurations || []).map(item => ({ ...item, missing: item.checks.filter(check => !check.ok).map(check => fieldNames.value[check.name] || check.name) })))
async function check() {
  checking.value = true
  try { diagnostics.value = await api('/api/admin/diagnostics') }
  catch (e) { c.report(e) }
  finally { checking.value = false }
}
onMounted(check)
</script>
<template>
  <form class="settings-form" novalidate @submit.prevent="save">
    <div class="form-groups">
      <FormGroup :title="t('支付配置检查', 'Payment configurations')" :note="t('付款结果始终经过验签与订单校验', 'Payments always require signature and order verification')">
        <template #action><button type="button" class="icon-button" :disabled="checking" :aria-label="t('重新检查配置', 'Run configuration checks')" @click="check"><Icon name="refresh" :class="{ spin: checking }" :size="16" /></button></template>
        <div v-for="item in configurations" :key="item.config_id" class="status-row">
          <span class="brand-small" :class="item.id"><Brand :name="item.id" /></span>
          <span class="status-text"><b>{{ item.name }}</b><small v-if="item.missing.length">{{ t('缺少：', 'Missing: ') }}{{ item.missing.join(t('、', ', ')) }}</small></span>
          <span class="state-badge" :class="item.configured ? 'ok' : 'wait'"><i></i>{{ item.configured ? t('已配置', 'Configured') : t('待配置', 'Needs setup') }}</span>
        </div>
        <p v-if="!configurations.length" class="status-row"><span class="status-text"><small>{{ checking ? t('正在检查…', 'Checking…') : t('暂无渠道配置', 'No payment configurations') }}</small></span></p>
      </FormGroup>

      <FormGroup :title="t('访问与交易', 'Access & payments')">
        <FormRow field="site-registration" :label="t('开放用户注册', 'Allow registration')" :hint="t('允许新用户创建普通账户', 'Allow new users to create accounts')"><input id="site-registration" name="registration" v-model="draft.registration" type="checkbox" role="switch" class="form-switch" :disabled="busy" /></FormRow>
        <FormRow field="site-max_amount" :label="t('单笔测试金额上限', 'Maximum test amount')" :hint="`${t('不能超过服务器额度', 'Server ceiling')} ¥ ${c.settings.value?.amount_ceiling || '3000.00'}`" :error="message('max_amount')">
          <div class="unit-field"><input id="site-max_amount" name="max_amount" v-model="draft.max_amount" class="field" inputmode="decimal" autocomplete="off" required :disabled="busy" v-bind="aria('max_amount')" /><span class="unit">{{ t('元', 'CNY') }}</span></div>
        </FormRow>
      </FormGroup>

      <FormGroup :title="t('订单与支付时间', 'Order & payment timing')">
        <FormRow field="site-order_timeout_minutes" :label="t('订单有效期', 'Order validity')" :hint="t('新订单的付款截止时间，已有订单保持原期限', 'Payment deadline for new orders; existing orders keep theirs')" :error="message('order_timeout_minutes')">
          <div class="unit-field"><input id="site-order_timeout_minutes" name="order_timeout_minutes" v-model.number="draft.order_timeout_minutes" class="field" type="number" min="1" max="1440" step="1" required :disabled="busy" v-bind="aria('order_timeout_minutes')" /><span class="unit">{{ t('分钟', 'min') }}</span></div>
        </FormRow>
        <FormRow field="site-poll_interval_seconds" :label="t('支付状态查询间隔', 'Query interval')" :error="message('poll_interval_seconds')">
          <div class="unit-field"><input id="site-poll_interval_seconds" name="poll_interval_seconds" v-model.number="draft.poll_interval_seconds" class="field" type="number" min="3" max="300" step="1" required :disabled="busy" v-bind="aria('poll_interval_seconds')" /><span class="unit">{{ t('秒', 'sec') }}</span></div>
        </FormRow>
        <FormRow field="site-query_window_minutes" :label="t('支付结果核验时长', 'Verification window')" :hint="t('未完成订单会逐步延长查询间隔，核验结束后仍可手动查询', 'Pending orders are queried less often over time; manual queries stay available')" :error="message('query_window_minutes')">
          <div class="unit-field"><input id="site-query_window_minutes" name="query_window_minutes" v-model.number="draft.query_window_minutes" class="field" type="number" :min="draft.order_timeout_minutes || 1" max="4320" step="1" required :disabled="busy" v-bind="aria('query_window_minutes')" /><span class="unit">{{ t('分钟', 'min') }}</span></div>
        </FormRow>
      </FormGroup>

      <FormGroup :title="t('支付回调时间', 'Callback timing')">
        <FormRow field="site-callback_first_delay_seconds" :label="t('首次回调延迟', 'Initial delay')" :error="message('callback_first_delay_seconds')">
          <div class="unit-field"><input id="site-callback_first_delay_seconds" name="callback_first_delay_seconds" v-model.number="draft.callback_first_delay_seconds" class="field" type="number" min="0" max="3600" step="1" required :disabled="busy" v-bind="aria('callback_first_delay_seconds')" /><span class="unit">{{ t('秒', 'sec') }}</span></div>
        </FormRow>
        <FormRow field="site-callback_timeout_seconds" :label="t('回调请求超时', 'Request timeout')" :error="message('callback_timeout_seconds')">
          <div class="unit-field"><input id="site-callback_timeout_seconds" name="callback_timeout_seconds" v-model.number="draft.callback_timeout_seconds" class="field" type="number" min="2" max="30" step="1" required :disabled="busy" v-bind="aria('callback_timeout_seconds')" /><span class="unit">{{ t('秒', 'sec') }}</span></div>
        </FormRow>
        <FormRow field="site-callback_retry_delays" stacked :label="t('失败重试间隔', 'Retry intervals')" :hint="t('单位为秒，以逗号分隔，最多 8 次重试，留空表示不重试', 'Seconds separated by commas; up to 8 retries, blank disables retries')" :error="message('callback_retry_delays')">
          <input id="site-callback_retry_delays" name="callback_retry_delays" v-model="draft.callback_retry_delays" class="field" placeholder="30, 120, 600, 1800, 3600" autocomplete="off" spellcheck="false" :disabled="busy" v-bind="aria('callback_retry_delays')" />
        </FormRow>
      </FormGroup>

      <FormGroup :title="t('域名与通知', 'Domains & notifications')">
        <template #action><button type="button" class="text-link" :disabled="busy" @click="fillRoutes">{{ t('填充默认路径', 'Fill default paths') }}</button></template>
        <FormRow field="site-origin" stacked :label="t('Pay 站点地址', 'Pay site address')" :hint="t('域名需已解析并配置 HTTPS', 'The domain needs DNS and HTTPS configured')" :error="message('origin')">
          <input id="site-origin" name="origin" v-model.trim="draft.origin" class="field" type="url" placeholder="https://pay.bingyun.vip" autocomplete="off" spellcheck="false" required :disabled="busy" v-bind="aria('origin')" />
        </FormRow>
        <FormRow field="site-notify_url" stacked :label="t('支付宝异步通知地址', 'Alipay notification URL')" :hint="t('填写指向 Pay 服务的公网地址，本地无公网入口时可留空', 'A public endpoint for Pay; leave blank when localhost has no public entry')" :error="message('notify_url')">
          <input id="site-notify_url" name="notify_url" v-model.trim="draft.notify_url" class="field" type="url" placeholder="https://pay.bingyun.vip/alipay/notify" autocomplete="off" spellcheck="false" :disabled="busy" v-bind="aria('notify_url')" />
        </FormRow>
        <FormRow field="site-return_url" stacked :label="t('支付宝同步返回地址', 'Alipay browser return URL')" :error="message('return_url')">
          <input id="site-return_url" name="return_url" v-model.trim="draft.return_url" class="field" type="url" placeholder="https://pay.bingyun.vip/alipay/return" autocomplete="off" spellcheck="false" required :disabled="busy" v-bind="aria('return_url')" />
        </FormRow>
        <FormRow field="site-callback_hosts" stacked :label="t('商户回调域名白名单', 'Allowed merchant callback domains')" :hint="t('每行一个域名，不含协议与路径，用于接收 Pay 发出的业务回调', 'One domain per line without scheme or path, for business callbacks sent by Pay')" :error="message('callback_hosts')">
          <textarea id="site-callback_hosts" name="callback_hosts" v-model="draft.callback_hosts" class="field mono" rows="3" placeholder="test.bingyun.vip" autocomplete="off" spellcheck="false" :disabled="busy" v-bind="aria('callback_hosts')"></textarea>
        </FormRow>
      </FormGroup>
    </div>
    <SaveBar :dirty="dirty" :busy="busy" :label="t('保存设置', 'Save settings')" :error="error ? (error.field ? t('请检查标出的设置', 'Check the highlighted setting') : error.message) : ''" @discard="reload" />
  </form>
</template>
