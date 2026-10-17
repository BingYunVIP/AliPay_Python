<script setup>
import { inject, nextTick, onMounted, ref } from 'vue'
import { api, errorText, t } from '../state'
import { fromLines, toLines, useDraft } from './useDraft'
import FormGroup from './FormGroup.vue'
import FormRow from './FormRow.vue'
import SaveBar from './SaveBar.vue'
import TagInput from './TagInput.vue'
const c = inject('console')
const { draft, dirty, load } = useDraft()
const version = ref(0), busy = ref(false), loadError = ref(''), failure = ref(null), errors = ref({})
c.guard(() => dirty.value)

const yuan = ['元', 'CNY'], percent = ['%', '%'], seconds = ['秒', 'sec'], days = ['天', 'days'], times = ['次', 'times'], orders = ['笔', 'orders']
const bool = (key, label, hint) => ({ key, type: 'bool', label, hint })
const money = (key, label, unit = yuan, hint) => ({ key, type: 'money', label, unit, hint })
const rate = (key, label, hint) => ({ key, type: 'money', label, unit: percent, hint, rate: true })
const count = (key, label, unit, range, hint) => ({ key, type: 'number', label, unit, range, hint })
const pick = (key, label, options) => ({ key, type: 'select', label, options })
const lines = (key, label, hint) => ({ key, type: 'list', label, hint })
const tags = (key, label, hint) => ({ key, type: 'tags', label, hint })
const groups = [
  { title: ['注册准入', 'Registration'], fields: [bool('registration_review', ['开启注册审核', 'Review new merchants']), bool('registration_paid', ['开启注册付费', 'Charge a registration fee']), money('registration_fee', ['注册费用', 'Registration fee'])] },
  { title: ['商品与域名', 'Items & domains'], fields: [tags('blocked_keywords', ['商品屏蔽关键词', 'Blocked item keywords'], ['输入后按回车添加；商品名称包含其中任一个词就会被拦截', 'Press Enter to add; an item is blocked when its name contains any of them']), { key: 'blocked_message', type: 'text', label: ['商品被屏蔽时显示内容', 'Message for blocked items'] },
    count('merchant_domain_limit', ['每个商户可填写的网站域名数量', 'Site domains per merchant'], ['个', 'domains'], [1, 10]),
    bool('domain_add', ['允许商户申请授权支付域名', 'Merchants may request payment domains']), bool('domain_enforce', ['禁止未授权支付域名下单', 'Block orders from unauthorised domains'])] },
  { title: ['手续费与退款', 'Fees & refunds'], fields: [rate('fee_rate', ['平台服务费率', 'Platform fee rate']), money('fee_threshold', ['手续费低于此金额时触发最低扣除', 'Minimum fee applies below']), money('fee_minimum', ['最低扣除金额', 'Minimum fee']),
    bool('merchant_refund', ['开启商户后台自助退款', 'Allow merchant self-service refunds']),
    pick('refund_fee_payer', ['退款服务费承担方', 'Who bears the fee on refunds'], [['platform', ['平台承担，按比例返还服务费', 'Platform — fee refunded pro rata']], ['merchant', ['商户承担，服务费不退', 'Merchant — fee not refunded']]])] },
  { title: ['商户保证金', 'Merchant deposit'], note: ['通过已核验的支付充值，冻结到期且无未决交易后可转为可用余额', 'Paid through a verified payment; released to the available balance after the freeze and with no open transactions'], fields: [bool('deposit_enabled', ['开启商户保证金门槛', 'Require a merchant deposit']), money('deposit_minimum', ['保证金最低充值金额', 'Minimum deposit']), count('deposit_freeze_days', ['保证金最少冻结天数', 'Minimum freeze'], days, [0, 3650])] },
  { title: ['支付风控', 'Payment risk'], fields: [bool('risk_enabled', ['启用支付风控检查', 'Enable risk checks']), count('risk_window_seconds', ['统计窗口', 'Counting window'], seconds, [60, 86400]), count('risk_merchant_count', ['窗口内商户最多请求数', 'Requests per merchant in the window'], times, [1, 10000]),
    count('risk_ip_count', ['窗口内同 IP 最多请求数', 'Requests per IP in the window'], times, [1, 10000]), money('risk_daily_amount', ['商户每日下单限额', 'Daily order limit per merchant'], yuan, ['0 表示不限制', '0 means no limit']),
    count('risk_failure_count', ['成功率检查最少已过期订单数', 'Expired orders before the success-rate check'], orders, [1, 10000]), rate('risk_min_success_rate', ['低于此支付成功率时暂停收款', 'Pause collection below this success rate']),
    count('risk_callback_failures', ['连续回调失败订单数上限', 'Consecutive callback failures allowed'], orders, [1, 10000]), lines('blocked_ips', ['屏蔽 IP / CIDR', 'Blocked IPs / CIDRs'], ['每行一个', 'One per line'])] },
  { title: ['结算规则', 'Settlement rules'], note: ['按北京时间日历日准备结算，自动任务只生成申请，管理员完成实际转账后登记凭据', 'Settlements are prepared by Beijing calendar day. Automatic tasks only create requests; an administrator records the payout after transferring the money'],
    fields: [pick('settlement_mode', ['结算申请方式', 'How requests are made'], [['off', ['关闭', 'Off']], ['manual', ['手动申请', 'Manual']], ['auto', ['自动生成申请', 'Automatic']], ['both', ['手动与自动', 'Manual and automatic']]]),
      count('settlement_days', ['资金可结算等待天数', 'Days before funds can settle'], days, [0, 30], ['0 / 1 为 D+0 / D+1', '0 / 1 is D+0 / D+1']), money('settlement_minimum', ['最低结算金额', 'Minimum settlement']), rate('settlement_rate', ['结算手续费', 'Settlement fee rate']),
      money('settlement_fee_min', ['结算手续费最小', 'Minimum settlement fee']), money('settlement_fee_max', ['结算手续费最大', 'Maximum settlement fee'], yuan, ['0 表示不限制', '0 means no limit']), count('settlement_daily_limit', ['每日最多结算申请次数', 'Requests per day'], times, [1, 100])] },
  { title: ['后台任务开关', 'Background tasks'], fields: [count('jobs_interval_seconds', ['平台任务轮询间隔', 'Task polling interval'], seconds, [5, 3600]), bool('jobs_mail', ['启用邮件投递任务', 'Deliver email']), bool('jobs_risk', ['启用风控巡检任务', 'Run risk patrols']), bool('jobs_settlement', ['启用结算准备任务', 'Prepare settlements'])] },
]
const fields = groups.flatMap(group => group.fields)

