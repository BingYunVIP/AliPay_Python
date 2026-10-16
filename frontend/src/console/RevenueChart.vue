<script setup>
import { computed, ref, useId } from 'vue'
import { t, locale } from '../state'
import { amount } from './format'
const props = defineProps({ series: { type: Array, default: () => [] }, period: String })
const active = ref(null), fillId = useId()
const width = 780, height = 220, pad = 12
const peak = computed(() => Math.max(0, ...props.series.map(p => Number(p.amount_cents) || 0)))
const gridStep = computed(() => {
  if (!peak.value) return 25
  const raw = peak.value / 4, power = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].find(n => n * power >= raw) * power
  return Math.max(1, Math.ceil(step))
})
const gridTop = computed(() => gridStep.value * 4)
const points = computed(() => props.series.map((p, i) => ({ ...p, x: pad + i * (width - pad * 2) / Math.max(1, props.series.length - 1), y: height - 16 - p.amount_cents / gridTop.value * (height - 34) })))
const line = computed(() => points.value.map((p, i, a) => i === 0 ? `M ${p.x} ${p.y}` : `C ${(a[i-1].x+p.x)/2} ${a[i-1].y}, ${(a[i-1].x+p.x)/2} ${p.y}, ${p.x} ${p.y}`).join(' '))
const area = computed(() => points.value.length ? `${line.value} L ${points.value.at(-1).x} ${height - 16} L ${pad} ${height - 16} Z` : '')
const label = timestamp => new Date(timestamp * 1000).toLocaleString(locale.value === 'zh' ? 'zh-CN' : 'en-GB', props.period === 'today' ? { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false } : { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit' })
const visibleLabels = computed(() => points.value.filter((_, i) => props.series.length <= 7 || i % 5 === 0 || i === props.series.length - 1))
</script>
<template>
  <div class="revenue-chart">
    <div class="chart-y-axis"><span v-for="fraction in [1, .75, .5, .25, 0]" :key="fraction">{{ amount(gridTop * fraction) }}</span></div>
    <div class="chart-plot" @pointerleave="active = null">
      <svg :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none" role="img" :aria-label="t('收款金额趋势，统计时区为北京时间', 'Payment revenue trend, China Standard Time')">
        <defs><linearGradient :id="fillId" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#4f94ff" stop-opacity=".24" /><stop offset="100%" stop-color="#4f94ff" stop-opacity="0" /></linearGradient></defs>
        <line v-for="fraction in [0, .25, .5, .75, 1]" :key="fraction" :x1="pad" :x2="width-pad" :y1="18 + fraction * (height-34)" :y2="18 + fraction * (height-34)" class="chart-grid" />
        <path :d="area" :fill="`url(#${fillId})`" /><path :d="line" class="chart-line" />
        <template v-if="active !== null && points[active]"><line :x1="points[active].x" :x2="points[active].x" y1="15" :y2="height-16" class="chart-crosshair" /><circle :cx="points[active].x" :cy="points[active].y" r="4" class="chart-dot" /></template>
      </svg>
      <button v-for="(p, i) in points" :key="p.timestamp" class="chart-target" :style="{ left: `${p.x / width * 100}%`, width: `${100/points.length}%` }" @pointerenter="active = i" @focus="active = i" @blur="active = null" :aria-label="`${label(p.timestamp)}: ¥${amount(p.amount_cents)}, ${p.count} ${t('笔', 'orders')}`"></button>
      <div v-if="active !== null && points[active]" class="chart-tooltip" :style="{ left: `${Math.min(78, Math.max(12, points[active].x/width*100))}%` }"><small>{{ label(points[active].timestamp) }}</small><b>¥ {{ amount(points[active].amount_cents) }}</b><span>{{ points[active].count }} {{ t('笔成功交易', 'paid orders') }}</span></div>
      <div class="chart-x-axis"><span v-for="p in visibleLabels" :key="p.timestamp" :style="{ left: `${p.x / width * 100}%` }">{{ label(p.timestamp) }}</span></div>
    </div>
  </div>
</template>
