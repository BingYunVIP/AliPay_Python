<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { auth, dark, locale, t } from './state'
import Icon from './components/Icon.vue'
import Brand from './components/Brand.vue'
const route = useRoute()
const consoleRoute = computed(() => route.path.startsWith('/dashboard'))
const menuOpen = ref(false)
watch(() => route.path, () => { menuOpen.value = false })
const links = [
  { zh: 'AI 代充', en: 'AI Top-up', brand: 'claude', href: 'https://buy.bingyun.vip/' },
  { zh: 'AI 中转站', en: 'AI Router', brand: 'openai', href: 'https://api.bingyun.vip' },
  { zh: '冰云商店', en: 'Store', icon: 'store', href: 'https://shop.bingyun.vip/' },
  { zh: '文档教程', en: 'Docs', icon: 'book', href: 'https://docs.bingyun.vip/zh/' },
  { zh: '售后群', en: 'Support', icon: 'users', href: 'https://qm.qq.com/q/2uwAjBpZbO' },
  { zh: '联系邮箱', en: 'Email', icon: 'mail', href: 'mailto:support@bingyun.vip' },
]
</script>
<template>
  <RouterView v-if="route.path.startsWith('/cashier/')" />
  <div v-else :class="consoleRoute ? 'console-root' : 'byh'">
    <a class="skip-link" href="#main-content">{{ t('跳转到主要内容', 'Skip to content') }}</a>
    <header v-if="!consoleRoute" class="byh-nav">
      <div class="byh-nav-in">
        <RouterLink to="/" class="byh-brand" :aria-label="t('冰云支付首页', 'Pay home')"><Brand name="bingyun" /><span>{{ t('冰云支付', 'Pay') }}</span></RouterLink>
        <nav class="byh-links" :aria-label="t('冰云站点导航', 'BingYun navigation')">
          <a v-for="link in links" :key="link.en" :href="link.href" target="_blank" rel="noopener noreferrer"><Brand v-if="link.brand" :name="link.brand" /><Icon v-else :name="link.icon" :size="17" />{{ t(link.zh, link.en) }}</a>
        </nav>
        <div class="byh-act">
          <button class="icon-button" @click="dark = !dark" :aria-label="t(dark ? '切换浅色' : '切换深色', dark ? 'Switch to light mode' : 'Switch to dark mode')" :title="t(dark ? '切换浅色' : '切换深色', dark ? 'Light mode' : 'Dark mode')"><Icon :name="dark ? 'sun' : 'moon'" :size="18" /></button>
          <button class="locale-button" @click="locale = locale === 'zh' ? 'en' : 'zh'" :aria-label="t('Switch to English', '切换到中文')"><Icon name="language" :size="17" /><span>{{ locale === 'zh' ? 'EN' : '中文' }}</span></button>
          <span class="nav-divider" aria-hidden="true"></span>
          <template v-if="!auth.user"><RouterLink class="nav-login" to="/login">{{ t('登录', 'Sign in') }}</RouterLink><RouterLink class="button button-small nav-register" to="/register">{{ t('注册', 'Sign up') }}</RouterLink></template>
          <RouterLink v-else to="/dashboard" class="button button-small">{{ t('控制台', 'Console') }}<Icon name="arrow" :size="15" /></RouterLink>
          <button class="icon-button mobile-menu-toggle" :aria-expanded="menuOpen" aria-controls="mobile-nav" :aria-label="t('菜单', 'Menu')" @click="menuOpen = !menuOpen"><Icon :name="menuOpen ? 'x' : 'menu'" /></button>
        </div>
      </div>
      <nav v-if="menuOpen" id="mobile-nav" class="mobile-nav" :aria-label="t('移动导航', 'Mobile navigation')" @keydown.esc="menuOpen = false">
        <a v-for="link in links" :key="link.en" :href="link.href" target="_blank" rel="noopener noreferrer" @click="menuOpen = false"><Brand v-if="link.brand" :name="link.brand" /><Icon v-else :name="link.icon" />{{ t(link.zh, link.en) }}<Icon class="push-right" name="external" :size="16" /></a>
        <RouterLink v-if="!auth.user" to="/register">{{ t('创建账户', 'Create an account') }}<Icon class="push-right" name="arrow" /></RouterLink>
      </nav>
    </header>
    <main id="main-content"><RouterView /></main>

  </div>
</template>
