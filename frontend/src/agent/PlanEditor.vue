<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PlanStep } from './events'

/**
 * Editable plan surface (spec step 2): the agent proposes a plan; the human
 * reorders, deletes, or edits steps BEFORE anything runs. Reorder is native
 * HTML5 drag-and-drop (no dependency). Behavior of each step is owned by the
 * backend catalog — editing here only changes order, presence, and label.
 */
const props = defineProps<{ steps: PlanStep[] }>()
const emit = defineEmits<{ approve: [PlanStep[]]; cancel: [] }>()

const local = ref<PlanStep[]>([])
const dragIndex = ref<number | null>(null)

watch(
  () => props.steps,
  (steps) => {
    local.value = steps.map((s) => ({ ...s }))
  },
  { immediate: true, deep: true },
)

function onDragStart(i: number) {
  dragIndex.value = i
}
function onDragEnter(i: number) {
  const from = dragIndex.value
  if (from === null || from === i) return
  const arr = local.value
  const [moved] = arr.splice(from, 1)
  arr.splice(i, 0, moved)
  dragIndex.value = i
}
function onDragEnd() {
  dragIndex.value = null
}
function remove(i: number) {
  local.value.splice(i, 1)
}
function fmtRows(n?: number) {
  if (n == null) return null
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M` : n.toLocaleString()
}
</script>

<template>
  <section class="plan">
    <header class="plan-head">
      <h3>Proposed plan</h3>
      <span class="sub">Drag to reorder · edit titles · delete steps, then approve</span>
    </header>

    <ul class="plan-list">
      <li
        v-for="(step, i) in local"
        :key="step.id"
        class="plan-item"
        :class="{ dragging: dragIndex === i, gate: step.needsApproval }"
        draggable="true"
        @dragstart="onDragStart(i)"
        @dragenter="onDragEnter(i)"
        @dragover.prevent
        @dragend="onDragEnd"
      >
        <span class="grip" aria-hidden="true">⠿</span>
        <span class="idx">{{ i + 1 }}</span>
        <div class="body">
          <input v-model="step.title" class="title" spellcheck="false" />
          <span v-if="step.description" class="desc">{{ step.description }}</span>
        </div>
        <span v-if="step.needsApproval" class="gate-tag" :title="`~${step.estimate?.seconds}s`">
          approval gate
          <template v-if="step.estimate?.rows"> · {{ fmtRows(step.estimate.rows) }} rows</template>
        </span>
        <button class="del" title="Delete step" @click="remove(i)">×</button>
      </li>
    </ul>

    <footer class="plan-foot">
      <button class="ghost" @click="emit('cancel')">Cancel</button>
      <button class="primary" :disabled="!local.length" @click="emit('approve', local)">
        Approve &amp; run ({{ local.length }})
      </button>
    </footer>
  </section>
</template>

<style scoped>
.plan {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.1rem 1.2rem;
  max-width: 720px;
}
.plan-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.9rem;
}
.plan-head h3 {
  margin: 0;
  font-size: 1rem;
}
.sub {
  font-size: 0.74rem;
  color: var(--muted);
}
.plan-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.plan-item {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.6rem 0.7rem;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 9px;
  cursor: grab;
}
.plan-item.gate {
  border-color: color-mix(in srgb, var(--warn) 45%, var(--border));
}
.plan-item.dragging {
  opacity: 0.5;
  border-style: dashed;
}
.grip {
  color: var(--muted);
  cursor: grab;
  user-select: none;
}
.idx {
  width: 1.4rem;
  height: 1.4rem;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  font-size: 0.72rem;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 50%;
}
.body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.title {
  width: 100%;
  font: inherit;
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--text);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 5px;
  padding: 0.15rem 0.3rem;
  margin-left: -0.3rem;
}
.title:hover {
  border-color: var(--border);
}
.title:focus {
  outline: none;
  border-color: var(--accent);
  background: var(--bg);
}
.desc {
  font-size: 0.74rem;
  color: var(--muted);
}
.gate-tag {
  font-size: 0.64rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--warn);
  background: color-mix(in srgb, var(--warn) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--warn) 35%, transparent);
  padding: 0.15rem 0.4rem;
  border-radius: 5px;
  white-space: nowrap;
}
.del {
  flex-shrink: 0;
  width: 1.5rem;
  height: 1.5rem;
  font-size: 1rem;
  line-height: 1;
  color: var(--muted);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
}
.del:hover {
  color: var(--neg);
  border-color: color-mix(in srgb, var(--neg) 40%, var(--border));
}
.plan-foot {
  display: flex;
  justify-content: flex-end;
  gap: 0.6rem;
  margin-top: 1rem;
}
.plan-foot button {
  padding: 0.5rem 1.1rem;
  font-size: 0.85rem;
  font-weight: 600;
  border-radius: 8px;
  cursor: pointer;
}
.ghost {
  color: var(--text-2);
  background: transparent;
  border: 1px solid var(--border);
}
.primary {
  color: var(--bg);
  background: var(--accent);
  border: none;
}
.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
