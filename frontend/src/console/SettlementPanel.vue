<script setup>
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorText, t } from '../state'
import { amount, dayOnly, fullDate, time } from './format'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import EmptyState from './EmptyState.vue'
import Segmented from './Segmented.vue'
const c = inject('console'), route = useRoute(), router = useRouter()
const filters = ['pending', 'paid', 'cancel', 'all']
const rows = ref([]), loading = ref(true), error = ref(''), filter = ref(filters.includes(route.query.status) ? route.query.status : ''), selected = ref(null), action = ref(''), reference = ref(''), busy = ref(false), actionError = ref(''), touched = ref(false)
const net = row => row.gross - row.fee
// totals come from the server and cover every settlement; the table shows only the chosen status (latest 200)
const totals = ref({})
const total = (status, pick) => totals.value[status] ? pick(totals.value[status]) : 0
const count = status => total(status, item => item.count), payout = status => total(status, item => item.gross - item.fee), fees = status => total(status, item => item.fee)
const options = computed(() => [{ value: 'pending', label: t('待处理', 'Pending'), count: count('pending') }, { value: 'paid', label: t('已出款', 'Paid'), count: count('paid') },
  { value: 'cancel', label: t('已取消', 'Cancelled'), count: count('cancel') }, { value: 'all', label: t('全部', 'All'), count: count('pending') + count('paid') + count('cancel') }])
