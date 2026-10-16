<script setup>
defineProps({ modelValue: String, options: Array, label: String })
defineEmits(['update:modelValue'])
</script>
<template>
  <div class="segmented-control" role="group" :aria-label="label">
    <button v-for="option in options" :key="option.value" type="button" :class="{ selected: modelValue === option.value }" :aria-pressed="modelValue === option.value" @click="$emit('update:modelValue', option.value)">{{ option.label }}<small v-if="option.count !== undefined">{{ option.count }}</small></button>
  </div>
</template>
