<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
const props = defineProps({ modelValue: String, items: Array, label: String })
const emit = defineEmits(['update:modelValue'])
const buttons = ref([])
// On a narrow screen the open tab may sit outside the visible part of the list
const reveal = () => nextTick(() => buttons.value[props.items.findIndex(item => item.id === props.modelValue)]?.scrollIntoView({ block: 'nearest', inline: 'nearest' }))
onMounted(reveal)
watch(() => props.modelValue, reveal)
// Arrow keys, Home and End move between tabs, as in a native tab list
function move(event, index) {
  const last = props.items.length - 1
  const target = { ArrowRight: index === last ? 0 : index + 1, ArrowLeft: index === 0 ? last : index - 1, Home: 0, End: last }[event.key]
  if (target === undefined) return
  event.preventDefault()
  emit('update:modelValue', props.items[target].id)
  buttons.value[target]?.focus()
}
</script>
<template>
  <div class="page-tabs" role="tablist" :aria-label="label">
    <button v-for="(item, index) in items" :id="`tab-${item.id}`" :key="item.id" :ref="el => { buttons[index] = el }" type="button" role="tab" :aria-selected="modelValue === item.id" :aria-controls="`panel-${item.id}`" :tabindex="modelValue === item.id ? 0 : -1" @click="emit('update:modelValue', item.id)" @keydown="move($event, index)">{{ item.label }}</button>
  </div>
</template>
