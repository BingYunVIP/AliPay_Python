<script setup>
import { inject, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { t } from '../state'
import { usePaged } from './useConsole'
import { fullDate, logMessage, stageName } from './format'
import Icon from '../components/Icon.vue'
import EmptyState from './EmptyState.vue'
import Pagination from './Pagination.vue'
const c = inject('console'), route = useRoute()
const search = ref(String(route.query.q || '')), category = ref('all'), rejected = ref(false)
watch(() => route.query.q, value => { search.value = String(value || '') })
const rows = usePaged('/api/console/events', 'events', () => ({ search: search.value, category: category.value, status: rejected.value ? 'rejected' : 'all' }), c)
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('系统日志', 'Event log') }}</h2><p>{{ t('订单处理、通知验证与管理操作，全程留痕。', 'Order processing, verified notifications, and administration activity.') }}</p></div><button class="button button-secondary" @click="rows.load"><Icon name="refresh" :size="16" />{{ t('刷新日志', 'Refresh log') }}</button></div>
  <section class="hig-card"><div class="table-toolbar log-toolbar"><label class="console-search"><Icon name="search" :size="17" /><input v-model.lazy="search" :placeholder="t('订单号、事件或错误代码', 'Order number, event, or error code')" :aria-label="t('搜索日志', 'Search log')" /></label><select v-model="category" :aria-label="t('日志分类', 'Log category')"><option value="all">{{ t('全部分类', 'All categories') }}</option><option v-for="cat in ['create','checkout','sdk','query','notify','return','merchant','refund','order','console']" :key="cat" :value="cat">{{ stageName(cat) }}</option></select><label class="checkbox-label"><input v-model="rejected" type="checkbox" />{{ t('仅待验证 / 异常', 'Unverified / errors') }}</label></div><p v-if="rows.error.value" role="alert" class="error-box">{{ rows.error.value }}</p><div v-if="rows.data.value.length" class="console-log-list"><details v-for="event in rows.data.value" :key="event.id" class="console-log-row"><summary><span class="log-indicator" :class="event.accepted ? 'success' : 'pending'"><Icon :name="event.accepted ? 'check' : 'help'" :size="14" /></span><span class="log-main"><span><b>{{ logMessage(event.reason) }}</b><small class="log-category">{{ stageName(event.stage) }}</small></span><code>{{ event.out_trade_no || t('系统事件', 'System event') }}</code></span><time>{{ fullDate(event.created_at) }}</time><Icon name="chevron" :size="16" /></summary><div class="log-expanded"><div><code>{{ event.stage }}</code><span>{{ t('事件', 'Event') }} #{{ event.id }}</span></div><pre>{{ JSON.stringify(event.details, null, 2) }}</pre></div></details></div><EmptyState v-else icon="logs" :title="rows.loading.value ? t('正在读取日志…', 'Loading events…') : t('暂无匹配日志', 'No matching events')" :description="t('调整分类或搜索条件以查看其他记录。', 'Adjust the category or search to find other records.')" /><Pagination v-model:page="rows.page.value" :total="rows.total.value" :loading="rows.loading.value" /></section>
</template>
