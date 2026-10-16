<script setup>
import { t } from '../state'
import Icon from '../components/Icon.vue'
defineProps({ page: Number, total: Number, loading: Boolean })
defineEmits(['update:page'])
</script>
<template><div class="console-pagination"><span>{{ t(`共 ${total} 条`, `${total} records`) }}</span><div><button class="icon-button" :disabled="page <= 1 || loading" :aria-label="t('上一页', 'Previous page')" @click="$emit('update:page', page - 1)"><Icon name="back" :size="16" /></button><span>{{ page }} / {{ Math.max(1, Math.ceil(total / 20)) }}</span><button class="icon-button" :disabled="page * 20 >= total || loading" :aria-label="t('下一页', 'Next page')" @click="$emit('update:page', page + 1)"><Icon name="chevron" :size="16" /></button></div></div></template>
