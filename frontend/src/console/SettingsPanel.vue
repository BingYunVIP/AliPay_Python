<script setup>
import { computed, inject, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api, errorText, t } from '../state'
import Icon from '../components/Icon.vue'

const c = inject('console'), diagnostics = ref(null), checking = ref(false)
const groups = reactive({ access: {}, timing: {}, routing: {}, callback: {} })
const dirty = reactive({}), busy = reactive({}), errors = reactive({}), versions = reactive({})
const savingAny = computed(() => Object.values(busy).some(Boolean))
const keys = {
  access: ['registration', 'max_amount'],
  timing: ['order_timeout_minutes', 'poll_interval_seconds', 'query_window_minutes'],
  routing: ['origin', 'notify_url', 'return_url', 'callback_hosts'],
  callback: ['callback_timeout_seconds', 'callback_first_delay_seconds', 'callback_retry_delays'],
}
function loadGroup(name, value = c.settings.value) {
  if (!value) return
  for (const key of keys[name]) groups[name][key] = Array.isArray(value[key]) ? value[key].join(key === 'callback_hosts' ? '\n' : ', ') : value[key]
  versions[name] = value.version; dirty[name] = false; errors[name] = null
}
watch(c.settings, value => { if (value) for (const name of Object.keys(groups)) if (!dirty[name] && !busy[name]) loadGroup(name, value) }, { immediate: true })
function changed(name) { dirty[name] = true; errors[name] = null }
function fillRoutes() {
  const origin = String(groups.routing.origin || '').replace(/\/$/, '')
  groups.routing.origin = origin
  groups.routing.notify_url = origin.startsWith('https://') ? `${origin}/alipay/notify` : ''
  groups.routing.return_url = `${origin}/alipay/return`
  changed('routing')
}
async function save(name) {
  if (savingAny.value) return
  busy[name] = true; errors[name] = null
  const payload = { ...groups[name], version: versions[name] }
  if (name === 'routing') payload.callback_hosts = String(payload.callback_hosts).split(/[\s,，]+/u).filter(Boolean)
  if (name === 'callback') payload.callback_retry_delays = String(payload.callback_retry_delays).split(/[,，\s]+/u).filter(Boolean).map(Number)
  try {
    const saved = await api('/api/admin/settings', { method: 'PUT', body: JSON.stringify(payload) })
    // Our own update touched only this group; retain drafts in other groups
    // while advancing their version through this known, disjoint change.
    for (const other of Object.keys(groups)) if (other !== name && versions[other] === payload.version) versions[other] = saved.version
    dirty[name] = false
    await c.refresh(true)
    loadGroup(name)
    c.toast(t('设置已保存并生效', 'Settings saved and applied'))
  } catch (e) {
    errors[name] = { message: errorText(e), field: e.field }
    if (e.message === 'settings_conflict') await c.refresh(true)
    await nextTick(); document.getElementById(`settings-error-${name}`)?.focus()
  } finally { busy[name] = false }
}
async function check() {
  checking.value = true
  try { diagnostics.value = await api('/api/admin/diagnostics') }
  catch (e) { c.report(e) }
  finally { checking.value = false }
}
onMounted(check)
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('系统管理', 'System settings') }}</h2><p>{{ t('管理交易规则、站点地址与支付通知', 'Manage payment rules, site addresses, and notifications') }}</p></div></div>
  <div class="settings-grid settings-layout">
    <form class="hig-card settings-card settings-access" @submit.prevent="save('access')" @input="changed('access')">
      <div class="card-heading"><div><h3>{{ t('访问与交易', 'Access & payments') }}</h3><p>{{ t('管理账户注册与单笔限额', 'Account registration and per-order limit') }}</p></div><Icon name="settings" :size="19" /></div>
      <div class="setting-switch-row"><div><b>{{ t('开放用户注册', 'Allow registration') }}</b><p>{{ t('允许新用户创建普通账户', 'Allow new users to create accounts') }}</p></div><button type="button" class="hig-switch" role="switch" :aria-checked="!!groups.access.registration" :aria-label="t('开放用户注册', 'Allow registration')" :disabled="busy.access" @click="groups.access.registration = !groups.access.registration; changed('access')"><span></span></button></div>
      <label for="max-amount">{{ t('单笔测试金额上限（CNY）', 'Maximum test amount (CNY)') }}</label>
      <div class="currency-input"><span>¥</span><input id="max-amount" v-model="groups.access.max_amount" inputmode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" :disabled="busy.access" :aria-invalid="errors.access?.field === 'max_amount'" required /></div>
      <p class="field-hint">{{ t('服务器额度', 'Server ceiling') }} ¥ {{ c.settings.value?.amount_ceiling || '3000.00' }}</p>
      <p v-if="errors.access" id="settings-error-access" tabindex="-1" class="error-box" role="alert">{{ errors.access.message }}</p>
      <div class="settings-save"><button type="button" class="text-link" :disabled="savingAny || !dirty.access" @click="loadGroup('access')">{{ t('重新载入', 'Reload') }}</button><button class="button" :disabled="savingAny || !dirty.access"><Icon :name="busy.access ? 'loader' : 'check'" :class="{ spin: busy.access }" :size="16" />{{ t('保存设置', 'Save settings') }}</button></div>
    </form>

    <section class="hig-card settings-card settings-checks">
      <div class="card-heading"><div><h3>{{ t('支付配置检查', 'Payment configuration') }}</h3><p>{{ t('检查配置与密钥格式', 'Check configuration and key format') }}</p></div><button class="icon-button" :disabled="checking" :aria-label="t('重新检查配置', 'Run configuration checks')" @click="check"><Icon name="refresh" :class="{ spin: checking }" :size="17" /></button></div>
      <ul class="configuration-checks"><li v-for="item in diagnostics?.checks || []" :key="item.name"><Icon :name="item.ok ? 'success' : 'alert'" :class="item.ok ? 'text-green' : 'text-amber'" :size="18" /><span>{{ ({ app_id: 'APP ID', seller_id: t('收款方 UID', 'Seller UID'), app_private_key: t('应用私钥', 'App private key'), alipay_public_key: t('支付宝公钥', 'Alipay public key'), sdk_keys: t('SDK 密钥格式', 'SDK key format') })[item.name] }}</span><b>{{ item.ok ? t('已通过', 'Passed') : t('待配置', 'Needs setup') }}</b></li></ul>
      <div class="context-note"><Icon name="lock" :size="16" /><span>{{ t('付款结果始终经过验签与订单校验', 'Payments always require signature and order verification') }}</span></div>
    </section>

    <form class="hig-card settings-card settings-timing" @submit.prevent="save('timing')" @input="changed('timing')">
      <div class="card-heading"><div><h3>{{ t('订单与支付时间', 'Order & payment timing') }}</h3><p>{{ t('控制付款期限与自动核验频率', 'Payment validity and automatic verification') }}</p></div><Icon name="timer" :size="19" /></div>
      <label for="order-timeout">{{ t('订单有效期', 'Order validity') }}</label><div class="unit-input"><input id="order-timeout" v-model.number="groups.timing.order_timeout_minutes" type="number" min="1" max="1440" step="1" :disabled="busy.timing" :aria-invalid="errors.timing?.field === 'order_timeout_minutes'" required /><span>{{ t('分钟', 'min') }}</span></div>
      <p class="field-hint">{{ t('写入新订单的支付宝付款截止时间，已有订单保持原期限', 'Applies to the Alipay payment deadline for new orders') }}</p>
      <div class="form-grid"><div><label for="poll-interval">{{ t('支付状态查询间隔', 'Query interval') }}</label><div class="unit-input"><input id="poll-interval" v-model.number="groups.timing.poll_interval_seconds" type="number" min="3" max="300" step="1" :disabled="busy.timing" required /><span>{{ t('秒', 'sec') }}</span></div></div><div><label for="query-window">{{ t('支付结果核验时长', 'Verification window') }}</label><div class="unit-input"><input id="query-window" v-model.number="groups.timing.query_window_minutes" type="number" :min="groups.timing.order_timeout_minutes || 1" max="4320" step="1" :disabled="busy.timing" required /><span>{{ t('分钟', 'min') }}</span></div></div></div>
      <p class="field-hint">{{ t('未完成订单会逐步延长查询间隔，核验结束后仍可手动查询', 'Pending queries back off over time; manual verification remains available') }}</p>
      <p v-if="errors.timing" id="settings-error-timing" tabindex="-1" class="error-box" role="alert">{{ errors.timing.message }}</p>
      <div class="settings-save"><button type="button" class="text-link" :disabled="savingAny || !dirty.timing" @click="loadGroup('timing')">{{ t('重新载入', 'Reload') }}</button><button class="button" :disabled="savingAny || !dirty.timing"><Icon :name="busy.timing ? 'loader' : 'check'" :class="{ spin: busy.timing }" :size="16" />{{ t('保存时间设置', 'Save timing') }}</button></div>
    </form>

    <form class="hig-card settings-card notification-settings" @submit.prevent="save('routing')" @input="changed('routing')">
      <div class="card-heading"><div><h3>{{ t('域名与通知', 'Domains & notifications') }}</h3><p>{{ t('维护站点地址和通知入口', 'Site addresses and notification endpoints') }}</p></div><Icon name="globe" :size="19" /></div>
      <label for="site-origin">{{ t('Pay 站点地址', 'Pay site address') }}</label><input id="site-origin" v-model.trim="groups.routing.origin" type="url" placeholder="https://pay.bingyun.vip" :disabled="busy.routing" :aria-invalid="errors.routing?.field === 'origin'" required />
      <div class="routing-fill"><span>{{ t('域名需已解析并配置 HTTPS', 'The domain needs DNS and HTTPS configured') }}</span><button type="button" class="text-link" :disabled="busy.routing" @click="fillRoutes">{{ t('填充默认路径', 'Fill default paths') }}</button></div>
      <label for="alipay-notify">{{ t('支付宝异步通知地址', 'Alipay notification URL') }}</label><input id="alipay-notify" v-model.trim="groups.routing.notify_url" type="url" placeholder="https://pay.bingyun.vip/alipay/notify" :disabled="busy.routing" :aria-invalid="errors.routing?.field === 'notify_url'" />
      <p class="field-hint">{{ t('填写指向 Pay 服务的公网地址，本地无公网入口时可留空', 'A public endpoint for Pay; leave blank when localhost has no public entry') }}</p>
      <label for="alipay-return">{{ t('支付宝同步返回地址', 'Alipay browser return URL') }}</label><input id="alipay-return" v-model.trim="groups.routing.return_url" type="url" placeholder="https://pay.bingyun.vip/alipay/return" :disabled="busy.routing" :aria-invalid="errors.routing?.field === 'return_url'" required />
      <label for="callback-hosts">{{ t('商户回调域名白名单', 'Allowed merchant callback domains') }}</label><textarea id="callback-hosts" v-model="groups.routing.callback_hosts" rows="3" placeholder="test.bingyun.vip" :disabled="busy.routing" :aria-invalid="errors.routing?.field === 'callback_hosts'" spellcheck="false"></textarea>
      <p class="field-hint">{{ t('每行一个域名，不含协议与路径，用于接收 Pay 发出的业务回调', 'One domain per line without scheme or path, for business callbacks sent by Pay') }}</p>
      <p v-if="errors.routing" id="settings-error-routing" tabindex="-1" class="error-box" role="alert">{{ errors.routing.message }}</p>
      <div class="settings-save"><button type="button" class="text-link" :disabled="savingAny || !dirty.routing" @click="loadGroup('routing')">{{ t('重新载入', 'Reload') }}</button><button class="button" :disabled="savingAny || !dirty.routing"><Icon :name="busy.routing ? 'loader' : 'check'" :class="{ spin: busy.routing }" :size="16" />{{ t('保存域名与通知', 'Save routing') }}</button></div>
    </form>

    <form class="hig-card settings-card settings-callback" @submit.prevent="save('callback')" @input="changed('callback')">
      <div class="card-heading"><div><h3>{{ t('支付回调时间', 'Callback timing') }}</h3><p>{{ t('设置 Pay 向商户投递回调的时间规则', 'Timing for payment notifications sent to merchants') }}</p></div><Icon name="webhook" :size="19" /></div>
      <div class="form-grid"><div><label for="callback-first-delay">{{ t('首次回调延迟', 'Initial delay') }}</label><div class="unit-input"><input id="callback-first-delay" v-model.number="groups.callback.callback_first_delay_seconds" type="number" min="0" max="3600" step="1" :disabled="busy.callback" required /><span>{{ t('秒', 'sec') }}</span></div></div><div><label for="callback-timeout">{{ t('回调请求超时', 'Request timeout') }}</label><div class="unit-input"><input id="callback-timeout" v-model.number="groups.callback.callback_timeout_seconds" type="number" min="2" max="30" step="1" :disabled="busy.callback" required /><span>{{ t('秒', 'sec') }}</span></div></div></div>
      <label for="callback-retries">{{ t('失败重试间隔', 'Retry intervals') }}</label><input id="callback-retries" v-model="groups.callback.callback_retry_delays" placeholder="30, 120, 600, 1800, 3600" :disabled="busy.callback" :aria-invalid="errors.callback?.field === 'callback_retry_delays'" />
      <p class="field-hint">{{ t('单位为秒，以逗号分隔，最多 8 次重试，留空表示不重试', 'Seconds separated by commas; up to 8 retries, blank disables retries') }}</p>
      <p v-if="errors.callback" id="settings-error-callback" tabindex="-1" class="error-box" role="alert">{{ errors.callback.message }}</p>
      <div class="settings-save"><button type="button" class="text-link" :disabled="savingAny || !dirty.callback" @click="loadGroup('callback')">{{ t('重新载入', 'Reload') }}</button><button class="button" :disabled="savingAny || !dirty.callback"><Icon :name="busy.callback ? 'loader' : 'check'" :class="{ spin: busy.callback }" :size="16" />{{ t('保存回调设置', 'Save callbacks') }}</button></div>
    </form>
  </div>
</template>
