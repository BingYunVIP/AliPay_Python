<script setup>
import { onActivated, onDeactivated, onMounted, ref } from 'vue'
import { t } from '../state'
import Icon from '../components/Icon.vue'
// 保存条：默认跟在表单末尾；系统管理页把按钮放到页面标题最右边（#settings-heading-actions）
defineProps({ dirty: Boolean, busy: Boolean, label: String, error: String })
defineEmits(['discard'])
const footer = ref(null), host = ref(null), formId = ref(''), active = ref(true)
onMounted(() => {
  // 按钮移到表单外以后，仍要提交原来那个表单，所以记住它的 id
  const form = footer.value?.closest('form')
  if (form) { if (!form.id) form.id = `settings-form-${Math.random().toString(36).slice(2, 8)}`; formId.value = form.id }
  host.value = document.getElementById('settings-heading-actions')
})
// 系统管理的分区会被 KeepAlive 缓存：切走后必须把自己的按钮从标题里拿走，否则标题里会堆好几个
onActivated(() => { active.value = true })
onDeactivated(() => { active.value = false })
</script>
<template>
  <Teleport v-if="active" :to="host || '#settings-heading-actions'" :disabled="!host">
    <div ref="footer" class="form-footer" :class="{ idle: !dirty && !busy && !error }">
      <p class="form-footer-state" :class="{ failed: error }" role="status"><template v-if="error"><Icon name="alert" :size="15" />{{ error }}</template><template v-else-if="dirty"><i class="dirty-dot"></i>{{ t('有未保存的修改', 'Unsaved changes') }}</template></p>
      <div class="form-footer-actions">
        <slot />
        <button type="button" class="button button-secondary" :disabled="!dirty || busy" @click="$emit('discard')">{{ t('放弃修改', 'Discard') }}</button>
        <button type="submit" class="button" :form="formId || undefined" :disabled="!dirty || busy"><Icon :name="busy ? 'loader' : 'check'" :class="{ spin: busy }" :size="16" />{{ busy ? t('正在保存…', 'Saving…') : label }}</button>
      </div>
    </div>
  </Teleport>
</template>
