<script setup>
import { computed, inject, reactive, ref, watch } from 'vue'
import { api, errorText, t } from '../state'
import { usePaged } from './useConsole'
import { amount, fullDate } from './format'
import Icon from '../components/Icon.vue'
import EmptyState from './EmptyState.vue'
import Pagination from './Pagination.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import MerchantDialog from './MerchantDialog.vue'
const c = inject('console'), search = ref(''), editing = ref(null), createDialog = ref(null), busy = ref(false), error = ref('')
const merchantDialog = ref(null)
const reviewing = ref(null), reviewStatus = ref('approved'), reviewNote = ref(''), riskUser = ref(null), payUser = ref(null)
// 新增商户沿用 Epay admin/uset.php?my=add 的字段顺序：登录账号 → 网站域名 → 功能开关
const blank = () => ({ email: '', password: '', domains: [''], whitelist: true, disabled: false, payEnabled: true, deposit: '0.00' })
const form = reactive(blank())
const policy = ref({ deposit_minimum: '10.00', fee_rate: '1.50', fee_minimum: '0.01', merchant_domain_limit: 1 })
const domainLimit = computed(() => Math.max(1, Number(policy.value.merchant_domain_limit) || 1))
const hasDomain = computed(() => form.domains.some(value => value.trim()))
const belowMinimum = computed(() => Number(form.deposit || 0) < Number(policy.value.deposit_minimum))
// 保证金低于门槛时默认停用商户状态，管理员仍可手动改回
watch(() => form.deposit, () => { form.disabled = belowMinimum.value })
function rate(user) { return user.order_count ? (user.paid_count / user.order_count * 100).toFixed(1) : '—' }
async function openCreate() {
  error.value = ''; Object.assign(form, blank()); createDialog.value.showModal()
  try { policy.value = { ...policy.value, ...await api('/api/admin/policy') } } catch { /* 保留默认值 */ }
  form.disabled = belowMinimum.value
}
async function create() {
  busy.value = true; error.value = ''
  try {
    await api('/api/admin/users', { method: 'POST', body: JSON.stringify({ email: form.email, password: form.password, domains: form.domains.map(value => value.trim()).filter(Boolean), callback_whitelist: form.whitelist, disabled: form.disabled, pay_enabled: form.payEnabled, deposit: form.deposit || '0.00' }) })
    createDialog.value.close(); Object.assign(form, blank()); await rows.load(); c.refresh(true); c.toast(t('商户已创建', 'Merchant created'))
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
// 审核备注默认带上实时读取的保证金余额，提交后写入系统日志
async function openReview(user) {
  reviewing.value = user; reviewStatus.value = 'approved'; reviewNote.value = ''; error.value = ''
  try { reviewNote.value = `${t('商户保证金余额：', 'Deposit balance: ')}${(await api(`/api/admin/users/${user.id}/deposit`)).deposit}` }
  catch (e) { error.value = errorText(e) }
}
async function review() { busy.value = true; error.value = ''; try { await api(`/api/admin/users/${reviewing.value.id}/review`, { method: 'POST', body: JSON.stringify({ status: reviewStatus.value, note: reviewNote.value }) }); reviewing.value = null; await rows.load() } catch (e) { error.value = errorText(e) } finally { busy.value = false } }
async function togglePay() {
  const user = payUser.value
  busy.value = true; error.value = ''
  try {
    await api(`/api/admin/users/${user.id}/pay-access`, { method: 'POST', body: JSON.stringify({ pay_enabled: !user.pay_enabled }) })
    payUser.value = null; await rows.load()
    c.toast(user.pay_enabled ? t('已关闭商户支付权限', 'Payment permission disabled') : t('已开启商户支付权限', 'Payment permission enabled'))
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
function openOrders(user) { c.navigate('orders', { merchant: user.merchant_id || user.email }) }
function reviewLabel(user) { return ({ pending: t('待审核', 'Pending'), approved: t('已审核', 'Approved'), rejected: t('未通过', 'Rejected') })[user.review] || user.review }
function reviewTone(user) { return ({ pending: 'wait', approved: 'delivered', rejected: 'failed' })[user.review] || 'neutral' }
async function releaseRisk() { busy.value = true; try { await api(`/api/admin/users/${riskUser.value.id}/risk-release`, { method: 'POST' }); riskUser.value = null; await rows.load() } catch (e) { error.value = errorText(e) } finally { busy.value = false } }
const rows = usePaged('/api/admin/users', 'users', () => ({ search: search.value }), c)
async function toggle() {
  busy.value = true; error.value = ''
  try { await api(`/api/admin/users/${editing.value.id}/access`, { method: 'POST', body: JSON.stringify({ disabled: !editing.value.disabled }) }); editing.value = null; await rows.load(); c.toast(t('用户访问权限已更新。', 'User access updated.')) }
  catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('用户管理', 'User management') }}</h2><p>{{ t('一个管理员，独立的用户账户与 API 权限。', 'One administrator. Separate user accounts and API access.') }}</p></div><button class="button" @click="openCreate"><Icon name="plus" :size="16" />{{ t('新增用户', 'Add user') }}</button></div>
  <section class="hig-card"><div class="table-toolbar"><div><h3>{{ t('接入用户', 'Connected users') }}</h3><span class="field-hint">{{ t('账户、密钥状态及累计交易', 'Accounts, key status, and lifetime payments') }}</span></div><label class="console-search compact-search"><Icon name="search" :size="16" /><input v-model.lazy="search" :placeholder="t('搜索邮箱', 'Search email')" :aria-label="t('搜索用户邮箱', 'Search user email')" /></label></div><p v-if="rows.error.value" class="error-box" role="alert">{{ rows.error.value }}</p><div v-if="rows.data.value.length" class="table-scroll"><table class="console-table user-table"><thead><tr><th>{{ t('用户', 'User') }}</th><th>{{ t('角色', 'Role') }}</th><th>{{ t('商户 ID', 'Merchant ID') }}</th><th>{{ t('累计收款', 'Revenue') }}</th><th>{{ t('成功率 / 订单数', 'Success / Orders') }}</th><th>{{ t('状态', 'Access') }}</th><th>{{ t('准入状态','Review') }}</th><th class="align-right"><span class="action-head">{{ t('操作', 'Actions') }}</span></th></tr></thead><tbody><tr v-for="user in rows.data.value" :key="user.id"><td><div class="user-cell"><span class="table-avatar">{{ user.email[0].toUpperCase() }}</span><span><b>{{ user.email }}</b><small>{{ fullDate(user.created_at) }}</small></span></div></td><td><span class="role-pill">{{ user.role === 'admin' ? t('管理员', 'Administrator') : t('普通用户', 'User') }}</span></td><td><code>{{ user.merchant_id || '—' }}</code><small v-if="user.domains" class="domain-links"><a v-for="host in user.domains.split('、')" :key="host" :href="`https://${host}`" target="_blank" rel="noopener noreferrer">{{ host }}</a></small></td><td><button type="button" class="text-link numeric" :disabled="!user.merchant_id" @click="openOrders(user)" :title="t('查看该商户的订单', 'Open this merchant\'s orders')">¥ {{ amount(user.revenue_cents) }}</button></td><td><button type="button" class="text-link numeric" :disabled="!user.merchant_id" @click="openOrders(user)">{{ rate(user) }}{{ user.order_count ? '%' : '' }}</button><small>{{ user.order_count }} {{ t('笔订单', 'orders') }}</small></td><td><span class="state-badge" :class="user.disabled ? 'failed' : 'delivered'"><i></i>{{ user.disabled ? t('已停用', 'Disabled') : t('正常', 'Active') }}</span><small><button type="button" class="text-link" :class="{ 'text-red': !user.pay_enabled }" :disabled="busy || user.role === 'admin'" @click="payUser = user">{{ user.pay_enabled ? t('支付权限：开启', 'Pay: on') : t('支付权限：关闭', 'Pay: off') }}</button></small></td><td><span class="state-badge" :class="reviewTone(user)"><i></i>{{ reviewLabel(user) }}</span><small v-if="user.review === 'pending'">{{ t('未审核不能发起新订单', 'No new orders until reviewed') }}</small><small v-if="user.fee_due>user.fee_paid">{{ t('注册费待支付','Registration fee due') }}</small><small v-if="user.risk_hold">{{ t('风控暂停','Risk hold') }} <button class="text-link" @click="riskUser=user">{{ t('解除','Release') }}</button></small></td><td class="align-right"><div class="action-triple"><span><button v-if="user.role!=='admin'" type="button" class="text-link" @click="openReview(user)">{{ t('审核','Review') }}</button></span><span><button type="button" class="text-link" :disabled="!user.merchant_id" @click="merchantDialog.open(user.merchant_id)">{{ t('接口配置','Merchant API') }}</button></span><span><button v-if="user.role!=='admin'" type="button" class="text-link" :class="{ 'text-red': !user.disabled }" @click="editing = user; error = ''">{{ user.disabled ? t('恢复访问', 'Enable') : t('停用', 'Disable') }}</button></span></div></td></tr></tbody></table></div><EmptyState v-else icon="users" :title="rows.loading.value ? t('正在读取用户…', 'Loading users…') : t('没有匹配的用户', 'No matching users')" /><Pagination v-model:page="rows.page.value" :total="rows.total.value" :loading="rows.loading.value" /></section>
  <MerchantDialog ref="merchantDialog" />
  <dialog ref="createDialog" class="console-dialog merchant-create" :aria-label="t('新增商户', 'Add merchant')" @cancel.prevent="!busy && createDialog.close()" @close="error = ''">
    <div class="dialog-heading"><h2>{{ t('新增商户', 'Add merchant') }}</h2><button class="icon-button" :disabled="busy" :aria-label="t('关闭', 'Close')" @click="createDialog.close()"><Icon name="x" /></button></div>
    <p>{{ t('创建登录账号，并设置网站域名与收款权限', 'Create a sign-in account and set its site domain and payment permissions') }}</p>
    <form @submit.prevent="create">
      <label for="new-email">{{ t('邮箱（登录账号）', 'Email (sign-in)') }}</label>
      <input id="new-email" v-model="form.email" type="email" required autocomplete="off" maxlength="254" />
      <label for="new-password">{{ t('登录密码', 'Password') }}</label>
      <input id="new-password" v-model="form.password" type="password" minlength="12" maxlength="128" required autocomplete="new-password" />
      <p class="field-hint">{{ t('至少 12 个字符。请通过安全渠道告知账户持有人。', 'At least 12 characters. Share securely with the account owner.') }}</p>
      <label>{{ t('网站域名', 'Site domain') }}</label>
      <div v-for="(_, index) in form.domains" :key="index" class="domain-row"><input v-model="form.domains[index]" :placeholder="t('shop.example.com，可粘贴完整网址', 'shop.example.com — a full URL is fine')" maxlength="300" autocomplete="off" spellcheck="false" /><button v-if="form.domains.length > 1" type="button" class="icon-button" :aria-label="t('删除这个域名', 'Remove this domain')" @click="form.domains.splice(index, 1)"><Icon name="x" :size="16" /></button></div>
      <button v-if="form.domains.length < domainLimit" type="button" class="text-link" @click="form.domains.push('')">{{ t('添加域名', 'Add domain') }}</button>
      <p class="field-hint">{{ t(`最多 ${domainLimit} 个，可留空；自动去掉协议与路径，保存后可直接点击访问`, `Up to ${domainLimit}; leave blank if unused. Scheme and path are removed and the domain stays clickable`) }}</p>
      <label v-if="hasDomain" class="checkbox-row"><input type="checkbox" v-model="form.whitelist" />{{ t('加入商户回调域名白名单', 'Add to the merchant callback domain allowlist') }}</label>
      <div class="form-grid">
        <div><label for="new-status">{{ t('商户状态', 'Merchant status') }}</label><select id="new-status" v-model="form.disabled"><option :value="false">{{ t('正常', 'Active') }}</option><option :value="true">{{ t('停用', 'Disabled') }}</option></select></div>
        <div><label for="new-pay">{{ t('支付权限', 'Payment permission') }}</label><select id="new-pay" v-model="form.payEnabled"><option :value="true">{{ t('开启', 'On') }}</option><option :value="false">{{ t('关闭', 'Off') }}</option></select></div>
      </div>
      <label for="new-deposit">{{ t('商户保证金余额', 'Merchant deposit balance') }}</label>
      <div class="currency-input"><span>¥</span><input id="new-deposit" v-model="form.deposit" inputmode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" maxlength="12" autocomplete="off" /></div>
      <p class="field-hint">{{ t(`低于 ¥ ${policy.deposit_minimum} 时默认停用商户状态；手续费 ${policy.fee_rate}%，最低 ¥ ${policy.fee_minimum}`, `Below ¥${policy.deposit_minimum} the merchant starts disabled; fee ${policy.fee_rate}% with a ¥${policy.fee_minimum} minimum`) }}</p>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
      <div class="dialog-actions"><button type="button" class="button button-secondary" :disabled="busy" @click="createDialog.close()">{{ t('取消', 'Cancel') }}</button><button class="button" :disabled="busy"><Icon v-if="busy" name="loader" class="spin" :size="16" />{{ t('创建商户', 'Create merchant') }}</button></div>
    </form>
  </dialog>
  <ConfirmDialog :open="!!reviewing" :busy="busy" title="审核商户" description="审核结果影响商户发起新订单的权限，已有订单与回调保留" @cancel="reviewing=null" @confirm="review"><label>审核结果<select v-model="reviewStatus"><option value="approved">通过</option><option value="rejected">拒绝</option></select></label><label>审核备注<input v-model="reviewNote" maxlength="240"/></label><p v-if="error" class="error-box" role="alert">{{ error }}</p></ConfirmDialog><ConfirmDialog :open="!!riskUser" :busy="busy" title="解除商户风控暂停" description="请先确认触发原因已处理，后续请求仍会经过配置的风控检测" @cancel="riskUser=null" @confirm="releaseRisk"/><ConfirmDialog :open="!!payUser" :busy="busy" :destructive="payUser?.pay_enabled" :title="payUser?.pay_enabled ? t('关闭这个商户的支付权限？', 'Turn payment off for this merchant?') : t('开启这个商户的支付权限？', 'Turn payment on for this merchant?')" :description="t('用于防止刷单：关闭后该商户无法发起新订单，已有订单的查询、回调与退款不受影响', 'Guards against fake orders: no new orders, while existing orders, callbacks and refunds keep working')" @cancel="payUser = null" @confirm="togglePay"><p v-if="error" class="error-box" role="alert">{{ error }}</p></ConfirmDialog>
  <ConfirmDialog :open="!!editing" :busy="busy" :destructive="!editing?.disabled" :title="editing?.disabled ? t('恢复这个用户的访问？', 'Restore user access?') : t('停用这个用户？', 'Disable this user?')" :description="editing?.disabled ? t('恢复后，用户重新登录即可使用商户接口', 'The user will need to sign in and generate a new API key.') : t('将注销登录会话并停用该商户接口，已有订单与支付通知继续保留和处理', 'Signs the user out and revokes their API key. Existing orders and payment notifications remain intact.')" @cancel="editing = null" @confirm="toggle"><p v-if="error" class="error-box" role="alert">{{ error }}</p></ConfirmDialog>
</template>
<style scoped>
.merchant-create { max-width: 520px; }
.domain-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.domain-row input { flex: 1; min-width: 0; }
.checkbox-row { display: flex; align-items: center; gap: 9px; margin-top: 16px; font-size: 13px; }
.checkbox-row input { width: 16px; height: 16px; min-height: 0; }
.merchant-create .text-link { margin-bottom: 10px; }
.domain-links { display: flex; flex-wrap: wrap; gap: 2px 8px; }
.domain-links a { color: var(--accent); }
</style>
