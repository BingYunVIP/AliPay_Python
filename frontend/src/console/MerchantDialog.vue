<script setup>
import { ref } from 'vue'
import { t } from '../state'
import Icon from '../components/Icon.vue'
import KeysPanel from './KeysPanel.vue'

// Merchant API credentials open in place: the user table passes a merchant, the account page opens your own.
const dialog = ref(null), panel = ref(null), opened = ref(false), merchantId = ref(null)
function hasChanges(){ return Boolean(panel.value?.hasPaymentChanges?.()) }
function confirmDiscard(){ return !hasChanges() || window.confirm(t('交易参数尚未保存，确定关闭吗？','Transaction settings have unsaved changes. Close anyway?')) }
function close(){ if(confirmDiscard()) dialog.value?.close() }
function open(id = null) {
  if (opened.value && merchantId.value === id) return true
  if (opened.value && merchantId.value !== id) {
    if (!confirmDiscard()) return false
    merchantId.value = id
    return true
  }
  merchantId.value = id
  opened.value = true
  dialog.value.showModal()
  return true
}
defineExpose({ open, close })
</script>
<template>
  <dialog ref="dialog" class="console-dialog merchant-config-dialog" :aria-label="t('商户接口配置', 'Merchant API settings')" @close="opened = false" @cancel.prevent="close">
    <div class="dialog-heading"><h2>{{ t('商户接口', 'Merchant API') }}<template v-if="merchantId"> · {{ merchantId }}</template></h2><button class="icon-button" :aria-label="t('关闭接口配置', 'Close merchant API settings')" @click="close"><Icon name="x" /></button></div>
    <KeysPanel v-if="opened" ref="panel" :key="merchantId ?? 'own'" :merchant-id="merchantId" />
  </dialog>
</template>
