<script setup>
import {isAdmin} from '../state'
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { copyText, t } from '../state'
import { usePaged } from './useConsole'
import { dayLabel, fullDate, logCategories, logLevel, logMessage, stageName, time } from './format'
import Icon from '../components/Icon.vue'
import EmptyState from './EmptyState.vue'
import Pagination from './Pagination.vue'
import Segmented from './Segmented.vue'
const c = inject('console'), route = useRoute(), router = useRouter()
const search = ref(String(route.query.q || '')), searchValue = ref(search.value), copied = ref(0)
// the filters live in the address, so a filtered log can be linked to or reloaded
const category = ref(logCategories.includes(route.query.category) ? route.query.category : 'all'), scope = ref(route.query.scope === 'attention' ? 'attention' : 'all')
watch([category, scope], () => router.replace({ query: { ...route.query, category: category.value === 'all' ? undefined : category.value, scope: scope.value === 'all' ? undefined : scope.value } }))
const rows = usePaged('/api/console/events', 'events', () => ({ search: search.value, category: category.value, status: scope.value === 'attention' ? 'rejected' : 'all' }), c)
let debounce, copyTimer
onBeforeUnmount(() => { clearTimeout(debounce); clearTimeout(copyTimer) })
watch(searchValue, value => { clearTimeout(debounce); debounce = setTimeout(() => { search.value = value }, 250) })
watch(() => route.query.q, value => { searchValue.value = String(value || ''); search.value = searchValue.value })
const levelIcon = { ok: 'check', wait: 'timer', bad: 'alert' }
const levelName = level => ({ ok: t('正常', 'OK'), wait: t('待验证', 'Pending'), bad: t('异常', 'Problem') })[level]
// Events arrive newest first; a heading appears whenever the day changes
const days = computed(() => {
  const groups = []
  for (const event of rows.data.value) {
    const label = dayLabel(event.created_at)
    if (groups.at(-1)?.label !== label) groups.push({ label, events: [] })
    groups.at(-1).events.push(event)
  }
  return groups
})
async function copyDetails(event) {
  try { await copyText(JSON.stringify(event.details, null, 2)); copied.value = event.id; clearTimeout(copyTimer); copyTimer = setTimeout(() => { copied.value = 0 }, 2000) }
  catch { c.toast(t('请选中内容手动复制', 'Select the text to copy')) }
}
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('系统日志', 'Event log') }}</h2><p>{{ t('订单处理、通知验证与管理操作，全程留痕', 'Order processing, verified notifications, and administration activity') }}</p></div></div>
  <section class="hig-card admin-card">
    <div class="admin-toolbar">
      <label class="console-search"><Icon name="search" :size="17" /><input v-model="searchValue" :placeholder="t('订单号、事件或错误代码', 'Order number, event, or error code')" :aria-label="t('搜索日志', 'Search log')" name="search" autocomplete="off" spellcheck="false" /></label>
      <select v-model="category" class="field" :aria-label="t('日志分类', 'Log category')"><option value="all">{{ t('全部分类', 'All categories') }}</option><option v-for="key in logCategories" :key="key" :value="key">{{ stageName(key) }}</option></select>
      <Segmented v-model="scope" class="push" :label="t('日志范围', 'Log scope')" :options="[{ value: 'all', label: t('全部', 'All') }, { value: 'attention', label: t('待验证 / 异常', 'Pending / problems') }]" />
    </div>
    <p v-if="rows.error.value" role="alert" class="error-box">{{ rows.error.value }}</p>
    <template v-if="rows.data.value.length">
      <div v-for="group in days" :key="group.label">
        <h3 class="log-day">{{ group.label }}</h3>
        <details v-for="event in group.events" :key="event.id" class="log-row">
          <summary>
            <span class="log-level" :class="logLevel(event)" :title="levelName(logLevel(event))"><Icon :name="levelIcon[logLevel(event)]" :size="14" /></span>
            <time class="log-time" :datetime="new Date(event.created_at * 1000).toISOString()">{{ time(event.created_at) }}</time>
            <span class="log-chip">{{ stageName(event.stage) }}</span>
            <span class="log-message">{{ logMessage(event.reason) }}</span>
            <code class="log-order" translate="no" :title="event.out_trade_no || undefined">{{ event.out_trade_no || t('系统事件', 'System') }}</code>
            <Icon name="chevron" :size="16" />
          </summary>
          <div class="log-detail">
            <div class="log-detail-head">
              <code translate="no">{{ event.stage }}</code><span>{{ t('事件', 'Event') }} #{{ event.id }}</span><span>{{ fullDate(event.created_at) }}</span>
              <RouterLink v-if="event.out_trade_no" class="text-link push" :to="{ path: `/${isAdmin ? 'admin' : 'user'}/orders`, query: { q: event.out_trade_no } }">{{ t('查看订单', 'View order') }}<Icon name="chevron" :size="13" /></RouterLink>
              <button type="button" class="icon-button" :class="{ push: !event.out_trade_no }" :aria-label="t('复制详情', 'Copy details')" @click="copyDetails(event)"><Icon :name="copied === event.id ? 'check' : 'copy'" :size="15" /></button>
            </div>
            <pre translate="no">{{ JSON.stringify(event.details, null, 2) }}</pre>
          </div>
        </details>
      </div>
    </template>
    <EmptyState v-else icon="logs" :title="rows.loading.value ? t('正在读取日志…', 'Loading events…') : t('暂无匹配日志', 'No matching events')" :description="rows.loading.value ? '' : t('调整分类或搜索条件以查看其他记录', 'Adjust the category or search to find other records')" />
    <Pagination v-model:page="rows.page.value" :total="rows.total.value" :loading="rows.loading.value" />
  </section>
</template>
