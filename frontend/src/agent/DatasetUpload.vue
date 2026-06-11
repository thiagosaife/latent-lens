<script setup lang="ts">
import { ref } from 'vue'
import { uploadDataset, type DatasetMeta } from './datasets'

/**
 * Dataset connect surface (spec step 1): drop or pick a CSV/Parquet file. The
 * backend parses + profiles it (auto-detecting the delimiter and whether there's
 * a header) and returns a datasetId. We show the detected schema for REVIEW first
 * — the run only starts once the user confirms ("Run analysis"). When no dataset
 * is connected, runs use the synthetic default.
 */
const props = defineProps<{ meta: DatasetMeta | null }>()
const emit = defineEmits<{ uploaded: [DatasetMeta]; cleared: [] }>()

const input = ref<HTMLInputElement | null>(null)
const busy = ref(false)
const error = ref<string | null>(null)
const dragging = ref(false)
// Parsed-but-unconfirmed dataset: the schema preview the user reviews before running.
const pending = ref<DatasetMeta | null>(null)

const DELIM_LABEL: Record<string, string> = { ',': 'comma', ';': 'semicolon', '\t': 'tab', '|': 'pipe' }
const delimLabel = (d: string | null | undefined) => (d ? (DELIM_LABEL[d] ?? JSON.stringify(d)) : '—')

