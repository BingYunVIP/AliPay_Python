<script setup>
defineProps({ title: String, note: String })
</script>
<template>
  <section class="form-group">
    <header v-if="title || $slots.action" class="form-group-title"><h3>{{ title }}</h3><slot name="action" /></header>
    <div class="hig-card form-card"><slot /></div>
    <p v-if="note" class="form-group-note">{{ note }}</p>
  </section>
</template>
