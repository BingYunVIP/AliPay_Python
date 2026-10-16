<script setup>
import { inject, ref } from 'vue'
import { api, errorText, t } from '../state'
import { usePaged } from './useConsole'
import { amount, fullDate } from './format'
import Icon from '../components/Icon.vue'
import EmptyState from './EmptyState.vue'
import Pagination from './Pagination.vue'
import ConfirmDialog from './ConfirmDialog.vue'
const c = inject('console'), search = ref(''), editing = ref(null), createDialog = ref(null), email = ref(''), password = ref(''), busy = ref(false), error = ref('')
const rows = usePaged('/api/admin/users', 'users', () => ({ search: search.value }), c)
async function create() {
  busy.value = true; error.value = ''
  try { await api('/api/admin/users', { method: 'POST', body: JSON.stringify({ email: email.value, password: password.value }) }); password.value = ''; email.value = ''; createDialog.value.close(); await rows.load(); c.refresh(true); c.toast(t('普通用户已创建。', 'User account created.')) }
  catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
async function toggle() {
  busy.value = true; error.value = ''
  try { await api(`/api/admin/users/${editing.value.id}/access`, { method: 'POST', body: JSON.stringify({ disabled: !editing.value.disabled }) }); editing.value = null; await rows.load(); c.toast(t('用户访问权限已更新。', 'User access updated.')) }
  catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="console-page-heading"><div><h2>{{ t('用户管理', 'User management') }}</h2><p>{{ t('一个管理员，独立的用户账户与 API 权限。', 'One administrator. Separate user accounts and API access.') }}</p></div><button class="button" @click="error = ''; createDialog.showModal()"><Icon name="plus" :size="16" />{{ t('新增用户', 'Add user') }}</button></div>
  <section class="hig-card"><div class="table-toolbar"><div><h3>{{ t('接入用户', 'Connected users') }}</h3><span class="field-hint">{{ t('账户、密钥状态及累计交易', 'Accounts, key status, and lifetime payments') }}</span></div><label class="console-search compact-search"><Icon name="search" :size="16" /><input v-model.lazy="search" :placeholder="t('搜索邮箱', 'Search email')" :aria-label="t('搜索用户邮箱', 'Search user email')" /></label></div><p v-if="rows.error.value" class="error-box" role="alert">{{ rows.error.value }}</p><div v-if="rows.data.value.length" class="table-scroll"><table class="console-table user-table"><thead><tr><th>{{ t('用户', 'User') }}</th><th>{{ t('角色', 'Role') }}</th><th>{{ t('商户 ID', 'Merchant ID') }}</th><th>{{ t('订单数 / 累计收款', 'Orders / Revenue') }}</th><th>{{ t('状态', 'Access') }}</th><th class="align-right">{{ t('操作', 'Actions') }}</th></tr></thead><tbody><tr v-for="user in rows.data.value" :key="user.id"><td><div class="user-cell"><span class="table-avatar">{{ user.email[0].toUpperCase() }}</span><span><b>{{ user.email }}</b><small>{{ fullDate(user.created_at) }}</small></span></div></td><td><span class="role-pill">{{ user.role === 'admin' ? t('管理员', 'Administrator') : t('普通用户', 'User') }}</span></td><td><code>{{ user.merchant_id || '—' }}</code></td><td><span class="numeric">¥ {{ amount(user.revenue_cents) }}</span><small>{{ user.order_count }} {{ t('笔订单', 'orders') }}</small></td><td><span class="state-badge" :class="user.disabled ? 'failed' : 'delivered'"><i></i>{{ user.disabled ? t('已停用', 'Disabled') : t('正常', 'Active') }}</span></td><td class="align-right"><RouterLink class="text-link" :to="{path:'/dashboard',query:{tab:'keys',merchant_id:user.merchant_id}}">{{ t('接口配置','Merchant API') }}</RouterLink> <button v-if="user.role !== 'admin'" class="text-link" :class="{ 'text-red': !user.disabled }" @click="editing = user; error = ''">{{ user.disabled ? t('恢复访问', 'Enable') : t('停用', 'Disable') }}</button><span v-else class="muted">—</span></td></tr></tbody></table></div><EmptyState v-else icon="users" :title="rows.loading.value ? t('正在读取用户…', 'Loading users…') : t('没有匹配的用户', 'No matching users')" /><Pagination v-model:page="rows.page.value" :total="rows.total.value" :loading="rows.loading.value" /></section>
  <dialog ref="createDialog" class="console-dialog" :aria-label="t('新增普通用户', 'Add user')" @cancel.prevent="!busy && createDialog.close()" @close="password = ''"><div class="dialog-heading"><h2>{{ t('新增普通用户', 'Add user') }}</h2><button class="icon-button" :disabled="busy" :aria-label="t('关闭', 'Close')" @click="createDialog.close()"><Icon name="x" /></button></div><p>{{ t('用户登录后可配置自己的商户接口，查看订单和回调', 'Users can configure merchant credentials and view their orders and callbacks') }}</p><form @submit.prevent="create"><label for="new-email">{{ t('邮箱', 'Email') }}</label><input id="new-email" v-model="email" type="email" required autocomplete="off" maxlength="254" /><label for="new-password">{{ t('初始密码', 'Initial password') }}</label><input id="new-password" v-model="password" type="password" minlength="12" maxlength="128" required autocomplete="new-password" /><p class="field-hint">{{ t('至少 12 个字符。请通过安全渠道告知账户持有人。', 'At least 12 characters. Share securely with the account owner.') }}</p><p v-if="error" class="error-box" role="alert">{{ error }}</p><div class="dialog-actions"><button class="button" :disabled="busy"><Icon v-if="busy" name="loader" class="spin" :size="16" />{{ t('创建用户', 'Create user') }}</button></div></form></dialog>
  <ConfirmDialog :open="!!editing" :busy="busy" :destructive="!editing?.disabled" :title="editing?.disabled ? t('恢复这个用户的访问？', 'Restore user access?') : t('停用这个用户？', 'Disable this user?')" :description="editing?.disabled ? t('恢复后，用户重新登录即可使用商户接口', 'The user will need to sign in and generate a new API key.') : t('将注销登录会话并停用该商户接口，已有订单与支付通知继续保留和处理', 'Signs the user out and revokes their API key. Existing orders and payment notifications remain intact.')" @cancel="editing = null" @confirm="toggle"><p v-if="error" class="error-box" role="alert">{{ error }}</p></ConfirmDialog>
</template>
