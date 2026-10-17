<script setup>
import { ref } from 'vue'
import Icon from '../components/Icon.vue'
// 标签式输入：输入内容后回车变成一个标签，标签宽度随内容变化，可点叉删除；域名标签可点击打开
const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  id: String, placeholder: String, linkable: Boolean, disabled: Boolean,
  normalize: { type: String, default: 'domain' }, maxlength: { type: Number, default: 253 },
})
const emit = defineEmits(['update:modelValue'])
const draft = ref('')
function clean(value) {
  let text = String(value || '').trim()
  if (props.normalize !== 'domain') return text
  text = text.toLowerCase().replace(/^[a-z][a-z0-9+.-]*:\/\//, '')
  text = text.split('/')[0].split('?')[0].split('#')[0].replace(/:\d+$/, '').replace(/\.$/, '')
  return text
}
function add() {
  const value = clean(draft.value)
  draft.value = ''
  if (!value) return
  const seen = props.modelValue.map(item => String(item).toLowerCase())
  if (seen.includes(value.toLowerCase())) return
  emit('update:modelValue', [...props.modelValue, value])
}
function remove(index) { emit('update:modelValue', props.modelValue.filter((_, position) => position !== index)) }
function backspace() { if (!draft.value && props.modelValue.length) remove(props.modelValue.length - 1) }
</script>
<template>
  <div class="tag-input" :class="{ disabled }">
    <span v-for="(value, index) in modelValue" :key="`${value}-${index}`" class="tag-chip">
      <a v-if="linkable" :href="`https://${value}`" target="_blank" rel="noopener noreferrer">{{ value }}</a>
      <span v-else>{{ value }}</span>
      <button type="button" class="tag-remove" :disabled="disabled" :aria-label="`删除 ${value}`" @click="remove(index)"><Icon name="x" :size="11" /></button>
    </span>
    <input :id="id" v-model="draft" class="tag-field" :placeholder="placeholder" :maxlength="maxlength" :disabled="disabled" autocomplete="off" spellcheck="false"
      @keydown.enter.prevent="add" @keydown.comma.prevent="add" @keydown.backspace="backspace" @blur="add" />
  </div>
</template>
