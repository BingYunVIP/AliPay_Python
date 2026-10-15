const $ = (id) => document.getElementById(id)
const stage = { 'create.request': '① 向 Pay 提交创建请求', 'create.accepted': '② Pay 返回支付链接', 'create.failed': '创建请求失败', 'notify.verified': '③ 已验签并收到支付回调', 'notify.rejected': '回调校验未通过' }
const statuses = { CREATING: '正在创建', WAITING: '等待付款与回调', PAID: '已收到有效支付回调', CREATE_FAILED: '创建失败' }
const errors = { unauthorized: 'Pay API 密钥无效，请重新配置。', payment_not_configured: 'Pay 还未配置好支付宝。', invalid_merchant_callback: 'Pay 未允许此商户回调域名。', test_amount_exceeded: '金额超过 Pay 的测试上限。', pay_unreachable: '无法连接 Pay，请检查域名、网络与 Cloudflare 设置。', rate_limited: '操作频繁，请稍后再试。' }
function element(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node }
async function refresh() {
  try {
    const response = await fetch('/api/state'); if (!response.ok) throw new Error('读取失败，请刷新页面重新登录。')
    const data = await response.json()
    $('pay-origin').textContent = data.pay_origin; $('callback-url').textContent = data.callback_url
    $('config-status').textContent = `API 密钥：${data.key_configured ? '已配置' : '待配置'} · 回调验签公钥：${data.verification_key_configured ? '已配置' : '待配置'}`
    $('orders').replaceChildren()
    if (!data.orders.length) $('orders').append(element('p', '尚无订单。创建第一笔测试订单后，记录会出现在这里。', 'empty'))
    for (const order of data.orders) {
      const row = element('article', '', 'order')
      row.append(element('strong', `¥${order.amount}`), element('span', statuses[order.status] || order.status, order.status === 'PAID' ? 'paid' : 'status'), element('code', order.number))
      if (order.pay_url && order.status !== 'PAID') { const a = element('a', '打开支付宝收银台 ↗', 'pay-link'); a.href = order.pay_url; a.target = '_blank'; a.rel = 'noopener noreferrer'; row.append(a) }
      $('orders').append(row)
    }
    $('logs').replaceChildren()
    if (!data.logs.length) $('logs').append(element('p', '还没有日志。', 'empty'))
    for (const log of data.logs) {
      const details = element('details', '', 'log'), summary = element('summary', '')
      summary.append(element('b', stage[log.stage] || log.stage), element('time', new Date(log.time * 1000).toLocaleString('zh-CN')), element('code', log.number || '系统'))
      details.append(summary, element('pre', JSON.stringify(log.detail, null, 2))); $('logs').append(details)
    }
  } catch (error) { $('message').textContent = error.message }
}
$('create-form').addEventListener('submit', async event => {
  event.preventDefault(); $('submit').disabled = true; $('message').textContent = '正在向 Pay 创建订单…'
  try {
    const response = await fetch('/api/orders', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Demo-Request': '1' }, body: JSON.stringify({ amount: $('amount').value, product: $('product').value }) })
    const data = await response.json()
    if (!response.ok) throw new Error(errors[data.error] || `创建失败：${data.error || response.status}`)
    $('message').textContent = '订单已创建。请在右侧打开支付宝收银台，付款后观察回调记录。'
    await refresh()
  } catch (error) { $('message').textContent = error.message }
  finally { $('submit').disabled = false }
})
$('refresh').addEventListener('click', refresh)
refresh()
setInterval(() => { if (!document.hidden) refresh() }, 5000)
