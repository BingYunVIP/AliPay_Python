import { computed, reactive, ref, watch } from 'vue'

export const locale = ref(document.documentElement.lang === 'en' ? 'en' : 'zh')
export const dark = ref(document.documentElement.classList.contains('dark'))
export const auth = reactive({ user: null, csrf: '', key: null })
export const t = (zh, en) => locale.value === 'zh' ? zh : en
export const isAdmin = computed(() => auth.user?.role === 'admin')

watch(locale, value => {
  document.documentElement.lang = value === 'zh' ? 'zh-CN' : 'en'
  document.title = value === 'zh' ? '冰云支付 · Pay' : 'Pay · BingYun'
  try { localStorage.setItem('bypay-locale', value) } catch { /* Storage is optional. */ }
})
watch(dark, value => {
  document.documentElement.classList.toggle('dark', value)
  document.querySelector('meta[name="theme-color"]').content = value ? '#0e1116' : '#f4f5f7'
  try { localStorage.setItem('bypay-theme', value ? 'dark' : 'light') } catch { /* Storage is optional. */ }
})

export class ApiError extends Error {
  constructor(code, status) { super(code); this.status = status }
}

export async function api(path, options = {}) {
  let response
  try {
    response = await fetch(path, { ...options, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': auth.csrf, ...options.headers },
      signal: options.signal ?? AbortSignal.timeout(20000),
    })
  } catch { throw new ApiError('network_error', 0) }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401) { auth.user = null; auth.csrf = ''; auth.key = null }
    throw new ApiError(data.error || 'request_failed', response.status)
  }
  return data
}

let initPromise
export function initializeAuth() {
  return initPromise ??= api('/api/auth/me').then(data => {
    auth.user = data.user; auth.csrf = data.csrf_token; auth.key = data.api_key
  }).catch(() => {})
}

export const errors = {
  network_error: ['连接服务失败，请检查网络后重试。', 'Unable to reach the service. Please retry.'],
  invalid_credentials: ['邮箱或密码不正确。', 'Incorrect email or password.'],
  invalid_email: ['请输入有效的邮箱地址。', 'Enter a valid email address.'],
  password_too_short: ['密码至少需要 12 个字符。', 'Use at least 12 characters for your password.'],
  email_unavailable: ['该邮箱无法注册，请尝试登录。', 'This email is unavailable. Try signing in.'],
  registration_closed: ['暂未开放注册，请联系冰云支持。', 'Registration is closed. Contact support.'],
  rate_limited: ['操作较频繁，请稍后再试。', 'Too many attempts. Please try again later.'],
  payment_not_configured: ['支付宝尚未配置完成，请由管理员检查回调配置。', 'Alipay is not configured. Ask the administrator to check setup.'],
  payment_provider_error: ['支付宝接口返回异常，请查看事件日志并核对配置。', 'Alipay returned an error. Review the event log and configuration.'],
  payment_mismatch: ['交易信息核对失败，订单未更新，请检查日志。', 'Payment details do not match. The order was not updated.'],
  invalid_amount: ['金额须为大于 0、最多两位小数的数字。', 'Enter a positive amount with at most two decimal places.'],
  test_amount_exceeded: ['金额超过当前测试上限。', 'Amount exceeds the current test limit.'],
  order_conflict: ['该订单号已被使用，请创建新订单。', 'This order number is already in use. Create a new order.'],
  unauthorized: ['登录已过期，请重新登录。', 'Your session expired. Please sign in again.'],
  invalid_csrf: ['登录状态已更新，请刷新页面重试。', 'Please reload the page and try again.'],
  invalid_origin: ['网站域名与服务器配置不一致，请检查 PUBLIC_BASE_URL。', 'The site domain differs from PUBLIC_BASE_URL.'],
  unsupported_product: ['此渠道或产品暂未开放。', 'This payment product is not available yet.'],
}
export const errorText = (error) => t(...(errors[error.message] || ['操作未完成，请稍后重试。', 'The request could not be completed. Please retry.']))
export async function copyText(value) {
  await navigator.clipboard.writeText(value)
}
