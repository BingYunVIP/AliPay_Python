<script setup>
import { ref } from 'vue'
import { t } from '../state'
import Icon from '../components/Icon.vue'
import KeysPanel from './KeysPanel.vue'

// Merchant API credentials open in place: the user table passes a merchant, the account page opens your own.
const dialog = ref(null), opened = ref(false), merchantId = ref(null)
defineExpose({ open(id = null) { merchantId.value = id; opened.value = true; dialog.value.showModal() } })
</script>
<template>
  <dialog ref="dialog" class="console-dialog merchant-config-dialog" :aria-label="t('商户接口配置', 'Merchant API settings')" @close="opened = false">
    <div class="dialog-heading"><h2>{{ t('商户接口', 'Merchant API') }}<template v-if="merchantId"> · {{ merchantId }}</template></h2><button class="icon-button" :aria-label="t('关闭接口配置', 'Close merchant API settings')" @click="dialog.close()"><Icon name="x" /></button></div>
    <KeysPanel v-if="opened" :key="merchantId ?? 'own'" :merchant-id="merchantId" />
  </dialog>
</template>
