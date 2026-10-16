<script setup>
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { auth, t } from '../state'
import { usePaged } from './useConsole'
import { fullDate, productName, status } from './format'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
import EmptyState from './EmptyState.vue'
import Pagination from './Pagination.vue'
import OrderActions from './OrderActions.vue'
const c = inject('console'), route = useRoute()
const search = ref(String(route.query.q || '')), searchValue = ref(search.value), state = ref('all'), period = ref('all'), exporting = ref(false)
const channel = ref('all'), configId = ref(''), deleted = ref('0')
const configs = computed(() => (c.caps.value?.payment_configs || []).filter(ch => channel.value === 'all' || ch.id === channel.value))
watch(channel, () => { configId.value = '' })
let debounce
onBeforeUnmount(() => clearTimeout(debounce))
watch(searchValue, value => { clearTimeout(debounce); debounce = setTimeout(() => { search.value = value }, 250) })
watch(() => route.query.q, value => { searchValue.value = String(value || ''); search.value = searchValue.value })
const rows = usePaged('/api/console/orders', 'orders', () => ({ search: search.value, status: state.value, period: period.value, channel: channel.value, config_id: configId.value, deleted: deleted.value }), c)
async function exportCsv() {
  exporting.value = true
  try {
    const response = await fetch(`/api/console/orders/export?${rows.query.value}`, { credentials: 'same-origin', signal: AbortSignal.timeout(20000) })
    if (!response.ok) { const data = await response.json(); throw Object.assign(new Error(data.error), { status: response.status }) }
    const url = URL.createObjectURL(await response.blob()), link = document.createElement('a')
    link.href = url; link.download = `pay-orders-${new Date().toISOString().slice(0,10)}.csv`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    c.toast(t('订单已导出。', 'Orders exported.'))
  } catch (e) { c.report(e) }
  finally { exporting.value = false }
}
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('订单记录', 'Orders') }}</h2><p>{{ t('查询交易状态，追踪每一笔收款。', 'Track your payments and inspect each transaction.') }}</p></div><button class="button" @click="c.navigate('testpay')"><Icon name="plus" :size="16" />{{ t('创建订单', 'Create order') }}</button></div>
  <section class="hig-card orders-card">
    <div class="table-toolbar"><select v-model="state" class="order-filter-select" :aria-label="t('交易状态', 'Payment status')"><option v-for="value in ['all','CREATED','PAID','EXPIRED','CLOSED','REFUNDING','PARTIALLY_REFUNDED','REFUNDED']" :key="value" :value="value">{{ value === 'all' ? t('全部状态', 'All statuses') : status(value) }}</option></select><select v-model="channel" class="order-filter-select" :aria-label="t('收款渠道筛选', 'Filter by channel')"><option value="all">{{ t('全部收款渠道', 'All channels') }}</option><option value="alipay">{{ t('支付宝', 'Alipay') }}</option><option value="wechat">{{ t('微信支付', 'WeChat Pay') }}</option><option value="paypal">PayPal</option><option value="stripe">Stripe</option></select><select v-model="configId" class="order-filter-select" :aria-label="t('收款配置筛选', 'Filter by configuration')"><option value="">{{ t('全部配置', 'All configurations') }}</option><option v-for="ch in configs" :key="ch.config_id" :value="ch.config_id">{{ ch.name }}</option></select><select v-model="deleted" class="order-filter-select" :aria-label="t('订单列表范围', 'Order list scope')"><option value="0">{{ t('正常订单', 'Active orders') }}</option><option value="1">{{ t('已删除', 'Deleted') }}</option></select><button class="button button-secondary" :disabled="exporting || !rows.total.value" @click="exportCsv"><Icon :name="exporting ? 'loader' : 'download'" :class="{ spin: exporting }" :size="16" />{{ t('导出 CSV', 'Export CSV') }}</button></div>
    <div class="table-filter-row"><label class="console-search"><Icon name="search" :size="17" /><input v-model="searchValue" :placeholder="t('搜索订单号、商品或交易号', 'Search order, item, or trade number')" :aria-label="t('搜索订单', 'Search orders')" /></label><label class="select-inline"><Icon name="calendar" :size="15" /><select v-model="period" :aria-label="t('订单时间', 'Order period')"><option value="all">{{ t('全部时间', 'All time') }}</option><option value="today">{{ t('今日', 'Today') }}</option><option value="7d">{{ t('近 7 天', 'Last 7 days') }}</option><option value="30d">{{ t('近 30 天', 'Last 30 days') }}</option></select></label><button class="icon-button" @click="rows.load" :aria-label="t('刷新订单', 'Refresh orders')"><Icon name="refresh" :size="16" :class="{ spin: rows.loading.value }" /></button></div>
    <p v-if="rows.error.value" class="error-box" role="alert">{{ rows.error.value }}</p>
    <div v-if="rows.data.value.length" class="table-scroll"><table class="console-table"><thead><tr><th>{{ t('订单号 / 商品', 'Order / Item') }}</th><th>{{ t('渠道 / 场景', 'Channel / Product') }}</th><th>{{ t('实付金额', 'Amount') }}</th><th>{{ t('交易状态', 'Status') }}</th><th>{{ t('创建时间', 'Created') }}</th><th class="align-right">{{ t('操作', 'Actions') }}</th></tr></thead><tbody><tr v-for="order in rows.data.value" :key="order.out_trade_no"><td><button class="order-name" @click="c.selectedOrder.value = order">{{ order.subject }}</button><code>{{ order.out_trade_no }}</code></td><td><span class="table-channel"><span class="brand-small" :class="order.channel"><Brand :name="order.channel" /></span>{{ ({ alipay: t('支付宝', 'Alipay'), wechat: t('微信支付', 'WeChat Pay'), paypal: 'PayPal', stripe: 'Stripe' })[order.channel] }}</span><small>{{ order.config_name || `#${order.payment_config_id}` }} · {{ productName(order.product) }}</small></td><td class="numeric strong">¥ {{ order.amount }}</td><td><span class="state-badge" :class="order.status"><i></i>{{ status(order.status) }}</span></td><td class="date-cell">{{ fullDate(order.created_at) }}</td><td class="align-right"><OrderActions :order="order" /></td></tr></tbody></table></div>
    <EmptyState v-else :title="rows.loading.value ? t('正在读取订单…', 'Loading orders…') : t('暂无匹配订单', 'No matching orders')" :description="t('可以调整筛选条件，或创建一笔测试订单。', 'Adjust the filters or create a test order.')" />
    <Pagination v-model:page="rows.page.value" :total="rows.total.value" :loading="rows.loading.value" />
  </section>
</template>
