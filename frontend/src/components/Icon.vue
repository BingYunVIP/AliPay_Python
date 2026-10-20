<script setup>
import { computed } from 'vue'
const props = defineProps({ name: String, size: { type: [Number, String], default: 20 } })
const glyphs = {
  info: 'info', trash: 'delete', sun: 'light_mode', moon: 'dark_mode', language: 'language', menu: 'menu', x: 'close', arrow: 'arrow_forward', external: 'open_in_new',
  shield: 'shield', timer: 'timer', zap: 'bolt', code: 'code', monitor: 'desktop_windows', phone: 'smartphone', app: 'apps',
  eye: 'visibility', 'eye-off': 'visibility_off', grid: 'apps', check: 'check', copy: 'content_copy', terminal: 'terminal', card: 'credit_card', logout: 'logout',
  refresh: 'refresh', key: 'key', search: 'search', help: 'help', success: 'check_circle', alert: 'warning', chevron: 'keyboard_arrow_right', loader: 'progress_activity',
  mail: 'mail', book: 'menu_book', store: 'storefront', users: 'groups', globe: 'public', network: 'hub', lock: 'lock', pause: 'pause', play: 'play_arrow', plus: 'add',
  sidebar: 'view_sidebar', overview: 'space_dashboard', sliders: 'tune', test: 'science', receipt: 'receipt_long', webhook: 'webhook', settings: 'settings', logs: 'article', wallet: 'account_balance_wallet',
  trend: 'query_stats', download: 'download', calendar: 'calendar_month', down: 'expand_more', back: 'keyboard_arrow_left', more: 'more_horiz', bell: 'notifications', checks: 'task_alt', copied: 'library_add_check', incoming: 'arrow_downward',
  circle: 'radio_button_unchecked', user: 'account_circle', file: 'description'
}
const glyph = computed(()=>glyphs[props.name] || 'code')
</script>
<template><svg class="bypay-icon" :width="size" :height="size" viewBox="0 0 24 24" aria-hidden="true"><path v-if="name === 'logout'" d="M10.09 15.59 11.5 17l5-5-5-5-1.41 1.41L12.67 11H3v2h9.67zM19 3H5c-1.1 0-2 .9-2 2v4h2V5h14v14H5v-4H3v4c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2z"/><text v-else class="material-glyph" x="12" y="12" text-anchor="middle" dominant-baseline="central">{{ glyph }}</text></svg></template>
