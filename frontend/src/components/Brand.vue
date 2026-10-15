<script setup>
defineProps({ name: String })
</script>
<template>
  <img v-if="name === 'bingyun' || name === 'claude'" :src="`/brand/${name}.png`" class="brand-image" alt="" aria-hidden="true" />
  <svg v-else-if="name === 'openai'" class="brand-image openai-mark" aria-hidden="true" viewBox="0 0 24 24"><use href="/brand/openai.svg#mark" /></svg>
  <svg v-else class="brand-image" viewBox="0 0 24 24" aria-hidden="true"><use :href="`/brand/payments.svg#${name}`" /></svg>
</template>
