<script setup lang="ts">
import type { RawIntent } from '../types'

/**
 * The guardrail surface. When the agent emits an intent the registry can't
 * vet — unknown component or invalid props — we render THIS instead of
 * silently dropping it or, worse, injecting unaudited UI. Visible failure is
 * the whole point of the constrained generation surface.
 */
defineProps<{
  reason: 'unknown_component' | 'invalid_props'
  message: string
  intent: RawIntent
}>()
</script>

<template>
  <div class="rejected">
    <div class="rejected-head">
      <span class="x">⛔</span>
      <strong>Intent rejected</strong>
      <code class="reason">{{ reason }}</code>
    </div>
    <p class="msg">{{ message }}</p>
    <pre class="raw">{{ JSON.stringify(intent, null, 2) }}</pre>
  </div>
</template>

<style scoped>
.rejected {
  background: color-mix(in srgb, var(--neg) 8%, var(--surface));
  border: 1px solid color-mix(in srgb, var(--neg) 40%, var(--border));
  border-radius: 10px;
  padding: 0.9rem 1.1rem;
  max-width: 560px;
}
.rejected-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.reason {
  font-size: 0.72rem;
  color: var(--neg);
  background: color-mix(in srgb, var(--neg) 16%, transparent);
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
}
.msg {
  margin: 0.5rem 0 0.6rem;
  color: var(--text-2);
  font-size: 0.9rem;
}
.raw {
  margin: 0;
  font-size: 0.72rem;
  color: var(--muted);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.55rem 0.7rem;
  overflow-x: auto;
}
</style>
