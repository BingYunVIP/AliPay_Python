<script setup>
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, auth, t } from '../state'
import { usePaged } from './useConsole'
import { fullDate, productName, status } from './format'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import EmptyState from './EmptyState.vue'
import Pagination from './Pagination.vue'
import OrderActions from './OrderActions.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const LIMIT = 1000
const c = inject('console'), route = useRoute()
const search = ref(String(route.query.q || '')), searchValue = ref(search.value)
const state = ref('all'), channel = ref('all'), product = ref('all'), deleted = ref('0'), period = ref('all')
const selected = ref(new Set()), selecting = ref(false), exporting = ref(false), confirming = ref(false), acting = ref(false)
const channelNames = computed(() => ({ alipay: t('支付宝', 'Alipay'), wechat: t('微信支付', 'WeChat Pay'), paypal: 'PayPal', stripe: 'Stripe' }))
const rows = usePaged('/api/console/orders', 'orders', () => ({ search: search.value, status: state.value, period: period.value, channel: channel.value, product: product.value, deleted: deleted.value }), c)
const filters = computed(() => new URLSearchParams({ search: search.value, status: state.value, period: period.value, channel: channel.value, product: product.value, deleted: deleted.value }).toString())
const count = computed(() => selected.value.size)
const pageNumbers = computed(() => rows.data.value.map(order => order.out_trade_no))
const pageSelected = computed(() => pageNumbers.value.length > 0 && pageNumbers.value.every(number => selected.value.has(number)))
const pageSome = computed(() => !pageSelected.value && pageNumbers.value.some(number => selected.value.has(number)))
let debounce
onBeforeUnmount(() => clearTimeout(debounce))
watch(searchValue, value => { clearTimeout(debounce); debounce = setTimeout(() => { search.value = value }, 250) })
watch(() => route.query.q, value => { searchValue.value = String(value || ''); search.value = searchValue.value })
// A selection is a list of order numbers. Searching, filtering and paging never change it;
// only the buttons below do, and switching between active and deleted orders starts over.
watch(deleted, clearSelection)
function clearSelection() { selected.value = new Set() }
function add(numbers) {
  const next = new Set(selected.value)
  for (const number of numbers) {
    if (next.size >= LIMIT && !next.has(number)) { c.toast(t(`一次最多选择 ${LIMIT} 笔订单`, `You can select up to ${LIMIT} orders at once`)); break }
    next.add(number)
  }
  selected.value = next
}
function toggle(number) { const next = new Set(selected.value); if (!next.delete(number)) next.add(number); selected.value = next }
function togglePage(checked) {
  if (checked) { add(pageNumbers.value); return }
  const next = new Set(selected.value); pageNumbers.value.forEach(number => next.delete(number)); selected.value = next
}
async function selectAll() {
  selecting.value = true
  try { const data = await api(`/api/console/orders/ids?${filters.value}`); add(data.ids); if (data.limited) c.toast(t(`匹配订单超过 ${LIMIT} 笔，已选择前 ${LIMIT} 笔`, `More than ${LIMIT} orders match; the first ${LIMIT} are selected`)) }
  catch (e) { c.report(e) }
  finally { selecting.value = false }
}
async function remove() {
  acting.value = true
  try {
    const data = await api(`/api/console/orders/batch?deleted=${deleted.value}`, { method: 'POST', body: JSON.stringify({ ids: [...selected.value], action: deleted.value === '1' ? 'restore' : 'delete' }) })
    c.toast(t(`已处理 ${data.changed} 笔${data.skipped.length ? `，${data.skipped.length} 笔退款中订单已跳过` : ''}`, `${data.changed} orders processed${data.skipped.length ? `, ${data.skipped.length} refunding orders skipped` : ''}`))
    clearSelection(); confirming.value = false; await rows.load()
  } catch (e) { c.report(e); confirming.value = false }
  finally { acting.value = false }
}
async function exportSelected() {
  exporting.value = true
  try {
    const response = await fetch(`/api/console/orders/export?deleted=${deleted.value}`, { method: 'POST', credentials: 'same-origin', signal: AbortSignal.timeout(20000), headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': auth.csrf }, body: JSON.stringify({ ids: [...selected.value] }) })
    if (!response.ok) { const data = await response.json(); throw Object.assign(new Error(data.error), { status: response.status }) }
    const url = URL.createObjectURL(await response.blob()), link = document.createElement('a')
    link.href = url; link.download = `pay-orders-${new Date().toISOString().slice(0, 10)}.csv`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    c.toast(t('订单已导出', 'Orders exported'))
  } catch (e) { c.report(e) }
  finally { exporting.value = false }
}
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('订单记录', 'Orders') }}</h2><p>{{ t('查询交易状态，追踪每一笔收款', 'Track your payments and inspect each transaction') }}</p></div><button class="button" @click="c.navigate('testpay')"><Icon name="plus" :size="16" />{{ t('创建订单', 'Create order') }}</button></div>
  <section class="hig-card orders-card">
    <div class="table-toolbar">
      <select v-model="state" class="order-filter-select" :aria-label="t('交易状态', 'Payment status')"><option v-for="value in ['all', 'CREATED', 'PAID', 'EXPIRED', 'CLOSED', 'REFUNDING', 'PARTIALLY_REFUNDED', 'REFUNDED']" :key="value" :value="value">{{ value === 'all' ? t('全部状态', 'All statuses') : status(value) }}</option></select>
      <select v-model="channel" class="order-filter-select" :aria-label="t('收款渠道筛选', 'Filter by channel')"><option value="all">{{ t('全部渠道', 'All channels') }}</option><option v-for="(name, key) in channelNames" :key="key" :value="key">{{ name }}</option></select>
      <select v-model="product" class="order-filter-select" :aria-label="t('支付场景筛选', 'Filter by product')"><option value="all">{{ t('全部场景', 'All products') }}</option><option v-for="p in ['page', 'wap', 'app', 'mini']" :key="p" :value="p">{{ productName(p) }}</option></select>
      <select v-model="deleted" class="order-filter-select" :aria-label="t('订单列表范围', 'Order list scope')"><option value="0">{{ t('正常订单', 'Active orders') }}</option><option value="1">{{ t('已删除', 'Deleted') }}</option></select>
      <select v-model="period" class="order-filter-select" :aria-label="t('订单时间', 'Order period')"><option value="all">{{ t('全部时间', 'All time') }}</option><option value="today">{{ t('今日', 'Today') }}</option><option value="7d">{{ t('近 7 天', 'Last 7 days') }}</option><option value="30d">{{ t('近 30 天', 'Last 30 days') }}</option></select>
    </div>
    <div class="table-filter-row">
      <button type="button" class="text-link" :disabled="selecting || !rows.total.value" @click="selectAll"><Icon v-if="selecting" name="loader" class="spin" :size="13" />{{ t('全选', 'Select all') }}</button>
      <button type="button" class="text-link" :disabled="!count" @click="clearSelection">{{ t('不选', 'Clear') }}</button>
      <span class="selected-count" role="status">{{ t(`已选 ${count} 笔`, `${count} selected`) }}</span>
      <label class="console-search"><Icon name="search" :size="17" /><input v-model="searchValue" :placeholder="t('搜索订单号、商品或交易号', 'Search order, item, or trade number')" :aria-label="t('搜索订单', 'Search orders')" /></label>
      <button type="button" class="button button-secondary" :disabled="!count || acting" @click="confirming = true">{{ deleted === '1' ? t('恢复选中', 'Restore selected') : t('删除选中', 'Delete selected') }}</button>
      <button type="button" class="button button-secondary" :disabled="!count || exporting" @click="exportSelected"><Icon v-if="exporting" name="loader" class="spin" :size="15" />{{ t('导出选择 CSV', 'Export selected CSV') }}</button>
    </div>
    <p v-if="rows.error.value" class="error-box" role="alert">{{ rows.error.value }}</p>
    <div v-if="rows.data.value.length" class="table-scroll">
      <table class="console-table orders-table">
        <thead><tr>
          <th class="check-cell"><input type="checkbox" :aria-label="t('选择本页订单', 'Select orders on this page')" :checked="pageSelected" :indeterminate.prop="pageSome" @change="togglePage($event.target.checked)" /></th>
          <th>{{ t('商品 / 订单号', 'Item / Order') }}</th><th>{{ t('渠道 / 产品', 'Channel / Product') }}</th><th>{{ t('实付金额', 'Amount') }}</th><th>{{ t('交易状态', 'Status') }}</th><th>{{ t('创建时间', 'Created at') }}</th><th>{{ t('支付时间', 'Paid at') }}</th><th class="align-right"><span class="action-head">{{ t('操作', 'Actions') }}</span></th>
        </tr></thead>
        <tbody>
          <tr v-for="order in rows.data.value" :key="order.out_trade_no">
            <td class="check-cell"><input type="checkbox" :aria-label="t(`选择订单 ${order.out_trade_no}`, `Select order ${order.out_trade_no}`)" :checked="selected.has(order.out_trade_no)" @change="toggle(order.out_trade_no)" /></td>
            <td><button type="button" class="order-name" @click="c.selectedOrder.value = order">{{ order.subject }}</button><code>{{ order.out_trade_no }}</code></td>
            <td><span class="table-channel"><span class="brand-small" :class="order.channel"><Brand :name="order.channel" /></span>{{ order.config_name || channelNames[order.channel] }}</span><small>{{ productName(order.product) }}</small></td>
            <td class="numeric strong">¥ {{ order.amount }}</td>
            <td><span class="state-badge" :class="order.status"><i></i>{{ status(order.status) }}</span></td>
            <td class="date-cell">{{ fullDate(order.created_at) }}</td>
            <td class="date-cell">{{ fullDate(order.paid_at) }}</td>
            <td class="align-right"><OrderActions :order="order" /></td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState v-else :title="rows.loading.value ? t('正在读取订单…', 'Loading orders…') : t('暂无匹配订单', 'No matching orders')" :description="t('可以调整筛选条件，或创建一笔测试订单', 'Adjust the filters or create a test order')" />
    <Pagination v-model:page="rows.page.value" :total="rows.total.value" :loading="rows.loading.value" />
  </section>
  <ConfirmDialog :open="confirming" :busy="acting" :destructive="deleted !== '1'" :title="deleted === '1' ? t('批量恢复订单', 'Restore selected orders') : t('批量删除订单', 'Delete selected orders')" :description="t(`将处理 ${count} 笔订单，账务与回调记录保留，退款中的订单会跳过`, `${count} orders will be processed. Accounting and callback records are kept; refunding orders are skipped`)" :label="deleted === '1' ? t('确认恢复', 'Restore') : t('确认删除', 'Delete')" @cancel="confirming = false" @confirm="remove" />
</template>
