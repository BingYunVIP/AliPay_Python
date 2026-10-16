<script setup>
import { computed } from 'vue'
const props = defineProps({ outgoing: Boolean })
const paths = computed(() => [36, 144, 252, 360].map(y => props.outgoing ? `M42 198C74 198 60 ${y} 100 ${y}` : `M0 ${y}C40 ${y} 26 198 58 198`))
const mobilePaths = computed(() => [125, 375, 625, 875].map(x => props.outgoing ? `M500 20C500 40 ${x} 25 ${x} 60` : `M${x} 0C${x} 40 500 20 500 40`))
</script>
<template>
  <div class="byh-wire" :class="outgoing ? 'byh-wire-out' : 'byh-wire-in'" aria-hidden="true">
    <svg class="byh-wire-desktop" viewBox="0 0 100 396" preserveAspectRatio="none">
      <path v-for="path in paths" :key="path" :d="path" class="byh-branch" />
      <path class="byh-trunk" :d="outgoing ? 'M0 198H42' : 'M58 198H100'" />
      <path class="byh-pulse" d="M0 0h.01" opacity="0" vector-effect="non-scaling-stroke" />
    </svg>
    <svg class="byh-wire-mobile" viewBox="0 0 1000 60" preserveAspectRatio="none">
      <path v-for="path in mobilePaths" :key="path" :d="path" class="byh-branch" />
      <path class="byh-trunk" :d="outgoing ? 'M500 0V20' : 'M500 40V60'" />
      <path class="byh-pulse" d="M0 0h.01" opacity="0" vector-effect="non-scaling-stroke" />
    </svg>
  </div>
</template>
