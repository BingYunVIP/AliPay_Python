<script setup>
import { inject, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, errorText, t } from '../state'
import { usePaged } from './useConsole'
import { date, fullDate, status } from './format'
import Icon from '../components/Icon.vue'
import Pagination from './Pagination.vue'
import EmptyState from './EmptyState.vue'
import ConfirmDialog from './ConfirmDialog.vue'
const c = inject('console'), route = useRoute()
const filter = ref(String(route.query.status || 'all')), search = ref(''), retry = ref(null), busy = ref(false), error = ref('')
const rows = usePaged('/api/console/callbacks', 'callbacks', () => ({ status: filter.value, search: search.value }), c)
async function schedule() {
  busy.value = true; error.value = ''
  try { await api(`/api/console/callbacks/${retry.value.id}/retry`, { method: 'POST' }); retry.value = null; await rows.load(); c.refresh(true); c.toast(t('已加入投递队列，请等待工作进程处理。', 'Queued for delivery. The worker will process it shortly.')) }
  catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('回调服务', 'Callback delivery') }}</h2><p>{{ t('追踪发往商户站点的业务回调与重试记录。', 'Track signed payment notifications sent to merchant endpoints.') }}</p></div><button class="button button-secondary" @click="rows.load"><Icon name="refresh" :size="16" />{{ t('刷新队列', 'Refresh queue') }}</button></div>
  <div class="callback-summary"><div class="hig-card"><Icon name="success" class="text-green" :size="20" /><span>{{ t('投递成功', 'Delivered') }}<b>{{ c.overview.value?.callbacks.delivered || 0 }}</b></span></div><div class="hig-card"><Icon name="timer" class="text-blue" :size="20" /><span>{{ t('等待投递', 'Pending') }}<b>{{ c.overview.value?.callbacks.pending || 0 }}</b></span></div><div class="hig-card"><Icon name="alert" class="text-amber" :size="20" /><span>{{ t('投递失败', 'Failed') }}<b>{{ c.overview.value?.callbacks.failed || 0 }}</b></span></div></div>
  <div class="context-note"><Icon name="shield" :size="17" /><span>{{ t('Ed25519 签名保护，按系统设置自动重试，商户返回 HTTP 200 和 success 后确认送达', 'Ed25519 signed with configured retries; HTTP 200 and success confirm delivery') }}</span></div>
  <section class="hig-card"><div class="table-toolbar"><div class="segmented-control"><button v-for="value in ['all','delivered','pending','failed']" :key="value" :class="{ selected: filter === value }" :aria-pressed="filter === value" @click="filter = value">{{ value === 'all' ? t('全部回调', 'All callbacks') : status(value) }}</button></div><label class="console-search compact-search"><Icon name="search" :size="16" /><input v-model.lazy="search" :aria-label="t('搜索回调订单', 'Search callback orders')" :placeholder="t('搜索订单号', 'Search order number')" /></label></div><p v-if="rows.error.value" class="error-box" role="alert">{{ rows.error.value }}</p><div v-if="rows.data.value.length" class="callback-list"><article v-for="job in rows.data.value" :key="job.id" class="callback-row"><div class="callback-row-heading"><span class="state-badge" :class="job.status"><i></i>{{ status(job.status) }}</span><code>{{ job.out_trade_no }}</code><button v-if="job.status !== 'delivered'" class="button button-secondary" :disabled="job.locked_until > Date.now()/1000" @click="retry = job; error = ''"><Icon name="refresh" :size="14" />{{ t('重新投递', 'Retry delivery') }}</button><span v-else class="delivery-ok"><Icon name="checks" :size="18" />{{ t('商户已确认', 'Acknowledged') }}</span></div><div class="callback-target"><Icon name="globe" :size="14" /><span>{{ job.url }}</span></div><div class="callback-facts"><span>{{ t('已尝试', 'Attempts') }} <b>{{ job.attempts }}</b></span><span v-if="job.status === 'pending'">{{ t('下次投递', 'Next attempt') }} <b>{{ date(job.next_attempt) }}</b></span><span v-if="job.last_error" class="text-amber">{{ job.last_error }}</span></div><details class="payload-details"><summary>{{ t('查看回调报文', 'Inspect payload') }}<Icon name="down" :size="13" /></summary><pre>{{ JSON.stringify(job.payload, null, 2) }}</pre></details></article></div><EmptyState v-else icon="webhook" :title="rows.loading.value ? t('正在读取队列…', 'Loading queue…') : t('暂无匹配的商户回调', 'No matching callbacks')" :description="t('带商户通知地址的订单支付成功后，会在这里生成投递记录。', 'Paid orders with a merchant notification URL appear here.')" /><Pagination v-model:page="rows.page.value" :total="rows.total.value" :loading="rows.loading.value" /></section>
  <ConfirmDialog :open="!!retry" :busy="busy" :title="t('重新投递这条回调？', 'Retry this callback?')" :description="t('将同一事件重新加入投递队列。收款状态不会改变，商户应根据事件 ID 幂等处理。', 'Requeues the same event. Payment status remains unchanged; the merchant should deduplicate using the event ID.')" :label="t('加入重试队列', 'Queue retry')" @cancel="retry = null" @confirm="schedule"><p v-if="error" class="error-box" role="alert">{{ error }}</p></ConfirmDialog>
</template>
