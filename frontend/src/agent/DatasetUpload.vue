<script setup lang="ts">
import { ref } from 'vue'
import { uploadDataset, type DatasetMeta } from './datasets'

/**
 * Dataset connect surface (spec step 1): drop or pick a CSV/Parquet file; the
 * backend parses + profiles it and returns a datasetId the run analyzes. When no
 * dataset is connected, runs use the synthetic default.
 */
const props = defineProps<{ meta: DatasetMeta | null }>()
const emit = defineEmits<{ uploaded: [DatasetMeta]; cleared: [] }>()

const input = ref<HTMLInputElement | null>(null)
const busy = ref(false)
const error = ref<string | null>(null)
const dragging = ref(false)

async function handle(file: File | undefined) {
  if (!file) return
  busy.value = true
  error.value = null
  try {
    emit('uploaded', await uploadDataset(file))
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
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
    <!-- connected dataset chip -->
    <div v-if="props.meta" class="chip">
      <span class="file">📄 {{ props.meta.name }}</span>
      <span class="stats">
        {{ props.meta.rows.toLocaleString() }} rows ·
        {{ props.meta.numeric }} num / {{ props.meta.categorical }} cat
        <template v-if="props.meta.duplicates"> · {{ props.meta.duplicates.toLocaleString() }} dup</template>
      </span>
      <button class="x" title="Disconnect" @click="clear">×</button>
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
  align-items: center;
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
.err {
  font-size: 0.76rem;
  color: var(--neg);
}
</style>