const statusName = status => ({ pending: t('待处理', 'Pending'), paid: t('已出款', 'Paid'), cancel: t('已取消', 'Cancelled') })[status] || status
const statusClass = status => ({ pending: 'wait', paid: 'ok', cancel: '' })[status] || ''
const methodName = channel => ({ alipay: t('支付宝', 'Alipay'), bank: t('银行卡', 'Bank card') })[channel] || channel
const referenceError = computed(() => {
  const value = reference.value.trim()
  return value.length < 6 ? t('请填写渠道转账流水号，至少 6 个字符', 'Enter the transfer reference, at least 6 characters') : value.length > 128 ? t('流水号最多 128 个字符', 'The reference can have at most 128 characters') : ''
})
async function load() {
  try {
    const data = await api(`/api/admin/settlements?status=${filter.value || 'pending'}`)
    rows.value = data.settlements; totals.value = data.totals; error.value = ''
  } catch (e) { error.value = errorText(e); if (e.status === 401) c.report(e) }
  finally { loading.value = false }
}
let defaulted = false
async function start() {
  await load()
  // the first view is the work to do, or everything when nothing is waiting
  if (!filter.value) { defaulted = true; filter.value = count('pending') ? 'pending' : 'all' }
}
function open(row, kind) { selected.value = row; action.value = kind; reference.value = ''; touched.value = false; actionError.value = '' }
function close() { if (!busy.value) selected.value = null }
async function apply() {
  if (action.value === 'paid' && referenceError.value) { touched.value = true; return }
  busy.value = true; actionError.value = ''
  try {
    await api(`/api/admin/settlements/${selected.value.id}`, { method: 'POST', body: JSON.stringify({ action: action.value, reference: reference.value.trim() }) })
    c.toast(action.value === 'paid' ? t('已登记出款凭据', 'Payout reference recorded') : t('结算申请已取消', 'Settlement cancelled'))
    selected.value = null; await load()
  } catch (e) { actionError.value = errorText(e); if (e.message === 'settlement_not_pending') await load() }
  finally { busy.value = false }
}
watch(filter, value => {
  if (value && route.query.status !== value) router.replace({ query: { ...route.query, status: value } })
  if (defaulted) defaulted = false  // the list for this status was just loaded
  else load()
})
watch(c.revision, load)
onMounted(start)
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('结算管理', 'Settlements') }}</h2><p>{{ t('完成实际转账后登记凭据，系统不会自动向收款账号转账', 'Record the reference after you transfer the money — the system never pays out by itself') }}</p></div></div>
  <p v-if="error" class="error-box" role="alert">{{ error }}</p>
  <div class="stat-grid">
    <div class="hig-card stat-tile"><span>{{ t('待出款', 'Waiting for payout') }}<Icon name="timer" :size="17" /></span><strong><small>¥</small>{{ amount(payout('pending')) }}</strong><small>{{ t(`${count('pending')} 笔申请`, `${count('pending')} requests`) }}</small></div>
    <div class="hig-card stat-tile"><span>{{ t('已出款', 'Paid out') }}<Icon name="success" :size="17" /></span><strong><small>¥</small>{{ amount(payout('paid')) }}</strong><small>{{ t(`${count('paid')} 笔`, `${count('paid')} settlements`) }}</small></div>
    <div class="hig-card stat-tile"><span>{{ t('结算手续费', 'Settlement fees') }}<Icon name="wallet" :size="17" /></span><strong><small>¥</small>{{ amount(fees('paid')) }}</strong><small>{{ t(`${count('paid')} 笔`, `${count('paid')} settlements`) }}</small></div>
  </div>
  <section class="hig-card admin-card">
    <div class="admin-toolbar"><Segmented v-model="filter" :label="t('结算状态', 'Settlement status')" :options="options" /></div>
    <div v-if="rows.length" class="table-scroll">
      <table class="console-table admin-table settlement-table">
        <thead><tr>
          <th>{{ t('申请时间', 'Requested') }}</th><th>{{ t('商户', 'Merchant') }}</th><th>{{ t('收款账户', 'Payout account') }}</th>
          <th class="num">{{ t('实付金额', 'Payout') }}</th><th>{{ t('状态', 'Status') }}</th><th class="align-right"><span class="action-head-pair">{{ t('操作', 'Actions') }}</span></th>
        </tr></thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td class="date-cell c-time"><span class="when">{{ dayOnly(row.created_at) }}</span><small class="sub">{{ time(row.created_at) }}</small></td>
            <td class="c-merchant"><b class="ellipsis">{{ row.email }}</b><code class="sub ellipsis" translate="no" :title="row.id">{{ row.id }}</code></td>
            <td class="c-account"><div class="party"><span v-if="row.beneficiary.channel === 'alipay'" class="brand-small alipay"><Brand name="alipay" /></span><Icon v-else name="card" :size="18" /><span><b>{{ methodName(row.beneficiary.channel) }} · {{ row.beneficiary.name }}</b><small>{{ row.beneficiary.account }}</small></span></div></td>
            <td class="num c-amount"><b class="strong">¥ {{ amount(net(row)) }}</b><small class="sub">{{ t('申请', 'Requested') }} ¥ {{ amount(row.gross) }}</small><small class="sub">{{ t('手续费', 'Fee') }} ¥ {{ amount(row.fee) }}</small></td>
            <td class="c-status"><span class="state-badge" :class="statusClass(row.status)"><i></i>{{ statusName(row.status) }}</span><code v-if="row.reference" class="sub ellipsis" translate="no" :title="row.reference">{{ row.reference }}</code><small v-if="row.paid_at" class="sub">{{ fullDate(row.paid_at) }}</small></td>
            <td class="align-right c-actions"><div class="action-pair"><span><button v-if="row.status === 'pending'" type="button" class="text-link" @click="open(row, 'paid')">{{ t('登记出款', 'Record payout') }}</button></span><span><button v-if="row.status === 'pending'" type="button" class="text-link danger-link" @click="open(row, 'cancel')">{{ t('取消申请', 'Cancel') }}</button></span></div></td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState v-else icon="wallet" :title="loading ? t('正在读取结算申请…', 'Loading settlements…') : filter === 'pending' ? t('没有待处理的结算申请', 'Nothing is waiting for payout') : t('暂无结算申请', 'No settlements yet')" />
  </section>
  <ConfirmDialog :open="!!selected" :busy="busy" :destructive="action === 'cancel'" :title="action === 'paid' ? t('登记真实出款凭据', 'Record the payout reference') : t('取消结算申请', 'Cancel this settlement')"
    :description="action === 'paid' ? t('请先核对实际转账成功，登记后结算冻结金额会记为已出款', 'Check that the transfer went through. The frozen amount is then recorded as paid out') : t('取消后冻结金额返回商户可用余额', 'The frozen amount returns to the merchant’s available balance')"
    :label="action === 'paid' ? t('确认已出款', 'Confirm payout') : t('确认取消', 'Cancel settlement')" @cancel="close" @confirm="apply">
    <div v-if="selected" class="confirm-summary settlement-summary">
      <div><span>{{ t('商户', 'Merchant') }}</span><b>{{ selected.email }}</b></div>
      <div class="confirm-amount"><b><small>￥</small>{{ amount(net(selected)) }}</b></div>
      <div><span>{{ methodName(selected.beneficiary.channel) }}</span><b>{{ selected.beneficiary.name }}</b><code translate="no">{{ selected.beneficiary.account }}</code></div>
    </div>
    <template v-if="action === 'paid'">
      <label for="settlement-reference">{{ t('渠道转账流水号', 'Transfer reference') }}</label>
      <input id="settlement-reference" v-model="reference" name="reference" minlength="6" maxlength="128" autocomplete="off" spellcheck="false" :aria-invalid="touched && !!referenceError" :aria-describedby="touched && referenceError ? 'settlement-reference-error' : undefined" :disabled="busy" />
      <p v-if="touched && referenceError" id="settlement-reference-error" class="field-error" role="alert">{{ referenceError }}</p>
    </template>
    <p v-if="actionError" class="error-box" role="alert">{{ actionError }}</p>
  </ConfirmDialog>
</template>
