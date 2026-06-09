<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import type { TraceEntry, TraceKind } from './useAgentRun'

/**
 * Observability console (spec layer 5). Surfaces the full event trace — every
 * step, tool call (inputs/outputs), and UI generation — with a trace ID, wall
 * offsets, and span durations. Answers the #1 agent-interface failure mode:
 * a capable agent behind a black box that users won't grant autonomy to.
 */
const props = defineProps<{
  traceId: string | null
  entries: TraceEntry[]
  running: boolean
}>()

const open = ref(false)
const filter = ref<'all' | 'steps' | 'tools' | 'ui'>('all')
const expanded = ref<Record<number, boolean>>({})
const body = ref<HTMLElement | null>(null)

const GROUPS: Record<typeof filter.value, TraceKind[] | null> = {
  all: null,
  steps: ['run', 'plan', 'step', 'approval'],
  tools: ['tool', 'delegation'],
  ui: ['ui'],
}

const shown = computed(() => {
  const kinds = GROUPS[filter.value]
  return kinds ? props.entries.filter((e) => kinds.includes(e.kind)) : props.entries
})

const totalMs = computed(() => props.entries.at(-1)?.elapsedMs ?? 0)
const toolCount = computed(() => props.entries.filter((e) => e.kind === 'tool' && e.durationMs != null).length)

function fmtMs(ms?: number): string {
  if (ms == null) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}
function toggleRow(seq: number, hasDetail: boolean) {
  if (hasDetail) expanded.value = { ...expanded.value, [seq]: !expanded.value[seq] }
}

// Auto-scroll to newest while open and running.
watch(
  () => props.entries.length,
  async () => {
    if (!open.value) return
    await nextTick()
    if (body.value) body.value.scrollTop = body.value.scrollHeight
  },
)
</script>

<template>
  <section class="trace" :class="{ open }">
    <button class="bar" @click="open = !open">
      <span class="dot" :class="{ live: running }" />
      <span class="lbl">TRACE</span>
      <code class="tid">{{ traceId ?? '—' }}</code>
      <span class="meta">{{ entries.length }} events · {{ toolCount }} tool calls · {{ fmtMs(totalMs) }}</span>
      <span class="chev">{{ open ? '▾' : '▸' }}</span>
    </button>

    <div v-show="open" class="panel">
      <div class="filters">
        <button v-for="f in (['all', 'steps', 'tools', 'ui'] as const)" :key="f" :class="{ active: filter === f }" @click="filter = f">
          {{ f }}
        </button>
      </div>

      <div ref="body" class="log">
        <div v-if="!shown.length" class="empty">No trace yet — start a run.</div>
        <div
          v-for="e in shown"
          :key="e.seq"
          class="row"
          :class="[e.kind, { hasDetail: e.detail != null, expanded: expanded[e.seq] }]"
          @click="toggleRow(e.seq, e.detail != null)"
        >
          <span class="off">+{{ fmtMs(e.elapsedMs) }}</span>
          <span class="kind">{{ e.kind }}</span>
          <span class="actor">{{ e.actor }}</span>
          <span class="label">{{ e.label }}</span>
          <span v-if="e.durationMs != null" class="dur">{{ fmtMs(e.durationMs) }}</span>
          <span v-if="e.detail != null" class="caret">{{ expanded[e.seq] ? '▾' : '▸' }}</span>
          <pre v-if="expanded[e.seq]" class="detail">{{ JSON.stringify(e.detail, null, 2) }}</pre>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.trace {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 50;
  background: var(--surface);
  border-top: 1px solid var(--border);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.bar {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  width: 100%;
  padding: 0.5rem 1rem;
  background: transparent;
  border: none;
  color: var(--text-2);
  font: inherit;
  font-size: 0.74rem;
  cursor: pointer;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted);
}
.dot.live {
  background: var(--accent);
  animation: pulse 1.1s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.lbl {
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--text);
}
.tid {
  color: var(--accent);
}
.meta {
  color: var(--muted);
}
.chev {
  margin-left: auto;
  color: var(--muted);
}

.panel {
  border-top: 1px solid var(--border);
}
.filters {
  display: flex;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
}
.filters button {
  font: inherit;
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 0.15rem 0.5rem;
  cursor: pointer;
}
.filters button.active {
  color: var(--bg);
  background: var(--accent);
  border-color: transparent;
}

.log {
  max-height: 38vh;
  overflow-y: auto;
  padding: 0.25rem 0;
}
.empty {
  padding: 1rem;
  color: var(--muted);
  font-size: 0.78rem;
}
.row {
  display: grid;
  grid-template-columns: 64px 78px 96px 1fr auto auto;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0.2rem 1rem;
  font-size: 0.74rem;
  border-left: 2px solid transparent;
}
.row.hasDetail {
  cursor: pointer;
}
.row.hasDetail:hover {
  background: var(--surface-2);
}
.off {
  color: var(--muted);
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.kind {
  text-transform: uppercase;
  font-size: 0.62rem;
  letter-spacing: 0.04em;
  color: var(--muted);
}
.row.tool .kind { color: var(--pos); }
.row.delegation .kind { color: #a78bfa; }
.row.ui .kind { color: var(--accent); }
.row.approval .kind { color: var(--warn); }
.row.error .kind { color: var(--neg); }
.row.run .kind,
.row.plan .kind { color: #60a5fa; }
.actor {
  color: var(--muted);
}
.label {
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.row.expanded .label {
  white-space: normal;
}
.dur {
  color: var(--pos);
  font-variant-numeric: tabular-nums;
}
.caret {
  color: var(--muted);
}
.detail {
  grid-column: 2 / -1;
  margin: 0.35rem 0 0.2rem;
  padding: 0.5rem 0.65rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--muted);
  font-size: 0.7rem;
  white-space: pre-wrap;
  overflow-x: auto;
}
</style>