async function handle(file: File | undefined) {
  if (!file) return
  busy.value = true
  error.value = null
  try {
    pending.value = await uploadDataset(file)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

function confirmRun() {
  if (!pending.value) return
  emit('uploaded', pending.value)
  pending.value = null
}

function cancelPending() {
  pending.value = null
  error.value = null
  if (input.value) input.value.value = ''
}

function onPick(e: Event) {
  handle((e.target as HTMLInputElement).files?.[0])
}
function onDrop(e: DragEvent) {
  dragging.value = false
  handle(e.dataTransfer?.files?.[0])
}
function clear() {
  if (input.value) input.value.value = ''
  error.value = null
  emit('cleared')
}
</script>

<template>
  <div class="dataset">
    <!-- connected (confirmed) dataset chip -->
    <div v-if="props.meta" class="chip">
      <span class="file">📄 {{ props.meta.name }}</span>
      <span class="stats">
        {{ props.meta.rows.toLocaleString() }} rows ·
        {{ props.meta.numeric }} num / {{ props.meta.categorical }} cat
        <template v-if="props.meta.duplicates"> · {{ props.meta.duplicates.toLocaleString() }} dup</template>
      </span>
      <button class="x" title="Disconnect" @click="clear">×</button>
    </div>

    <!-- schema preview: review the detected structure, then confirm -->
    <div v-else-if="pending" class="preview">
      <div class="pv-head">
        <span class="file">📄 {{ pending.name }}</span>
        <span class="rows">{{ pending.rows.toLocaleString() }} rows</span>
      </div>
      <div class="pv-meta">
        <span class="tag">delimiter <strong>{{ delimLabel(pending.delimiter) }}</strong></span>
        <span class="tag">{{ pending.hasHeader === false ? 'no header (col1…colN)' : 'header detected' }}</span>
        <span class="tag">{{ pending.numeric }} num / {{ pending.categorical }} cat</span>
        <span v-if="pending.duplicates" class="tag warn">{{ pending.duplicates.toLocaleString() }} duplicate rows</span>
      </div>

      <ul class="cols">
        <li v-for="c in pending.columns" :key="c.name">
          <span class="cname">{{ c.name }}</span>
          <span class="ctype" :class="c.type">{{ c.type === 'numeric' ? '#' : 'A' }}</span>
          <span class="miss">
            <span class="miss-bar"><span :style="{ width: Math.round(c.missing * 100) + '%' }" /></span>
            <span class="miss-pct">{{ Math.round(c.missing * 100) }}%</span>
          </span>
        </li>
      </ul>
      <p v-if="pending.columns.length >= 40" class="more">showing first 40 columns</p>

      <div class="pv-actions">
        <button type="button" class="ghost" @click="cancelPending">Cancel</button>
        <button type="button" class="go" @click="confirmRun">Run analysis →</button>
      </div>
    </div>

    <!-- dropzone -->
    <label
      v-else
      class="drop"
      :class="{ dragging, busy }"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
    >
      <input ref="input" type="file" accept=".csv,.tsv,.txt,.parquet,.pq" :disabled="busy" @change="onPick" />
      <span v-if="busy">parsing…</span>
      <span v-else>＋ Connect a dataset <em>(CSV / Parquet)</em></span>
    </label>

    <span v-if="error" class="err">{{ error }}</span>
  </div>
</template>

<style scoped>
.dataset {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  flex-wrap: wrap;
  margin-top: 0.9rem;
}
.drop {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: var(--text-2);
  background: var(--surface);
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 0.5rem 0.85rem;
  cursor: pointer;
}
.drop em {
  color: var(--muted);
  font-style: normal;
}
.drop:hover,
.drop.dragging {
  border-color: var(--accent);
  color: var(--text);
}
.drop.busy {
  opacity: 0.6;
  cursor: progress;
}
.drop input {
  display: none;
}

/* ── connected chip ── */
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  font-size: 0.8rem;
  background: color-mix(in srgb, var(--accent) 10%, var(--surface));
  border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border));
  border-radius: 8px;
  padding: 0.4rem 0.7rem;
}
.chip .file {
  font-weight: 600;
  color: var(--text);
}
.chip .stats {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.chip .x {
  width: 1.2rem;
  height: 1.2rem;
  line-height: 1;
  font-size: 0.95rem;
  color: var(--muted);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 5px;
  cursor: pointer;
}
.chip .x:hover {
  color: var(--neg);
  border-color: color-mix(in srgb, var(--neg) 40%, var(--border));
}

/* ── schema preview ── */
.preview {
  width: 100%;
  max-width: 520px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.85rem 1rem;
}
.pv-head {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
}
.pv-head .file {
  font-weight: 600;
  color: var(--text);
}
.pv-head .rows {
  margin-left: auto;
  font-size: 0.78rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.pv-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin: 0.55rem 0 0.7rem;
}
.tag {
  font-size: 0.7rem;
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 0.15rem 0.45rem;
}
.tag strong {
  color: var(--accent);
}
.tag.warn {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 40%, var(--border));
}
.cols {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.cols li {
  display: grid;
  grid-template-columns: 1fr auto 7.5rem;
  align-items: center;
  gap: 0.55rem;
  font-size: 0.76rem;
}
.cname {
  font-family: ui-monospace, monospace;
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctype {
  width: 1.1rem;
  height: 1.1rem;
  display: grid;
  place-items: center;
  font-size: 0.66rem;
  font-weight: 700;
  border-radius: 4px;
}
.ctype.numeric {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 16%, transparent);
}
.ctype.categorical {
  color: var(--muted);
  background: var(--surface-2);
}
.miss {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.miss-bar {
  flex: 1;
  height: 5px;
  background: var(--bg);
  border-radius: 3px;
  overflow: hidden;
}
.miss-bar span {
  display: block;
  height: 100%;
  background: var(--warn);
}
.miss-pct {
  width: 2.2rem;
  text-align: right;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.more {
  margin: 0.4rem 0 0;
  font-size: 0.68rem;
  color: var(--muted);
}
.pv-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.85rem;
}
.pv-actions button {
  font-size: 0.78rem;
  border-radius: 7px;
  padding: 0.4rem 0.8rem;
  cursor: pointer;
}
.pv-actions .ghost {
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid var(--border);
}
.pv-actions .ghost:hover {
  border-color: var(--neg);
  color: var(--neg);
}
.pv-actions .go {
  color: var(--bg);
  background: var(--accent);
  border: 1px solid transparent;
  font-weight: 600;
}
.pv-actions .go:hover {
  filter: brightness(1.08);
}

.err {
  font-size: 0.76rem;
  color: var(--neg);
}
</style>
