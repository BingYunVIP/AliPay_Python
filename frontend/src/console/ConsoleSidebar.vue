<script setup>
import { inject } from 'vue'
import { auth, isAdmin, t } from '../state'
import Icon from '../components/Icon.vue'
import Brand from '../components/Brand.vue'
const c = inject('console')
defineProps({ compact: Boolean })
</script>
<template>
  <div class="console-sidebar-content" :class="{ compact }">
    <RouterLink to="/" class="console-brand" :title="t('返回冰云支付首页', 'Return to Pay home')"><Brand name="bingyun" /><span>{{ t('冰云支付', 'Pay') }}</span></RouterLink>
    <nav :aria-label="t('控制台导航', 'Console navigation')">
      <template v-for="group in ['core', 'system', 'account']" :key="group">
        <p class="sidebar-group">{{ ({ core: t('核心收单业务', 'PAYMENT OPERATIONS'), system: t('系统与安全', 'SYSTEM & SECURITY'), account: t('开发者', 'DEVELOPER') })[group] }}</p>
        <button v-for="item in c.menus.value.filter(m => m.group === group)" :key="item.id" class="sidebar-link" :class="{ active: c.tab.value === item.id }" :aria-label="item.label" :aria-current="c.tab.value === item.id ? 'page' : undefined" :title="compact ? item.label : undefined" @click="c.navigate(item.id)">
          <Icon :name="item.icon" :size="19" /><span>{{ item.label }}</span>
          <small v-if="item.id === 'webhook' && c.overview.value?.callbacks.failed" class="sidebar-count">{{ c.overview.value.callbacks.failed }}</small>
          <small v-if="item.id === 'channels' && c.caps.value?.channel_enabled && c.caps.value?.payment_ready" class="sidebar-count healthy">{{ c.overview.value?.enabled_config_count || 0 }}</small>
        </button>
      </template>
    </nav>
    <div class="sidebar-bottom">
      <div class="gateway-status"><span class="status-led" :class="c.connection.value"></span><span>{{ t('网关连接', 'Gateway') }}<small>{{ c.connection.value === 'online' ? t('已连接', 'Connected') : c.connection.value === 'error' ? t('连接中断', 'Disconnected') : t('连接中', 'Connecting') }}</small></span><Icon name="network" :size="17" /></div>
      <div class="sidebar-user"><span class="user-avatar"><Icon name="user" :size="17" /></span><span><b>{{ isAdmin ? t('管理员', 'Administrator') : t('我的账户', 'My account') }}</b><small>{{ auth.user?.email }}</small></span><button class="icon-button" :aria-label="t('退出登录', 'Sign out')" @click="c.logout"><Icon name="logout" :size="17" /></button></div>
    </div>
  </div>
</template>
