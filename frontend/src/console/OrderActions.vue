<script setup>
import { computed, inject, ref } from 'vue'
import { api, errorText, isAdmin, t } from '../state'
import ConfirmDialog from './ConfirmDialog.vue'
const props = defineProps({ order: Object, details: { type: Boolean, default: true } })
const c = inject('console'), action = ref(''), busy = ref(false), error = ref(''), refundAmount = ref(''), reason = ref('订单退款'), refundNo = ref('')
const retrying = ref(false)
const canRefund = computed(() => isAdmin.value && !props.order.deleted_at && ['PAID', 'PARTIALLY_REFUNDED'].includes(props.order.status) && Number(props.order.refundable_amount) > 0)
function open(name) {
  retrying.value = false
  action.value = name; error.value = ''
  if (name === 'refund') { refundAmount.value = props.order.refundable_amount; refundNo.value = `RF_${crypto.randomUUID().replaceAll('-', '')}` }
}
async function retryRefund() {
  busy.value = true
  try {
    const data = await api(`/api/v1/payments/${props.order.out_trade_no}/refund-status`, { method: 'POST' })
    if (!data.refund || !['PENDING','UNKNOWN'].includes(data.refund.status)) { await c.refresh(true); return }
    refundNo.value = data.refund.refund_no; refundAmount.value = data.refund.amount; reason.value = data.refund.reason
    retrying.value = true; action.value = 'refund'; error.value = ''
  } catch (e) { c.toast(errorText(e)) }
  finally { busy.value = false }
}
async function execute() {
  busy.value = true; error.value = ''
  const number = props.order.out_trade_no
  try {
    let result
    if (action.value === 'refund') {
      result = await api(`/api/v1/payments/${number}/refund`, { method: 'POST', body: JSON.stringify({ refund_no: refundNo.value, amount: refundAmount.value, reason: reason.value }) })
      if (result.refund.status === 'FAILED') throw new Error('refund_provider_error')
      c.toast(result.refund.status === 'SUCCESS' ? t('退款已确认', 'Refund confirmed') : t('退款结果待确认，后台会继续查询', 'Refund pending confirmation; verification continues'))
    } else {
      await api(`/api/v1/payments/${number}${action.value === 'restore' ? '/restore' : ''}`, { method: action.value === 'restore' ? 'POST' : 'DELETE' })
      c.toast(action.value === 'restore' ? t('订单已恢复', 'Order restored') : t('订单已移入已删除', 'Order moved to deleted'))
    }
    action.value = ''
    if (c.selectedOrder.value?.out_trade_no === number) c.selectedOrder.value = result?.order || null
    await c.refresh(true)
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
async function queryRefund() {
  busy.value = true
  try { const data = await api(`/api/v1/payments/${props.order.out_trade_no}/refund-status`, { method: 'POST' }); if (c.selectedOrder.value?.out_trade_no === props.order.out_trade_no) c.selectedOrder.value = data.order; await c.refresh(true); c.toast(data.order.status === 'REFUNDING' ? t('退款仍在确认中', 'Refund confirmation pending') : t('退款状态已更新', 'Refund status updated')) }
  catch (e) { c.toast(errorText(e)) }
  finally { busy.value = false }
}
</script>
<template>
  <div class="order-row-actions">
    <button v-if="details" class="text-link" @click="c.selectedOrder.value = order">{{ t('详情', 'Details') }}</button>
    <button v-if="canRefund" class="text-link" :disabled="busy" @click="open('refund')">{{ t('退款', 'Refund') }}</button>
    <button v-if="isAdmin && order.status === 'REFUNDING'" class="text-link" :disabled="busy" @click="queryRefund">{{ t('查询退款', 'Check refund') }}</button>
    <button v-if="isAdmin && order.status === 'REFUNDING'" class="text-link" :disabled="busy" @click="retryRefund">{{ t('重试原退款', 'Retry original refund') }}</button>
    <button v-if="order.deleted_at" class="text-link" :disabled="busy" @click="open('restore')">{{ t('恢复', 'Restore') }}</button>
    <button v-else class="text-link danger-link" :disabled="busy || order.status === 'REFUNDING'" @click="open('delete')">{{ t('删除', 'Delete') }}</button>
  </div>
  <ConfirmDialog :open="!!action" :busy="busy" :title="action === 'refund' ? t('确认单笔退款', 'Confirm refund') : action === 'restore' ? t('恢复订单', 'Restore order') : t('删除订单', 'Delete order')" :description="action === 'refund' ? t('款项将按原支付路径退回，请核对订单与金额', 'Funds return to the original payment method; verify the order and amount') : action === 'restore' ? t('恢复后重新显示在订单列表', 'Restore this order to the order list') : t('移入已删除，可恢复，账务与回调记录继续保留', 'Move to deleted; the order can be restored and accounting records remain')" :label="action === 'refund' ? t('确认退款', 'Confirm refund') : action === 'restore' ? t('确认恢复', 'Restore') : t('确认删除', 'Delete')" @cancel="action = ''" @confirm="execute">
    <div class="refund-summary"><b>{{ order.subject }}</b><code>{{ order.out_trade_no }}</code><span>{{ t('支付金额', 'Paid amount') }} ¥ {{ order.amount }} · {{ t('已退款', 'Refunded') }} ¥ {{ order.refunded_amount || '0.00' }}</span></div>
    <template v-if="action === 'refund'"><label :for="`refund-amount-${order.out_trade_no}`">{{ t('退款金额（CNY）', 'Refund amount (CNY)') }}</label><input :id="`refund-amount-${order.out_trade_no}`" v-model="refundAmount" inputmode="decimal" :disabled="busy || retrying" /><label :for="`refund-reason-${order.out_trade_no}`">{{ t('退款原因', 'Refund reason') }}</label><input :id="`refund-reason-${order.out_trade_no}`" v-model="reason" maxlength="128" :disabled="busy || retrying" /></template>
    <p v-if="error" class="error-box" role="alert">{{ error }}</p>
  </ConfirmDialog>
</template>
