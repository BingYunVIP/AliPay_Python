<script setup>
import { computed, defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { t } from '../state'
import PageTabs from './PageTabs.vue'
import SiteSettings from './SiteSettings.vue'
const route = useRoute(), router = useRouter()
const sections = {
  site: SiteSettings,
  policy: defineAsyncComponent(() => import('./PolicySettings.vue')),
  mail: defineAsyncComponent(() => import('./MailSettings.vue')),
  human: defineAsyncComponent(() => import('./TurnstileSettings.vue')),
  tasks: defineAsyncComponent(() => import('./TaskSettings.vue')),
}
const items = computed(() => [{ id: 'site', label: t('站点与订单', 'Site & orders') }, { id: 'policy', label: t('商户与资金规则', 'Merchants & funds') }, { id: 'mail', label: t('邮箱与模板', 'Email & templates') },
  { id: 'human', label: t('人机验证', 'Human check') }, { id: 'tasks', label: t('计划任务', 'Scheduled tasks') }])
// The open section lives in the address, so a section can be linked to and survives a reload
const section = computed({
  get: () => items.value.some(item => item.id === route.query.section) ? route.query.section : 'site',
  set: id => router.replace({ query: { ...route.query, section: id } }),
})
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('系统管理', 'System settings') }}</h2><p>{{ t('站点、商户规则、邮件、人机验证与后台任务', 'Site, merchant rules, email, human check and background tasks') }}</p></div></div>
  <PageTabs v-model="section" :items="items" :label="t('系统设置分类', 'Settings sections')" />
  <div :id="`panel-${section}`" role="tabpanel" :aria-labelledby="`tab-${section}`">
    <KeepAlive><component :is="sections[section]" /></KeepAlive>
  </div>
</template>
