<script setup>
import Icon from '../components/Icon.vue'
defineProps({ icon: { type: String, default: 'receipt' }, title: String, description: String })
</script>
<template><div class="console-empty"><span><Icon :name="icon" :size="26" /></span><h3>{{ title }}</h3><p v-if="description">{{ description }}</p><slot /></div></template>
