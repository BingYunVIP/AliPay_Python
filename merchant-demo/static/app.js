const $ = id => document.getElementById(id)
const stages = { 'app.started': '测试站启动', 'config.checked': 'API Key 验证通过', 'config.saved': '连接配置已保存', 'config.rejected': '配置验证未通过', 'create.request': '向 Pay 提交订单', 'create.accepted': 'Pay 返回支付链接', 'create.failed': '创建订单失败', 'notify.verified': '回调验签通过', 'notify.rejected': '回调校验未通过' }
const statuses = { CREATING: '正在创建', WAITING: '等待付款与回调', PAID: '已收到有效支付回调', CREATE_FAILED: '创建失败' }
const errors = {
  pay_key_missing: '请在上方填写当前 Pay 的 API Key，然后验证并保存',
  unauthorized: '这个 API Key 未被当前 Pay 接受，请检查 Pay 地址，并复制该站点控制台生成的密钥',
  invalid_api_key_format: '请粘贴完整的 byp_ 开头 API Key，不要填写支付宝 APPID 或管理员密码',
  key_required_for_new_origin: '切换 Pay 域名时，请填写对应站点的 API Key',
  invalid_pay_origin: '请输入 Pay 服务地址，例如 http://127.0.0.1:888，不需要附加路径',
  invalid_callback_origin: '请输入测试站地址，例如 http://127.0.0.1:8100，不需要附加 /notify',
  pay_unreachable: '无法连接 Pay，请先启动本机 Pay，并检查地址和端口',
  public_key_unavailable: 'API Key 已通过验证，但未能获取回调公钥，请检查 Pay 是否正常运行',
  pay_validation_unavailable: '当前地址未返回有效的 Pay API 响应，请检查端口是否指向 Pay',
  pay_access_denied: '请求被 Pay 或代理拦截，本地开发请使用本机 Pay 地址',
  pay_redirect_rejected: 'Pay 地址发生了重定向，请填写直接访问 Pay 的完整地址',
  config_write_failed: '配置保存失败，请检查测试站目录的写入权限，原配置已保留',
  local_configuration_only: '请通过本机地址 http://127.0.0.1:8100 打开测试站修改配置',
  payment_not_configured: '请先在本机 Pay 控制台配置支付宝渠道',
  invalid_merchant_callback: '本机 Pay 未接受此回调地址，请确认 Pay 已更新并重新启动，测试站地址使用 http://127.0.0.1:8100',
  test_amount_exceeded: '金额超过 Pay 的测试上限',
  amount_between_001_and_10: '测试金额应在 0.01 到 10 元之间',
  rate_limited: '操作频繁，请稍后重试',
}
const keyStates = { missing: '未填写密钥', unverified: '等待验证', valid: 'API Key 已验证', invalid: 'API Key 无效' }
let current = {}, defaults = {}, configBusy = false, orderBusy = false, dirty = false
function element(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node }
function message(id, text, error = false) { $(id).textContent = text; $(id).classList.toggle('error', error); $(id).setAttribute('role', error ? 'alert' : 'status') }
function buttons() {
  $('config-fields').disabled = configBusy || !current.can_manage
  $('save-config').disabled = configBusy; $('check-config').disabled = configBusy; $('local-config').disabled = configBusy
  $('submit').disabled = orderBusy || configBusy || dirty || current.key_status !== 'valid' || !current.verification_key_configured
  $('save-config').textContent = configBusy ? '正在验证' : '验证并保存'
  $('config-form').setAttribute('aria-busy', String(configBusy))
}
function consoleLink() {
  try {
    const url = new URL($('config-pay-origin').value)
    if (!['http:', 'https:'].includes(url.protocol)) return
    $('key-console').href = new URL('/dashboard?tab=keys', url.origin).href
  } catch { /* retain the last valid link while the address is being edited */ }
}
function renderConfig(data, fill = false) {
  current = data
  $('site-mode').textContent = data.can_manage ? '本地开发' : '独立测试站'
  $('pay-origin').textContent = data.pay_origin; $('callback-url').textContent = data.callback_url
  $('pay-home').href = data.pay_origin
  $('connection-state').textContent = keyStates[data.key_status] || '等待验证'
  $('connection-state').dataset.state = data.key_status
  $('key-hint').textContent = data.key_hint || ''
  $('config-status').textContent = `${keyStates[data.key_status] || '等待验证'} · 回调公钥${data.verification_key_configured ? '已保存' : '待获取'}`
  if (fill) { $('config-pay-origin').value = data.pay_origin; $('config-demo-origin').value = data.demo_origin; consoleLink() }
  $('config-fields').disabled = !data.can_manage
  if (!data.can_manage) message('config-message', errors.local_configuration_only, true)
  buttons()
}
async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Demo-Request': '1' }, body: JSON.stringify(payload) })
  let data
  try { data = await response.json() } catch { throw new Error('测试站未返回有效响应，请检查运行终端') }
  if (!response.ok) { const error = new Error(errors[data.error] || `请求失败：${data.error || response.status}`); error.code = data.error; throw error }
  return data
}
async function refresh() {
  try {
    const data = await api('/api/state'); renderConfig(data)
    $('orders').replaceChildren()
    if (!data.orders.length) $('orders').append(element('p', '尚无测试订单', 'empty'))
    for (const order of data.orders) {
      const row = element('article', '', 'order')
      row.append(element('strong', `¥${order.amount}`), element('span', statuses[order.status] || order.status, order.status === 'PAID' ? 'paid' : 'status'), element('code', order.number))
      if (order.pay_url && order.status !== 'PAID') { const a = element('a', '打开支付宝收银台 ↗', 'pay-link'); a.href = order.pay_url; a.target = '_blank'; a.rel = 'noopener noreferrer'; row.append(a) }
      $('orders').append(row)
    }
    const open = new Set([...$('logs').querySelectorAll('details[open]')].map(node => node.dataset.id))
    $('logs').replaceChildren()
    if (!data.logs.length) $('logs').append(element('p', '暂无日志', 'empty'))
    for (const log of data.logs) {
      const details = element('details', '', 'log'), summary = element('summary', '')
      details.dataset.id = String(log.id); details.open = open.has(String(log.id))
      summary.append(element('b', stages[log.stage] || log.stage), element('time', new Date(log.time * 1000).toLocaleString('zh-CN')), element('code', log.number || '系统'))
      details.append(summary, element('pre', JSON.stringify(log.detail, null, 2))); $('logs').append(details)
    }
  } catch (error) { message('message', error.message, true) }
}
async function configure(save, automatic = false) {
  if (configBusy || !current.can_manage || !$('config-form').reportValidity()) return
  configBusy = true; buttons()
  message('config-message', '正在验证 API Key 并获取回调公钥')
  $('config-api-key').removeAttribute('aria-invalid')
  const payload = { pay_origin: $('config-pay-origin').value, demo_origin: $('config-demo-origin').value, api_key: $('config-api-key').value }
  try {
    await api(save ? '/api/config' : '/api/config/check', payload)
    if (save) { $('config-api-key').value = ''; $('config-api-key').type = 'password'; $('toggle-key').textContent = '显示'; $('toggle-key').setAttribute('aria-pressed', 'false'); $('toggle-key').setAttribute('aria-label', '显示 API Key'); dirty = false }
    const data = await api('/api/config'); renderConfig(data, save)
    message('config-message', save ? '配置已保存并立即生效，可以创建测试订单' : dirty ? '连接验证通过，点击「验证并保存」应用修改' : 'API Key 与回调公钥已就绪，可以创建测试订单')
  } catch (error) {
    message('config-message', error.message, true)
    if (['unauthorized', 'invalid_api_key_format', 'pay_key_missing'].includes(error.code)) { $('config-api-key').setAttribute('aria-invalid', 'true'); if (!automatic) $('config-api-key').focus() }
  } finally { configBusy = false; buttons(); await refresh() }
}
$('config-form').addEventListener('submit', event => { event.preventDefault(); configure(true) })
$('config-form').addEventListener('input', () => { dirty = true; consoleLink(); message('config-message', '修改尚未保存'); buttons() })
$('check-config').addEventListener('click', () => configure(false))
$('local-config').addEventListener('click', () => { $('config-pay-origin').value = defaults.local_pay_origin; $('config-demo-origin').value = defaults.local_demo_origin; dirty = true; consoleLink(); message('config-message', '已填入本地地址，验证并保存后生效'); buttons() })
$('toggle-key').addEventListener('click', () => { const show = $('config-api-key').type === 'password'; $('config-api-key').type = show ? 'text' : 'password'; $('toggle-key').textContent = show ? '隐藏' : '显示'; $('toggle-key').setAttribute('aria-pressed', String(show)); $('toggle-key').setAttribute('aria-label', show ? '隐藏 API Key' : '显示 API Key') })
$('create-form').addEventListener('submit', async event => {
  event.preventDefault(); orderBusy = true; buttons(); message('message', '正在向 Pay 创建订单')
  try {
    await api('/api/orders', { amount: $('amount').value, product: $('product').value })
    message('message', '订单已创建，打开支付宝收银台付款后可查看回调记录')
  } catch (error) {
    message('message', error.message, true)
    if (['unauthorized', 'pay_key_missing'].includes(error.code)) { message('config-message', error.message, true); $('config-api-key').focus() }
  } finally { orderBusy = false; await refresh(); buttons() }
})
$('refresh').addEventListener('click', refresh)
async function start() {
  try {
    defaults = await api('/api/config'); renderConfig(defaults, true); await refresh()
    if (defaults.key_configured && defaults.can_manage) await configure(false, true)
  } catch (error) { message('config-message', error.message, true) }
}
start()
setInterval(() => { if (!document.hidden && !configBusy) refresh() }, 5000)
