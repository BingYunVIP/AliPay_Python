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
const c = inject('console'), route = useRoute()
const search = ref(String(route.query.q || '')), searchValue = ref(search.value), state = ref('all'), period = ref('all'), exporting = ref(false)
const channel=ref('all'),product=ref('all'),deleted=ref('0'),selected=ref(new Set()),allMatching=ref(false),batchOpen=ref(false),batchBusy=ref(false)
const selectedCount=computed(()=>allMatching.value?rows.total.value:selected.value.size)
function clearSelection(){selected.value=new Set();allMatching.value=false}
function selectPage(){selected.value=new Set(rows.data.value.map(o=>o.out_trade_no));allMatching.value=false}
function toggleRow(number){if(allMatching.value){selected.value=new Set(rows.data.value.map(o=>o.out_trade_no));allMatching.value=false}const next=new Set(selected.value);if(next.has(number))next.delete(number);else next.add(number);selected.value=next}
function selection(){return allMatching.value?{all_matching:true}:{ids:[...selected.value]}}
let debounce
onBeforeUnmount(() => clearTimeout(debounce))
watch(searchValue, value => { clearTimeout(debounce); debounce = setTimeout(() => { search.value = value }, 250) })
watch(() => route.query.q, value => { searchValue.value = String(value || ''); search.value = searchValue.value })
const rows = usePaged('/api/console/orders', 'orders', () => ({ search: search.value, status: state.value, period: period.value, channel: channel.value, product: product.value, deleted: deleted.value }), c)
watch([search,state,period,channel,product,deleted],clearSelection)
async function batch(){batchBusy.value=true;try{const data=await api(`/api/console/orders/batch?${rows.query.value}`,{method:'POST',body:JSON.stringify({...selection(),action:deleted.value==='1'?'restore':'delete'})});c.toast(`已处理 ${data.changed} 笔${data.skipped.length?`，${data.skipped.length} 笔退款中订单已跳过`:''}`);clearSelection();batchOpen.value=false;await rows.load()}catch(e){c.report(e)}finally{batchBusy.value=false}}
async function exportCsv(onlySelected=false) {
  exporting.value = true
  try {
    const response = await fetch(`/api/console/orders/export?${rows.query.value}`, { credentials:'same-origin',signal:AbortSignal.timeout(20000),...(onlySelected?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':auth.csrf},body:JSON.stringify(selection())}:{}) })
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
    <div class="table-toolbar"><select v-model="state" class="order-filter-select" :aria-label="t('交易状态', 'Payment status')"><option v-for="value in ['all','CREATED','PAID','EXPIRED','CLOSED','REFUNDING','PARTIALLY_REFUNDED','REFUNDED']" :key="value" :value="value">{{ value === 'all' ? t('全部状态', 'All statuses') : status(value) }}</option></select><select v-model="channel" class="order-filter-select" :aria-label="t('收款渠道筛选', 'Filter by channel')"><option value="all">{{ t('全部渠道', 'All channels') }}</option><option value="alipay">{{ t('支付宝', 'Alipay') }}</option><option value="wechat">{{ t('微信支付', 'WeChat Pay') }}</option><option value="paypal">PayPal</option><option value="stripe">Stripe</option></select><select v-model="product" class="order-filter-select" aria-label="支付场景筛选"><option value="all">全部场景</option><option v-for="p in ['page','wap','app','mini']" :key="p" :value="p">{{ productName(p) }}</option></select><select v-model="deleted" class="order-filter-select" :aria-label="t('订单列表范围', 'Order list scope')"><option value="0">{{ t('正常订单', 'Active orders') }}</option><option value="1">{{ t('已删除', 'Deleted') }}</option></select><button class="button button-secondary" :disabled="exporting || !rows.total.value" @click="exportCsv(false)"><Icon :name="exporting ? 'loader' : 'download'" :class="{ spin: exporting }" :size="16" />{{ t('导出筛选结果', 'Export filtered') }}</button></div>
    <div class="table-filter-row"><label class="console-search"><Icon name="search" :size="17" /><input v-model="searchValue" :placeholder="t('搜索订单号、商品或交易号', 'Search order, item, or trade number')" :aria-label="t('搜索订单', 'Search orders')" /></label><label class="select-inline"><Icon name="calendar" :size="15" /><select v-model="period" :aria-label="t('订单时间', 'Order period')"><option value="all">{{ t('全部时间', 'All time') }}</option><option value="today">{{ t('今日', 'Today') }}</option><option value="7d">{{ t('近 7 天', 'Last 7 days') }}</option><option value="30d">{{ t('近 30 天', 'Last 30 days') }}</option></select></label><button class="icon-button" @click="rows.load" :aria-label="t('刷新订单', 'Refresh orders')"><Icon name="refresh" :size="16" :class="{ spin: rows.loading.value }" /></button></div>
    <div class="order-selection-toolbar"><button class="text-link" @click="selectPage" :disabled="!rows.data.value.length">选择本页</button><button class="text-link" @click="allMatching=true;selected=new Set()" :disabled="!rows.total.value">全选筛选结果</button><button class="text-link" @click="clearSelection">不选</button><span>已选 {{ selectedCount }} 笔</span><button class="button button-secondary" :disabled="!selectedCount||exporting" @click="exportCsv(true)">导出选中 CSV</button><button class="button button-secondary" :disabled="!selectedCount||selectedCount>1000" @click="batchOpen=true">{{ deleted==='1'?'恢复选中':'删除选中' }}</button><span v-if="selectedCount>1000" class="field-hint">批量操作每次最多 1000 笔，请缩小筛选范围</span></div>
    <p v-if="rows.error.value" class="error-box" role="alert">{{ rows.error.value }}</p>
    <div v-if="rows.data.value.length" class="table-scroll"><table class="console-table"><thead><tr><th><input type="checkbox" aria-label="选择本页订单" :checked="rows.data.value.length>0 && rows.data.value.every(o=>allMatching||selected.has(o.out_trade_no))" @change="$event.target.checked?selectPage():clearSelection()"/></th><th>{{ t('订单号 / 商品', 'Order / Item') }}</th><th>{{ t('渠道 / 场景', 'Channel / Product') }}</th><th>{{ t('实付金额', 'Amount') }}</th><th>{{ t('交易状态', 'Status') }}</th><th>{{ t('创建时间', 'Created') }}</th><th class="align-right">{{ t('操作', 'Actions') }}</th></tr></thead><tbody><tr v-for="order in rows.data.value" :key="order.out_trade_no"><td><input type="checkbox" :aria-label="`选择订单 ${order.out_trade_no}`" :checked="allMatching||selected.has(order.out_trade_no)" @change="toggleRow(order.out_trade_no)"/></td><td><button class="order-name" @click="c.selectedOrder.value = order">{{ order.subject }}</button><code>{{ order.out_trade_no }}</code></td><td><span class="table-channel"><span class="brand-small" :class="order.channel"><Brand :name="order.channel" /></span>{{ ({ alipay: t('支付宝', 'Alipay'), wechat: t('微信支付', 'WeChat Pay'), paypal: 'PayPal', stripe: 'Stripe' })[order.channel] }}</span><small>{{ order.config_name || `#${order.payment_config_id}` }} · {{ productName(order.product) }}</small></td><td class="numeric strong">¥ {{ order.amount }}</td><td><span class="state-badge" :class="order.status"><i></i>{{ status(order.status) }}</span></td><td class="date-cell">{{ fullDate(order.created_at) }}</td><td class="align-right"><OrderActions :order="order" /></td></tr></tbody></table></div>
    <EmptyState v-else :title="rows.loading.value ? t('正在读取订单…', 'Loading orders…') : t('暂无匹配订单', 'No matching orders')" :description="t('可以调整筛选条件，或创建一笔测试订单。', 'Adjust the filters or create a test order.')" />
    <Pagination v-model:page="rows.page.value" :total="rows.total.value" :loading="rows.loading.value" />
  </section>
  <ConfirmDialog :open="batchOpen" :busy="batchBusy" :title="deleted==='1'?'批量恢复订单':'批量删除订单'" :description="`将处理 ${selectedCount} 笔订单，账务与回调记录保留，退款中的订单会跳过`" @cancel="batchOpen=false" @confirm="batch"/>
</template>
