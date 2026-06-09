<script setup lang="ts">
import type { Delegation } from './useAgentRun'

/**
 * Renders a delegation: the orchestrator handed this step to a specialist
 * sub-agent, which ran attributed tool calls (with inputs/outputs) and returned
 * control. Makes the multi-agent orchestration legible instead of a black box.
 */
defineProps<{ delegation: Delegation }>()

const AGENT_COLORS: Record<string, string> = {
  orchestrator: '#5eead4',
  'cleaning-agent': '#a78bfa',
  'segmentation-agent': '#fbbf24',
}
const colorFor = (a: string) => AGENT_COLORS[a] ?? '#60a5fa'

function fmtArgs(args?: Record<string, unknown>): string {
  if (!args) return ''
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? `"${v}"` : String(v)}`)
    .join(', ')
}
</script>

<template>
  <div class="delegation" :style="{ '--agent': colorFor(delegation.agent) }">
    <div class="agent-head">
      <span class="badge">{{ delegation.agent }}</span>
      <span class="role">delegated by orchestrator</span>
    </div>

    <ul class="calls">
      <li v-for="c in delegation.toolCalls" :key="c.callId" :class="c.status">
        <span class="bullet" />
        <code class="call"><b>{{ c.tool }}</b>(<span class="args">{{ fmtArgs(c.args) }}</span>)</code>
        <span v-if="c.result" class="result">→ {{ c.result }}</span>
        <span v-else class="pending">running…</span>
      </li>
    </ul>

    <div v-if="delegation.returned" class="returned">↩ returned to orchestrator</div>
  </div>
</template>

<style scoped>
.delegation {
  margin: 0 0 0.2rem;
  padding: 0.6rem 0.75rem;
  border-left: 2px solid var(--agent);
  background: color-mix(in srgb, var(--agent) 7%, var(--surface));
  border-radius: 0 8px 8px 0;
  max-width: 560px;
}
.agent-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.badge {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--bg);
  background: var(--agent);
  padding: 0.12rem 0.45rem;
  border-radius: 5px;
}
.role {
  font-size: 0.7rem;
  color: var(--muted);
}
.calls {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.calls li {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.78rem;
}
.bullet {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--agent);
}
.calls li.running .bullet {
  animation: pulse 1.1s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.call {
  font-family: ui-monospace, monospace;
  color: var(--text-2);
}
.call b {
  color: var(--text);
  font-weight: 600;
}
.args {
  color: var(--muted);
}
.result {
  color: var(--pos);
  font-variant-numeric: tabular-nums;
}
.pending {
  color: var(--muted);
  font-style: italic;
}
.returned {
  margin-top: 0.5rem;
  font-size: 0.72rem;
  color: var(--muted);
}
</style>
