import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, auth, errorText, isAdmin, t } from '../state'

export function useConsole() {
  const route = useRoute(), router = useRouter()
  const overview = ref(null), caps = ref(null), settings = ref(null), period = ref('7d')
  const busy = ref(true), refreshing = ref(false), error = ref(''), notice = ref(''), latency = ref(null)
  const revision = ref(0), selectedOrder = ref(null)
  let interval, timer, disposed = false, active = false, version = 0
  const menus = computed(() => [
    { id: 'overview', label: t('概览', 'Overview'), icon: 'overview', group: 'core' },
    ...(isAdmin.value ? [{ id: 'channels', label: t('收款渠道', 'Channels'), icon: 'sliders', group: 'core' }] : []),
    { id: 'testpay', label: t('测试支付', 'Test payment'), icon: 'test', group: 'core' },
    { id: 'orders', label: t('订单记录', 'Orders'), icon: 'receipt', group: 'core' },
    { id: 'webhook', label: t('回调服务', 'Callbacks'), icon: 'webhook', group: 'core' },
    ...(isAdmin.value ? [{ id: 'users', label: t('用户管理', 'Users'), icon: 'users', group: 'system' },
      { id: 'settings', label: t('系统管理', 'Settings'), icon: 'settings', group: 'system' }] : []),
    { id: 'logs', label: t('系统日志', 'Event log'), icon: 'shield', group: 'system' },
    { id: 'account',label:t('账户与结算','Account & settlements'),icon:'wallet',group:'account'},
    ...(isAdmin.value?[{id:'settlements',label:t('结算管理','Settlements'),icon:'card',group:'system'}]:[]),
    { id: 'keys', label: t('接口配置', 'Merchant API'), icon: 'key', group: 'account' },
  ])
  const tab = computed(() => menus.value.some(m => m.id === route.query.tab) ? route.query.tab : 'overview')
  const current = computed(() => menus.value.find(m => m.id === tab.value))
  function navigate(id, query = {}) { return router.push({ path: '/dashboard', query: { tab: id, ...query } }) }
  function report(e) { error.value = errorText(e); if (e.status === 401) router.push('/login') }
  function toast(text) { clearTimeout(timer); notice.value = text; timer = setTimeout(() => { notice.value = '' }, 4200) }
  async function refresh(manual = false) {
    if (disposed || (active && !manual)) return
    const requestVersion = ++version
    active = true; refreshing.value = true
    const start = performance.now()
    try {
      const results = await Promise.all([api(`/api/console/overview?period=${period.value}`), api('/api/capabilities'), ...(isAdmin.value ? [api('/api/admin/settings')] : [])])
      if (disposed || requestVersion !== version) return
      overview.value = results[0]; caps.value = results[1]; settings.value = results[2] || null
      latency.value = Math.round(performance.now() - start); error.value = ''
      if (manual) revision.value++
    } catch (e) { if (!disposed && requestVersion === version) report(e) }
    finally { if (requestVersion === version) { active = false; busy.value = false; refreshing.value = false } }
  }
  async function logout() {
    try { await api('/api/auth/logout', { method: 'POST' }); auth.user = null; auth.csrf = ''; auth.key = null; router.push('/') }
    catch (e) { report(e) }
  }
  watch(period, () => refresh(true))
  onMounted(() => { refresh(); interval = setInterval(() => { if (!document.hidden) { refresh(); revision.value++ } }, 10000) })
  onBeforeUnmount(() => { disposed = true; version++; clearInterval(interval); clearTimeout(timer) })
  const connection = computed(() => error.value ? 'error' : overview.value ? 'online' : 'loading')
  return { overview, caps, settings, period, busy, refreshing, error, notice, latency, menus, tab, current,
    revision, selectedOrder, connection, navigate, refresh, report, toast, logout }
}

export function usePaged(endpoint, key, filters, consoleState) {
  const data = ref([]), total = ref(0), page = ref(1), loading = ref(true), error = ref('')
  let sequence = 0, disposed = false
  const query = computed(() => new URLSearchParams({ ...filters(), page: String(page.value), page_size: '20' }).toString())
  async function load() {
    const version = ++sequence
    loading.value = true
    try {
      const result = await api(`${endpoint}?${query.value}`)
      if (disposed || version !== sequence) return
      data.value = result[key]; total.value = result.total; error.value = ''
      if (page.value > 1 && !result[key].length) page.value = Math.max(1, Math.ceil(result.total / 20))
    } catch (e) { if (!disposed && version === sequence) { error.value = errorText(e); if (e.status === 401) consoleState.report(e) } }
    finally { if (version === sequence) loading.value = false }
  }
  watch(() => JSON.stringify(filters()), () => { if (page.value !== 1) page.value = 1; else load() })
  watch(page, load)
  watch(consoleState.revision, load)
  onMounted(load)
  onBeforeUnmount(() => { disposed = true; sequence++ })
  return { data, total, page, loading, error, query, load }
}
