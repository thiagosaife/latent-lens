<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  label: string
  value: string | number
  delta?: number
  format?: 'number' | 'percent' | 'currency'
}>()

const display = computed(() => {
  if (typeof props.value === 'string') return props.value
  const n = props.value
  switch (props.format) {
    case 'percent':
      return `${(n * 100).toFixed(1)}%`
    case 'currency':
      return n.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
    default:
      return n.toLocaleString()
  }
})

const tone = computed(() => (props.delta == null ? '' : props.delta >= 0 ? 'up' : 'down'))
</script>

<template>
  <div class="stat-tile">
    <span class="label">{{ label }}</span>
    <span class="value">{{ display }}</span>
    <span v-if="delta != null" class="delta" :class="tone">
      {{ delta >= 0 ? '▲' : '▼' }} {{ Math.abs(delta * 100).toFixed(1) }}%
    </span>
  </div>
</template>

<style scoped>
.stat-tile {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding: 1rem 1.25rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  min-width: 150px;
}
.label {
  color: var(--muted);
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.07em;
}
.value {
  font-size: 1.7rem;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.delta {
  font-size: 0.78rem;
  font-variant-numeric: tabular-nums;
}
.delta.up { color: var(--pos); }
.delta.down { color: var(--neg); }
</style>
