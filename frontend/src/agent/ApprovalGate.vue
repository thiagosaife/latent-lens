<script setup lang="ts">
import type { ApprovalGate } from './useAgentRun'

/**
 * Approval gate (spec step 5): a heavy operation pauses and surfaces an
 * estimated cost/time before it proceeds. The run is genuinely held server-side
 * until the human decides — approval is a first-class interrupt, not cosmetic.
 */
defineProps<{ gate: ApprovalGate }>()
const emit = defineEmits<{ decide: ['approve' | 'skip' | 'cancel'] }>()

function fmtRows(n?: number) {
  if (n == null) return '—'
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M` : n.toLocaleString()
}
</script>

<template>
  <div class="gate">
    <div class="gate-head">
      <span class="lock" aria-hidden="true">⏸</span>
      <strong>Approval required</strong>
      <code class="step">{{ gate.stepId }}</code>
    </div>
    <p class="msg">{{ gate.message }}</p>

    <dl v-if="gate.estimate" class="est">
      <div>
        <dt>Rows</dt>
        <dd>{{ fmtRows(gate.estimate.rows) }}</dd>
      </div>
      <div>
        <dt>Est. time</dt>
        <dd>{{ gate.estimate.seconds != null ? `~${gate.estimate.seconds}s` : '—' }}</dd>
      </div>
      <div>
        <dt>Est. cost</dt>
        <dd>{{ gate.estimate.cost ?? '$0.00 (local)' }}</dd>
      </div>
    </dl>

    <div class="actions">
      <button class="cancel" @click="emit('decide', 'cancel')">Cancel run</button>
      <button class="skip" @click="emit('decide', 'skip')">Skip step</button>
      <button class="approve" @click="emit('decide', 'approve')">Approve &amp; continue</button>
    </div>
  </div>
</template>

<style scoped>
.gate {
  background: color-mix(in srgb, var(--warn) 9%, var(--surface));
  border: 1px solid color-mix(in srgb, var(--warn) 45%, var(--border));
  border-radius: 10px;
  padding: 1rem 1.15rem;
  max-width: 560px;
}
.gate-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.lock {
  color: var(--warn);
}
.step {
  font-size: 0.72rem;
  color: var(--muted);
  background: var(--bg);
  border: 1px solid var(--border);
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
}
.msg {
  margin: 0.55rem 0 0.8rem;
  font-size: 0.9rem;
  color: var(--text-2);
}
.est {
  display: flex;
  gap: 1.5rem;
  margin: 0 0 1rem;
  padding: 0.7rem 0.9rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.est div {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.est dt {
  font-size: 0.64rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}
.est dd {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.55rem;
}
.actions button {
  padding: 0.5rem 1rem;
  font-size: 0.83rem;
  font-weight: 600;
  border-radius: 8px;
  cursor: pointer;
}
.cancel {
  color: var(--neg);
  background: transparent;
  border: 1px solid color-mix(in srgb, var(--neg) 40%, var(--border));
  margin-right: auto;
}
.skip {
  color: var(--text-2);
  background: transparent;
  border: 1px solid var(--border);
}
.approve {
  color: var(--bg);
  background: var(--warn);
  border: none;
}
</style>
