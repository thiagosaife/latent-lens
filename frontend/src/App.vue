<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import IntentRenderer from './patterns/IntentRenderer.vue'
import PlanEditor from './agent/PlanEditor.vue'
import ApprovalGate from './agent/ApprovalGate.vue'
import DelegationTrace from './agent/DelegationTrace.vue'
import TraceInspector from './agent/TraceInspector.vue'
import DatasetUpload from './agent/DatasetUpload.vue'
import { listPatterns } from './patterns/registry'
import { useAgentRun } from './agent/useAgentRun'
import { selection } from './agent/selection'
import type { PlanStep } from './agent/events'
import type { DatasetMeta } from './agent/datasets'

const DEFAULT_GOAL = 'Find the structure in this customer dataset and tell me what the segments are.'

const run = useAgentRun()
const { phase, traceId, trace, plan, gate, steps, errorMessage } = run

const goalInput = ref(DEFAULT_GOAL)
const dataset = ref<DatasetMeta | null>(null)
const patterns = listPatterns()

const isActive = computed(() =>
  (['planning', 'awaiting_plan', 'executing', 'awaiting_approval'] as const).includes(
    phase.value as 'planning' | 'awaiting_plan' | 'executing' | 'awaiting_approval',
  ),
)

function submit() {
  if (isActive.value) run.cancel()
  else run.start(goalInput.value, dataset.value?.datasetId)
}

function onApprovePlan(steps: PlanStep[]) {
  run.approvePlan(steps)
}

// Connecting a dataset immediately (re)starts a run analyzing it.
function onUploaded(meta: DatasetMeta) {
  dataset.value = meta
  run.start(goalInput.value, meta.datasetId)
}

// Lasso → agent: turn the live selection into a follow-up run (skips planning).
// Send the REAL cluster composition so the agent explains the actual region.
function explainSelection() {
  const n = selection.count
  if (!n) return
  const goal = `Explain what these ${n.toLocaleString()} selected points have in common.`
  goalInput.value = goal
  const sel = { count: n, clusters: selection.clusters.map((c) => ({ cluster: c.cluster, count: c.count })) }
  run.start(goal, dataset.value?.datasetId, sel)
}

// Auto-start one run so the editable plan is visible on load.
onMounted(() => run.start(goalInput.value))
</script>

<template>
  <div class="console">
    <header class="masthead">
      <h1>LatentLens <span>· agentic data-exploration console</span></h1>
      <p>Live SSE stream — the agent emits UI <em>intents</em> over the wire; the registry validates props and renders only vetted components.</p>

      <DatasetUpload :meta="dataset" @uploaded="onUploaded" @cleared="dataset = null" />

      <form class="toolbar" @submit.prevent="submit">
        <input v-model="goalInput" type="text" placeholder="Describe a goal for the agent…" :disabled="isActive" />
        <button type="submit" :data-running="isActive">
          {{ isActive ? 'Stop' : 'Run' }}
        </button>
        <span class="status" :data-status="phase">{{ phase.replace('_', ' ') }}</span>
      </form>

      <Transition name="sel">
        <div v-if="selection.count" class="sel-bar">
          <span class="sel-dot" />
          <strong>{{ selection.count.toLocaleString() }}</strong> points selected
          <span class="sel-clusters">
            <span v-for="c in selection.clusters" :key="c.cluster" class="chip">{{ c.count.toLocaleString() }}</span>
          </span>
          <button type="button" @click="explainSelection">Explain these points →</button>
        </div>
      </Transition>
    </header>

    <div class="layout">
      <main class="stream">
        <p v-if="phase === 'planning'" class="banner muted">Planning…</p>

        <PlanEditor
          v-if="phase === 'awaiting_plan'"
          :steps="plan"
          @approve="onApprovePlan"
          @cancel="run.cancel()"
        />

        <section v-for="step in steps" :key="step.id" class="step">
          <div class="step-head">
            <span class="dot" :data-status="step.status" />
            <span class="step-title">{{ step.title }}</span>
            <span v-if="step.delegation" class="agent-tag">→ {{ step.delegation.agent }}</span>
            <span v-if="step.status === 'skipped'" class="skip-tag">skipped</span>
          </div>
          <div v-if="step.delegation || step.intents.length" class="step-body">
            <DelegationTrace v-if="step.delegation" :delegation="step.delegation" />
            <IntentRenderer v-for="(intent, i) in step.intents" :key="i" :intent="intent" />
          </div>
        </section>

        <ApprovalGate v-if="gate" :gate="gate" @decide="run.decide($event)" />

        <p v-if="errorMessage" class="banner err">
          Stream error: {{ errorMessage }} — is the mock server running? (<code>npm run mock</code>)
        </p>
        <p v-else-if="phase === 'cancelled'" class="banner muted">Run cancelled.</p>
        <p v-else-if="phase === 'idle' && !steps.length" class="banner muted">
          No run yet. Enter a goal and hit Run.
        </p>
      </main>

      <aside class="registry">
        <h2>Pattern Registry</h2>
        <ul>
          <li v-for="p in patterns" :key="p.name">
            <div class="row">
              <code>{{ p.name }}</code>
              <span class="kind" :data-kind="p.kind">{{ p.kind }}</span>
            </div>
            <span class="desc">{{ p.description }}</span>
          </li>
        </ul>
        <p class="note">Only names listed here are renderable. Anything else → rejected.</p>
      </aside>
    </div>

    <TraceInspector :trace-id="traceId" :entries="trace" :running="isActive" />
  </div>
