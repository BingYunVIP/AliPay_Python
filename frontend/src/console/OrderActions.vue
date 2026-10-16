<script setup>
import { computed, inject, ref } from 'vue'
import { api, errorText, isAdmin, t } from '../state'
import ConfirmDialog from './ConfirmDialog.vue'

// Row actions in three fixed slots: details | refund (or settle a pending refund) | delete (or restore)
const props = defineProps({ order: Object })
const c = inject('console'), action = ref(''), busy = ref(false), error = ref(''), refundAmount = ref(''), reason = ref(t('订单退款', 'Order refund')), refundNo = ref(''), retrying = ref(false)
const refundAllowed = computed(() => isAdmin.value || c.caps.value?.merchant_refund)
const canRefund = computed(() => refundAllowed.value && !props.order.deleted_at && ['PAID', 'PARTIALLY_REFUNDED'].includes(props.order.status) && Number(props.order.refundable_amount) > 0)
const canSettle = computed(() => refundAllowed.value && props.order.status === 'REFUNDING')
const toCents = value => { const m = /^(\d{1,9})(?:\.(\d{1,2}))?$/.exec(String(value).trim()); return m ? Number(m[1]) * 100 + Number((m[2] || '').padEnd(2, '0')) : NaN }
const entered = computed(() => toCents(refundAmount.value)), refundable = computed(() => toCents(props.order.refundable_amount))
const amountError = computed(() => {
  if (action.value !== 'refund' || retrying.value || !refundAmount.value.trim()) return ''
  if (Number.isNaN(entered.value)) return t('请输入金额，最多两位小数', 'Enter an amount with at most two decimals')
  if (entered.value <= 0) return t('退款金额必须大于 0', 'The refund must be greater than zero')
  if (entered.value > refundable.value) return refundable.value === toCents(props.order.amount) ? t(`退款金额不能超过支付金额 ￥${props.order.amount}`, `The refund cannot exceed the paid amount ￥${props.order.amount}`) : t(`退款金额不能超过可退金额 ￥${props.order.refundable_amount}`, `The refund cannot exceed the refundable amount ￥${props.order.refundable_amount}`)
  return ''
})
const ready = computed(() => action.value !== 'refund' || retrying.value || (entered.value > 0 && !amountError.value && !!reason.value.trim()))
const titles = computed(() => ({ refund: retrying.value ? t('继续处理退款', 'Continue the refund') : t('确认单笔退款', 'Confirm refund'), restore: t('恢复订单', 'Restore order'), delete: t('删除订单', 'Delete order') })[action.value])
const labels = computed(() => ({ refund: t('确认退款', 'Confirm refund'), restore: t('确认恢复', 'Restore'), delete: t('确认删除', 'Delete') })[action.value])
const descriptions = computed(() => ({ restore: t('恢复后重新显示在订单列表', 'Restore this order to the order list'), delete: t('移入已删除，可恢复，账务与回调记录继续保留', 'Move to deleted; the order can be restored and accounting records remain') })[action.value] || '')

function open(name) {
  retrying.value = false; action.value = name; error.value = ''
  if (name === 'refund') { refundAmount.value = ''; reason.value = t('订单退款', 'Order refund'); refundNo.value = `RF_${crypto.randomUUID().replaceAll('-', '')}` }
}
// Check the original refund first; only an unresolved one can be re-sent with the same request number.
async function settle() {
  busy.value = true
  try {
    const data = await api(`/api/v1/payments/${props.order.out_trade_no}/refund-status`, { method: 'POST' })
    if (!data.refund || !['PENDING', 'UNKNOWN'].includes(data.refund.status)) { c.toast(t('退款状态已更新', 'Refund status updated')); await c.refresh(true); return }
    refundNo.value = data.refund.refund_no; refundAmount.value = data.refund.amount; reason.value = data.refund.reason
    retrying.value = true; action.value = 'refund'; error.value = ''
  } catch (e) { c.toast(errorText(e)) }
  finally { busy.value = false }
}
async function execute() {
  busy.value = true; error.value = ''
  const number = props.order.out_trade_no
  try {
    if (action.value === 'refund') {
      const result = await api(`/api/v1/payments/${number}/refund`, { method: 'POST', body: JSON.stringify({ refund_no: refundNo.value, amount: refundAmount.value.trim(), reason: reason.value.trim() }) })
      if (result.refund.status === 'FAILED') throw new Error('refund_provider_error')
      c.toast(result.refund.status === 'SUCCESS' ? t('退款已确认', 'Refund confirmed') : t('退款结果待确认，后台会继续查询', 'Refund pending confirmation; verification continues'))
    } else {
      await api(`/api/v1/payments/${number}${action.value === 'restore' ? '/restore' : ''}`, { method: action.value === 'restore' ? 'POST' : 'DELETE' })
      c.toast(action.value === 'restore' ? t('订单已恢复', 'Order restored') : t('订单已移入已删除', 'Order moved to deleted'))
    }
    action.value = ''
    await c.refresh(true)
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="action-triple">
    <span><button type="button" class="text-link" @click="c.selectedOrder.value = order">{{ t('详情', 'Details') }}</button></span>
    <span>
      <button v-if="canRefund" type="button" class="text-link" :disabled="busy" @click="open('refund')">{{ t('退款', 'Refund') }}</button>
      <button v-else-if="canSettle" type="button" class="text-link" :disabled="busy" @click="settle">{{ t('处理退款', 'Resolve') }}</button>
    </span>
    <span>
      <button v-if="order.deleted_at" type="button" class="text-link" :disabled="busy" @click="open('restore')">{{ t('恢复', 'Restore') }}</button>
      <button v-else type="button" class="text-link danger-link" :disabled="busy || order.status === 'REFUNDING'" @click="open('delete')">{{ t('删除', 'Delete') }}</button>
    </span>
  </div>
  <ConfirmDialog v-if="action" open :busy="busy" :disabled="!ready" :destructive="action === 'delete'" :title="titles" :description="descriptions" :label="labels" @cancel="action = ''" @confirm="execute">
    <div class="confirm-summary">
      <div><span>{{ t('商品名称', 'Item') }}</span><b>{{ order.subject }}</b></div>
      <div class="confirm-amount"><b><small>￥</small>{{ order.amount }}</b></div>
      <div><span>{{ t('商户订单号', 'Order number') }}</span><code>{{ order.out_trade_no }}</code></div>
    </div>
    <template v-if="action === 'refund'">
      <label :for="`refund-amount-${order.out_trade_no}`">{{ t('退款金额（CNY）', 'Refund amount (CNY)') }}</label>
      <input :id="`refund-amount-${order.out_trade_no}`" v-model="refundAmount" inputmode="decimal" autocomplete="off" :placeholder="t(`最多 ${order.refundable_amount}`, `Up to ${order.refundable_amount}`)" :aria-invalid="!!amountError" :aria-describedby="amountError ? `refund-error-${order.out_trade_no}` : undefined" :disabled="busy || retrying" />
      <p v-if="amountError" :id="`refund-error-${order.out_trade_no}`" class="field-error" role="alert">{{ amountError }}</p>
      <label :for="`refund-reason-${order.out_trade_no}`">{{ t('退款原因', 'Refund reason') }}</label>
      <input :id="`refund-reason-${order.out_trade_no}`" v-model="reason" maxlength="128" autocomplete="off" :disabled="busy || retrying" />
    </template>
    <p v-if="error" class="error-box" role="alert">{{ error }}</p>
  </ConfirmDialog>
</template>