const read = data => Object.fromEntries(fields.map(field => [field.key, field.type === 'list' ? toLines(data[field.key]) : field.type === 'tags' ? [...(data[field.key] || [])] : data[field.key]]))
async function reload() {
  try { const data = await api('/api/admin/policy'); version.value = data.version; load(read(data)); loadError.value = ''; failure.value = null; errors.value = {} }
  catch (e) { loadError.value = errorText(e) }
}
onMounted(reload)

// The same limits the server enforces, so a mistake is named next to the field that has it
function problem(field, value) {
  if (field.type === 'money') {
    const text = String(value ?? '').trim()
    if (!/^\d{1,9}(\.\d{1,2})?$/.test(text)) return t('请输入数字，最多两位小数', 'Enter a number with at most two decimals')
    if (field.rate && Number(text) > 100) return t('不能超过 100', 'Cannot exceed 100')
    if (['registration_fee', 'deposit_minimum', 'settlement_minimum'].includes(field.key) && Number(text) < 0.01) return t('不能低于 0.01', 'Cannot be below 0.01')
  }
  if (field.type === 'number' && (!Number.isInteger(value) || value < field.range[0] || value > field.range[1])) return t(`请输入 ${field.range[0]} 到 ${field.range[1]} 的整数`, `Enter a whole number from ${field.range[0]} to ${field.range[1]}`)
  if (field.type === 'text' && !String(value || '').trim()) return t('请填写内容', 'Enter some text')
  return ''
}
async function save() {
  if (busy.value || !dirty.value) return
  errors.value = {}; failure.value = null
  for (const field of fields) { const message = problem(field, draft[field.key]); if (message) errors.value[field.key] = message }
  const first = Object.keys(errors.value)[0]
  if (first) { failure.value = t('请检查标出的设置', 'Check the highlighted settings'); await nextTick(); document.getElementById(`policy-${first}`)?.focus(); return }
  busy.value = true
  const payload = Object.fromEntries(fields.map(field => [field.key, field.type === 'list' ? fromLines(draft[field.key]) : field.type === 'tags' ? [...draft[field.key]] : field.type === 'money' ? String(draft[field.key]).trim() : draft[field.key]]))
  try {
    const saved = await api('/api/admin/policy', { method: 'PUT', body: JSON.stringify({ ...payload, version: version.value }) })
    version.value = saved.version; load(read(saved))
    c.toast(t('平台规则已保存，新订单使用新规则', 'Rules saved — new orders use them'))
  } catch (e) {
    const key = e.message.startsWith('invalid_policy:') ? e.message.split(':')[1] : e.message === 'trusted_proxies_required' ? 'trusted_proxies' : ''
    if (key) { errors.value[key] = e.message === 'trusted_proxies_required' ? t('使用代理头读取 IP 时，需要填写可信代理', 'Trusted proxies are required to read the IP from a header') : t('这一项的格式不正确', 'This value is not valid'); failure.value = t('请检查标出的设置', 'Check the highlighted settings') }
    else failure.value = errorText(e)
  } finally { busy.value = false }
  // a disabled field cannot take focus, so this waits until the form is enabled again
  const invalid = Object.keys(errors.value)[0]
  if (invalid) { await nextTick(); document.getElementById(`policy-${invalid}`)?.focus() }
}
const aria = key => ({ 'aria-invalid': errors.value[key] ? true : null, 'aria-describedby': errors.value[key] ? `policy-${key}-error` : null })
</script>
<template>
  <p v-if="loadError" class="error-box" role="alert">{{ loadError }}</p>
  <form class="settings-form" novalidate @submit.prevent="save">
    <div class="form-groups">
      <FormGroup v-for="group in groups" :key="group.title[0]" :title="t(...group.title)" :note="group.note && t(...group.note)">
        <FormRow v-for="field in group.fields" :key="field.key" :field="`policy-${field.key}`" :label="t(...field.label)" :hint="field.hint && t(...field.hint)" :stacked="field.type === 'list' || field.type === 'text' || field.type === 'tags'" :error="errors[field.key]">
          <input v-if="field.type === 'bool'" :id="`policy-${field.key}`" :name="field.key" v-model="draft[field.key]" type="checkbox" role="switch" class="form-switch" :disabled="busy" />
          <select v-else-if="field.type === 'select'" :id="`policy-${field.key}`" :name="field.key" v-model="draft[field.key]" class="field" :disabled="busy"><option v-for="option in field.options" :key="option[0]" :value="option[0]">{{ t(...option[1]) }}</option></select>
          <TagInput v-else-if="field.type === 'tags'" :id="`policy-${field.key}`" v-model="draft[field.key]" normalize="text" :placeholder="t('输入关键词后按回车', 'Type a keyword and press Enter')" :disabled="busy" />
          <textarea v-else-if="field.type === 'list'" :id="`policy-${field.key}`" :name="field.key" v-model="draft[field.key]" class="field mono" rows="3" autocomplete="off" spellcheck="false" :disabled="busy" v-bind="aria(field.key)"></textarea>
          <input v-else-if="field.type === 'text'" :id="`policy-${field.key}`" :name="field.key" v-model="draft[field.key]" class="field" maxlength="240" autocomplete="off" :disabled="busy" v-bind="aria(field.key)" />
          <div v-else-if="field.type === 'number'" class="unit-field"><input :id="`policy-${field.key}`" :name="field.key" v-model.number="draft[field.key]" class="field" type="number" :min="field.range[0]" :max="field.range[1]" step="1" autocomplete="off" :disabled="busy" v-bind="aria(field.key)" /><span class="unit">{{ t(...field.unit) }}</span></div>
          <div v-else class="unit-field"><input :id="`policy-${field.key}`" :name="field.key" v-model.trim="draft[field.key]" class="field" inputmode="decimal" autocomplete="off" :disabled="busy" v-bind="aria(field.key)" /><span class="unit">{{ t(...field.unit) }}</span></div>
        </FormRow>
      </FormGroup>
    </div>
    <SaveBar :dirty="dirty" :busy="busy" :label="t('保存平台规则', 'Save rules')" :error="failure || ''" @discard="reload" />
  </form>
</template>