</template>

<style scoped>
.console {
  max-width: 1100px;
  margin: 0 auto;
  padding: 2.5rem 1.5rem 4rem;
}
.masthead h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 700;
}
.masthead h1 span {
  color: var(--muted);
  font-weight: 400;
  font-size: 1rem;
}
.masthead p {
  margin: 0.4rem 0 0;
  color: var(--muted);
  max-width: 720px;
}
.masthead em {
  color: var(--accent);
  font-style: normal;
}

.toolbar {
  display: flex;
  gap: 0.6rem;
  align-items: center;
  margin-top: 1.1rem;
}
.toolbar input {
  flex: 1;
  min-width: 0;
  padding: 0.55rem 0.8rem;
  font-size: 0.9rem;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.toolbar input:focus {
  outline: none;
  border-color: var(--accent);
}
.toolbar button {
  padding: 0.55rem 1.1rem;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--bg);
  background: var(--accent);
  border: none;
  border-radius: 8px;
  cursor: pointer;
}
.toolbar button[data-running='true'] {
  color: var(--text);
  background: var(--neg);
}
.status {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--muted);
  min-width: 56px;
}
.status { min-width: 110px; }
.status[data-status='planning'],
.status[data-status='awaiting_plan'],
.status[data-status='executing'],
.status[data-status='awaiting_approval'] { color: var(--accent); }
.status[data-status='awaiting_approval'] { color: var(--warn); }
.status[data-status='done'] { color: var(--pos); }
.status[data-status='error'] { color: var(--neg); }
.status[data-status='cancelled'] { color: var(--muted); }

.sel-bar {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-top: 0.8rem;
  padding: 0.55rem 0.8rem;
  font-size: 0.85rem;
  color: var(--text-2);
  background: color-mix(in srgb, var(--accent) 10%, var(--surface));
  border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border));
  border-radius: 8px;
}
.sel-bar strong {
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
.sel-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--accent);
}
.sel-clusters {
  display: flex;
  gap: 0.3rem;
}
.sel-clusters .chip {
  font-size: 0.7rem;
  color: var(--muted);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.05rem 0.35rem;
  font-variant-numeric: tabular-nums;
}
.sel-bar button {
  margin-left: auto;
  padding: 0.4rem 0.85rem;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--bg);
  background: var(--accent);
  border: none;
  border-radius: 7px;
  cursor: pointer;
}
.sel-enter-active,
.sel-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.sel-enter-from,
.sel-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 2rem;
  margin-top: 2rem;
  align-items: start;
}

.stream {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}
.step {
  position: relative;
  padding-left: 1.1rem;
  border-left: 1px solid var(--border);
}
.step-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.8rem;
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  margin-left: -1.55rem;
  background: var(--muted);
  box-shadow: 0 0 0 3px var(--bg);
}
.dot[data-status='running'] {
  background: var(--accent);
  animation: pulse 1.1s ease-in-out infinite;
}
.dot[data-status='done'] { background: var(--pos); }
.dot[data-status='skipped'] { background: var(--muted); box-shadow: 0 0 0 3px var(--bg); }
.skip-tag {
  font-size: 0.62rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 0.05rem 0.35rem;
}
.agent-tag {
  font-size: 0.64rem;
  font-family: ui-monospace, monospace;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border));
  border-radius: 5px;
  padding: 0.05rem 0.4rem;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
.step-title {
  font-size: 0.82rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-2);
}
.step-body {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.85rem;
}

.banner {
  margin: 0;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  font-size: 0.85rem;
}
.banner.muted {
  color: var(--muted);
  border: 1px dashed var(--border);
}
.banner.err {
  color: var(--neg);
  background: color-mix(in srgb, var(--neg) 8%, var(--surface));
  border: 1px solid color-mix(in srgb, var(--neg) 40%, var(--border));
}
.banner code {
  color: inherit;
}

.registry {
  position: sticky;
  top: 1.5rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.1rem 1.2rem;
}
.registry h2 {
  margin: 0 0 0.85rem;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
}
.registry ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.registry .row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.registry code {
  font-size: 0.82rem;
  color: var(--accent);
}
.kind {
  font-size: 0.6rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 0.12rem 0.4rem;
  border-radius: 5px;
  border: 1px solid var(--border);
  color: var(--muted);
}
.kind[data-kind='hero'] {
  color: var(--bg);
  background: var(--accent);
  border-color: transparent;
}
.desc {
  display: block;
  margin-top: 0.2rem;
  font-size: 0.78rem;
  color: var(--muted);
  line-height: 1.45;
}
.note {
  margin: 1rem 0 0;
  padding-top: 0.85rem;
  border-top: 1px solid var(--border);
  font-size: 0.72rem;
  color: var(--muted);
}
@media (max-width: 820px) {
  .layout { grid-template-columns: 1fr; }
  .registry { position: static; }
}
</style>
