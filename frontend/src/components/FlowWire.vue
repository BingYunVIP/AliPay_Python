<script setup>
import { computed } from 'vue'
const props = defineProps({ outgoing: Boolean, paused: Boolean })
const paths = computed(() => [44, 149, 254, 359].map(y => props.outgoing ? `M0 202H34C70 202 46 ${y} 100 ${y}` : `M0 ${y}C54 ${y} 30 202 66 202H100`))
const mobilePaths = computed(() => [125, 375, 625, 875].map(x => props.outgoing ? `M500 0V16C500 40 ${x} 16 ${x} 60` : `M${x} 0C${x} 40 500 20 500 44V60`))
</script>
<template>
  <div class="byh-wire" :class="{ 'is-paused': paused }" aria-hidden="true">
    <svg class="wire-desktop" viewBox="0 0 100 404" preserveAspectRatio="none">
      <path v-for="path in paths" :key="path" :d="path" class="wire-path" />
      <path v-for="(path, i) in paths" :key="`pulse-${i}`" :d="path" pathLength="100" class="wire-pulse" :style="{ animationDelay: `${i * -1.45 - (outgoing ? 2.5 : 0)}s` }" />
      <circle :cx="outgoing ? 34 : 66" cy="202" r="3.4" class="wire-junction" />
    </svg>
    <svg class="wire-mobile" viewBox="0 0 1000 60" preserveAspectRatio="none">
      <path v-for="path in mobilePaths" :key="path" :d="path" class="wire-path" />
      <path v-for="(path, i) in mobilePaths" :key="`pulse-${i}`" :d="path" pathLength="100" class="wire-pulse" :style="{ animationDelay: `${i * -1.45}s` }" />
    </svg>
  </div>
</template>
