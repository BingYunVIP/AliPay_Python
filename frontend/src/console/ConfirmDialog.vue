<script setup>
import { onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue'
import { t } from '../state'
import Icon from '../components/Icon.vue'
const props = defineProps({ open: Boolean, title: String, description: String, busy: Boolean, label: String, destructive: Boolean, disabled: Boolean })
const emit = defineEmits(['cancel', 'confirm'])
const dialog = ref(null)
// May be mounted already open (per-row dialogs render only while in use); closing before removal restores focus
onMounted(() => { if (props.open && dialog.value) dialog.value.showModal() })
watch(() => props.open, async value => {
  await nextTick()
  const node = dialog.value
  if (!node) return
  if (value) { if (!node.open) node.showModal() }
  else if (node.open) node.close()
})
onBeforeUnmount(() => dialog.value?.close())
function submit() { if (!props.busy && !props.disabled) emit('confirm') }
</script>
<template>
  <dialog ref="dialog" class="console-dialog confirm-dialog" :aria-label="title" @cancel.prevent="!busy && emit('cancel')">
    <form @submit.prevent="submit">
      <header class="confirm-head"><span class="confirm-icon"><Icon :name="destructive ? 'alert' : 'help'" :size="22" /></span><h2>{{ title }}</h2></header>
      <p v-if="description" class="confirm-text">{{ description }}</p>
      <div class="confirm-body"><slot /></div>
      <footer class="confirm-actions">
        <button type="button" class="button button-secondary" :disabled="busy" @click="emit('cancel')">{{ t('取消', 'Cancel') }}</button>
        <button type="submit" class="button" :class="{ 'button-danger': destructive }" :disabled="busy || disabled"><Icon v-if="busy" class="spin" name="loader" :size="16" />{{ label || t('确认', 'Confirm') }}</button>
      </footer>
    </form>
  </dialog>
</template>
