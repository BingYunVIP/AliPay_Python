<script setup>
import { t } from '../state'
import Icon from '../components/Icon.vue'
// Sits under a form and stays in view while the form is longer than the screen
defineProps({ dirty: Boolean, busy: Boolean, label: String, error: String })
defineEmits(['discard'])
</script>
<template>
  <div class="form-footer" :class="{ idle: !dirty && !busy && !error }">
    <p class="form-footer-state" :class="{ failed: error }" role="status"><template v-if="error"><Icon name="alert" :size="15" />{{ error }}</template><template v-else-if="dirty"><i class="dirty-dot"></i>{{ t('有未保存的修改', 'Unsaved changes') }}</template></p>
    <div class="form-footer-actions">
      <slot />
      <button type="button" class="button button-secondary" :disabled="!dirty || busy" @click="$emit('discard')">{{ t('放弃修改', 'Discard') }}</button>
      <button type="submit" class="button" :disabled="!dirty || busy"><Icon :name="busy ? 'loader' : 'check'" :class="{ spin: busy }" :size="16" />{{ busy ? t('正在保存…', 'Saving…') : label }}</button>
    </div>
  </div>
</template>
