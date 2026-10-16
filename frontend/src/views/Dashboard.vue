<script setup>
import { defineAsyncComponent, provide, ref, watch } from 'vue'
import { dark, locale, t } from '../state'
import { useConsole } from '../console/useConsole'
import Icon from '../components/Icon.vue'
import ConsoleSidebar from '../console/ConsoleSidebar.vue'
import OverviewPanel from '../console/OverviewPanel.vue'
import OrderDrawer from '../console/OrderDrawer.vue'
import '../console/console.css'
const c = useConsole()
provide('console', c)
const compact = ref(false), mobileNav = ref(null), quickSearch = ref('')
const panels = {
  overview: OverviewPanel,
  channels: defineAsyncComponent(() => import('../console/ChannelsPanel.vue')),
  testpay: defineAsyncComponent(() => import('../console/PaymentPanel.vue')),
  orders: defineAsyncComponent(() => import('../console/OrdersPanel.vue')),
  webhook: defineAsyncComponent(() => import('../console/CallbacksPanel.vue')),
  users: defineAsyncComponent(() => import('../console/UsersPanel.vue')),
  settings: defineAsyncComponent(() => import('../console/SettingsPanel.vue')),
  logs: defineAsyncComponent(() => import('../console/LogsPanel.vue')),
  keys: defineAsyncComponent(() => import('../console/KeysPanel.vue')),
}
watch(c.tab, () => { mobileNav.value?.close(); c.error.value = ''; document.getElementById('console-scroll')?.scrollTo({ top: 0 }) })
function toggleSidebar() { if (window.matchMedia('(max-width: 760px)').matches) mobileNav.value.showModal(); else compact.value = !compact.value }
</script>
<template>
  <div class="pay-console" :class="{ 'sidebar-compact': compact }">
    <aside class="console-sidebar"><ConsoleSidebar :compact="compact" /></aside>
    <div class="console-main">
      <header class="console-topbar"><div class="console-breadcrumb"><button class="icon-button" :aria-label="t('切换侧栏', 'Toggle sidebar')" @click="toggleSidebar"><Icon name="sidebar" :size="19" /></button><h1>{{ c.current.value?.label }}</h1></div><div class="console-topbar-actions"><form class="console-quick-search" @submit.prevent="c.navigate('orders', { q: quickSearch })"><Icon name="search" :size="16" /><input v-model="quickSearch" :placeholder="t('搜索订单…', 'Search orders…')" :aria-label="t('全局搜索订单', 'Search all orders')" /><kbd>↵</kbd></form><button class="icon-button" @click="dark = !dark" :aria-label="dark ? t('切换浅色', 'Switch to light mode') : t('切换深色', 'Switch to dark mode')"><Icon :name="dark ? 'sun' : 'moon'" :size="18" /></button><button class="icon-button console-language" @click="locale = locale === 'zh' ? 'en' : 'zh'" :aria-label="t('Switch to English', '切换到中文')"><Icon name="language" :size="17" /><span>{{ locale === 'zh' ? 'EN' : '中' }}</span></button><span class="toolbar-divider"></span><button class="icon-button" :aria-label="t('刷新控制台', 'Refresh console')" :disabled="c.refreshing.value" @click="c.refresh(true)"><Icon name="refresh" :size="17" :class="{ spin: c.refreshing.value }" /></button></div></header>
      <div id="console-scroll" class="console-scroll"><div class="console-content"><p v-if="c.error.value" class="error-box" role="alert">{{ c.error.value }}<button class="text-link" @click="c.refresh(true)">{{ t('重试', 'Retry') }}</button></p><component :is="panels[c.tab.value]" /></div></div>
    </div>
    <dialog ref="mobileNav" class="console-nav-dialog" :aria-label="t('控制台导航', 'Console navigation')"><button class="icon-button nav-dialog-close" :aria-label="t('关闭导航', 'Close navigation')" @click="mobileNav.close()"><Icon name="x" /></button><ConsoleSidebar /></dialog>
    <div v-if="c.notice.value" class="console-toast" role="status"><Icon name="success" :size="18" />{{ c.notice.value }}</div>
    <OrderDrawer />
  </div>
</template>
