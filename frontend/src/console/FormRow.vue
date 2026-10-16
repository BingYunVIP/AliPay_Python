<script setup>
// One setting: its name and hint on the left, its control on the right (or underneath when `stacked`)
defineProps({ field: String, label: String, hint: String, error: String, stacked: Boolean })
</script>
<template>
  <div class="form-row" :class="{ stacked, invalid: !!error }">
    <div class="form-label"><label :for="field">{{ label }}</label><p v-if="hint" :id="`${field}-hint`" class="form-hint">{{ hint }}</p></div>
    <div class="form-control"><slot /><p v-if="error" :id="`${field}-error`" class="form-error" role="alert">{{ error }}</p></div>
  </div>
</template>
