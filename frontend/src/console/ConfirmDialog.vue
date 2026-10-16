<script setup>
import { ref, watch, nextTick } from 'vue'
import { t } from '../state'
import Icon from '../components/Icon.vue'
const props = defineProps({ open: Boolean, title: String, description: String, busy: Boolean, label: String, destructive: Boolean })
const emit = defineEmits(['cancel', 'confirm'])
const dialog = ref(null)
watch(() => props.open, async value => { await nextTick(); if (value) dialog.value.showModal(); else dialog.value.close() })
</script>
<template><dialog ref="dialog" class="console-dialog confirm-dialog" :aria-label="title" @cancel.prevent="!busy && emit('cancel')"><span class="confirm-icon"><Icon :name="destructive ? 'alert' : 'help'" :size="25" /></span><h2>{{ title }}</h2><p>{{ description }}</p><slot /><div class="dialog-actions"><button class="button button-secondary" :disabled="busy" @click="emit('cancel')">{{ t('取消', 'Cancel') }}</button><button class="button" :class="{ 'button-danger': destructive }" :disabled="busy" @click="emit('confirm')"><Icon v-if="busy" class="spin" name="loader" :size="16" />{{ label || t('确认', 'Confirm') }}</button></div></dialog></template>
