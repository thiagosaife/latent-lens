<script setup lang="ts">
import { computed } from 'vue'

/**
 * Diverging-bar readout of what makes a lasso selection distinct: each numeric
 * feature's selection mean expressed as a z-score from the population mean. The
 * agent emits this as a `feature_delta` intent (validated by the registry); the
 * numbers are computed server-side over the real dataset rows, not faked here.
 */
interface FeatureDelta {
  feature: string
  z: number
  selMean?: number
  popMean?: number
  direction?: 'above' | 'below'
}

const props = defineProps<{
  title?: string
  features: FeatureDelta[]
  note?: string
}>()

// Scale every bar to the strongest signal in the set, with a floor so a set of
// small deltas still renders visibly rather than collapsing to nothing.
const maxAbs = computed(() => Math.max(0.5, ...props.features.map((f) => Math.abs(f.z))))

// Fraction (0..50%) of the half-track this z occupies — bars grow out from center.
const halfWidth = (z: number): number => (Math.min(Math.abs(z), maxAbs.value) / maxAbs.value) * 50
</script>

<template>
  <div class="feature-delta">
    <h4>{{ title ?? 'What distinguishes these points' }}</h4>
    <ul>
      <li v-for="f in features" :key="f.feature">
        <span class="name">{{ f.feature }}</span>
        <span class="track">
          <span class="center" />
          <span
            class="bar"
            :class="f.z >= 0 ? 'pos' : 'neg'"
            :style="
              f.z >= 0
                ? { left: '50%', width: halfWidth(f.z) + '%' }
                : { right: '50%', width: halfWidth(f.z) + '%' }
            "
          />
        </span>
        <span class="z" :class="f.z >= 0 ? 'pos' : 'neg'">
          {{ f.z >= 0 ? '+' : '' }}{{ f.z.toFixed(1) }}σ
        </span>
      </li>
    </ul>
    <p v-if="note" class="note">{{ note }}</p>
  </div>
</template>

<style scoped>
.feature-delta {
  padding: 1.1rem 1.25rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 10px;
  max-width: 560px;
}
h4 {
  margin: 0 0 0.85rem;
  font-size: 0.92rem;
}
ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
li {
  display: grid;
  grid-template-columns: 7.5rem 1fr 3.4rem;
  align-items: center;
  gap: 0.6rem;
}
.name {
  font-size: 0.8rem;
  color: var(--text-2);
  font-family: ui-monospace, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.track {
  position: relative;
  height: 12px;
  background: var(--bg);
  border-radius: 4px;
  overflow: hidden;
}
.center {
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--border);
}
.bar {
  position: absolute;
  top: 2px;
  bottom: 2px;
  border-radius: 3px;
  min-width: 2px;
}
.bar.pos {
  background: var(--pos);
}
.bar.neg {
  background: var(--neg);
}
.z {
  font-size: 0.76rem;
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.z.pos {
  color: var(--pos);
}
.z.neg {
  color: var(--neg);
}
.note {
  margin: 0.85rem 0 0;
  font-size: 0.72rem;
  color: var(--muted);
}
</style>
