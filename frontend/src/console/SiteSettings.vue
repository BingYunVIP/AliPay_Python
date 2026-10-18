<script setup>
import { inject, nextTick, ref, watch } from 'vue'
import { api, errorText, t } from '../state'
import { useDraft } from './useDraft'
import FormGroup from './FormGroup.vue'
import FormRow from './FormRow.vue'
import SaveBar from './SaveBar.vue'
import TagInput from './TagInput.vue'
const c = inject('console')
const { draft, dirty, load } = useDraft()
const version = ref(0), busy = ref(false), error = ref(null)
let hydrating = false
c.guard(() => dirty.value)

const read = value => ({
  registration: !!value.registration, max_amount: value.max_amount,
  order_timeout_minutes: value.order_timeout_minutes, poll_interval_seconds: value.poll_interval_seconds, query_window_minutes: value.query_window_minutes,
  callback_first_delay_seconds: value.callback_first_delay_seconds, callback_timeout_seconds: value.callback_timeout_seconds, callback_retry_interval_seconds: value.callback_retry_interval_seconds,
  origin: value.origin, notify_url: value.notify_url, return_url: value.return_url,
  callback_hosts: [...(value.callback_hosts || [])], email_domains: [...(value.email_domains || [])],
})
function reload() {
  if (!c.settings.value) return
  hydrating = true
  load(read(c.settings.value)); version.value = c.settings.value.version; error.value = null
  nextTick(() => { hydrating = false })
}
// Background refreshes bring in other people's changes, but never overwrite what is being typed
watch(c.settings, () => { if (!dirty.value && !busy.value) reload() }, { immediate: true })
// 只填站点地址：异步通知与同步返回地址按固定路径自动带出
watch(() => draft.origin, value => {
  if (hydrating) return
  const origin = String(value || '').trim().replace(/\/$/, '')
  draft.origin = origin
  draft.notify_url = origin.startsWith('https://') ? `${origin}/alipay/notify` : ''
  draft.return_url = origin ? `${origin}/alipay/return` : ''
})

const invalid = key => error.value?.field === key
const message = key => invalid(key) ? error.value.message : ''
const aria = key => ({ 'aria-invalid': invalid(key) || null, 'aria-describedby': invalid(key) ? `site-${key}-error` : null })
async function save() {
  if (busy.value || !dirty.value) return
  busy.value = true; error.value = null
  const payload = { ...draft, version: version.value }
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
</script>
<template>
  <form class="settings-form" novalidate @submit.prevent="save">
    <div class="form-groups">
      <FormGroup :title="t('访问与交易', 'Access & payments')">
        <FormRow field="site-registration" :label="t('开放用户注册', 'Allow registration')" :hint="t('允许新用户创建普通账户', 'Allow new users to create accounts')"><input id="site-registration" name="registration" v-model="draft.registration" type="checkbox" role="switch" class="form-switch" :disabled="busy" /></FormRow>
        <FormRow field="site-email_domains" stacked :label="t('邮箱域名白名单', 'Allowed email domains')" :hint="t('只接受这些域名的邮箱注册，输入后按回车添加；支持 *.edu.cn 这类通配', 'Only these domains may sign up. Press Enter to add one; wildcards such as *.edu.cn are supported')" :error="message('email_domains')">
          <TagInput id="site-email_domains" v-model="draft.email_domains" normalize="domain" :placeholder="t('@qq.com、*.edu.cn', '@qq.com, *.edu.cn')" :disabled="busy" />
        </FormRow>
        <FormRow field="site-max_amount" :label="t('单笔金额上限', 'Maximum amount per order')" :hint="`${t('对测试支付和商户接口同时生效，不能超过服务器额度', 'Applies to test payments and the merchant API; capped by the server ceiling')} ¥ ${c.settings.value?.amount_ceiling || '3000.00'}`" :error="message('max_amount')">
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
        <FormRow field="site-callback_retry_interval_seconds" :label="t('失败重试间隔', 'Retry interval')" :hint="t('失败后每隔这么久重试一次，最多 8 次', 'Retry this often after a failure, up to 8 times')" :error="message('callback_retry_interval_seconds')">
          <div class="unit-field"><span class="unit unit-prefix">{{ t('每', 'every') }}</span><input id="site-callback_retry_interval_seconds" name="callback_retry_interval_seconds" v-model.number="draft.callback_retry_interval_seconds" class="field has-prefix" type="number" min="5" max="86400" step="1" required :disabled="busy" v-bind="aria('callback_retry_interval_seconds')" /><span class="unit">{{ t('秒', 'sec') }}</span></div>
        </FormRow>
      </FormGroup>

      <FormGroup :title="t('域名与通知', 'Domains & notifications')">
        <FormRow field="site-origin" stacked :label="t('Pay 站点地址', 'Pay site address')" :hint="t('域名需已解析并配置 HTTPS；下面两个地址会按站点地址自动填写', 'The domain needs DNS and HTTPS; the two addresses below follow it automatically')" :error="message('origin')">
          <input id="site-origin" name="origin" v-model.trim="draft.origin" class="field" type="url" placeholder="https://pay.bingyun.vip" autocomplete="off" spellcheck="false" required :disabled="busy" v-bind="aria('origin')" />
        </FormRow>
        <FormRow field="site-notify_url" stacked :label="t('异步通知地址', 'Asynchronous notification URL')" :hint="t('由站点地址自动填写；本地没有公网入口时留空', 'Filled in from the site address; left blank when there is no public entry')" :error="message('notify_url')">
          <input id="site-notify_url" name="notify_url" v-model.trim="draft.notify_url" class="field" type="url" placeholder="https://pay.bingyun.vip/alipay/notify" autocomplete="off" spellcheck="false" :disabled="busy" v-bind="aria('notify_url')" />
        </FormRow>
        <FormRow field="site-return_url" stacked :label="t('同步返回地址', 'Browser return URL')" :hint="t('由站点地址自动填写', 'Filled in from the site address')" :error="message('return_url')">
          <input id="site-return_url" name="return_url" v-model.trim="draft.return_url" class="field" type="url" placeholder="https://pay.bingyun.vip/alipay/return" autocomplete="off" spellcheck="false" required :disabled="busy" v-bind="aria('return_url')" />
        </FormRow>
        <FormRow field="site-callback_hosts" stacked :label="t('商户回调域名白名单', 'Allowed merchant callback domains')" :hint="t('输入域名后按回车添加，自动去掉 http(s):// 与路径；用于接收 Pay 发出的业务回调', 'Press Enter to add a domain; scheme and path are removed. Pay only calls these hosts')" :error="message('callback_hosts')">
          <TagInput id="site-callback_hosts" v-model="draft.callback_hosts" normalize="domain" linkable :placeholder="t('test.bingyun.vip', 'test.bingyun.vip')" :disabled="busy" />
        </FormRow>
      </FormGroup>
    </div>
    <SaveBar :dirty="dirty" :busy="busy" :label="t('保存设置', 'Save settings')" :error="error ? (error.field ? t('请检查标出的设置', 'Check the highlighted setting') : error.message) : ''" @discard="reload" />
  </form>
</template>
