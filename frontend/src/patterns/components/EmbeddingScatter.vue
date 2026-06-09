<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch, computed } from 'vue'
import createScatterplot from 'regl-scatterplot'
import { setSelection, clearSelection } from '../../agent/selection'
import { authHeaders } from '../../agent/http'

/**
 * HERO — the interactive embedding explorer. WebGL via regl-scatterplot so it
 * stays smooth past ~50k points, with pan/zoom and lasso selection. Points come
 * from the backend by `pointsRef` (never raw over the intent wire); the lasso
 * selection is pushed into the shared selection context as live agent input.
 */
const props = defineProps<{
  pointsRef: string
  colorBy?: string
  pointCount?: number
}>()

type Scatterplot = ReturnType<typeof createScatterplot>

const PALETTE: string[] = ['#5eead4', '#a78bfa', '#fb7185', '#fbbf24', '#60a5fa', '#f472b6']
const CLUSTER_NAMES = ['A', 'B', 'C', 'D', 'E', 'F']
const colorFor = (i: number) => PALETTE[i] ?? '#8b95a7'
const nameFor = (i: number) => CLUSTER_NAMES[i] ?? String(i)

const container = ref<HTMLDivElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)

let scatterplot: Scatterplot | null = null
let cats: Uint16Array | null = null // per-point cluster, for selection stats
let ro: ResizeObserver | null = null

const status = ref<'loading' | 'ready' | 'error'>('loading')
const errorMsg = ref<string | null>(null)
const total = ref(0)
const selectedIdx = ref<number[]>([])
const lassoMode = ref(false)

function toggleLasso(): void {
  lassoMode.value = !lassoMode.value
  scatterplot?.set({ mouseMode: lassoMode.value ? 'lasso' : 'panZoom' })
}

const selStats = computed(() => {
  const idx = selectedIdx.value
  if (!idx.length || !cats) return null
  const counts = new Map<number, number>()
  for (const i of idx) {
    const c = cats[i]
    if (c === undefined) continue
    counts.set(c, (counts.get(c) ?? 0) + 1)
  }
  const clusters = [...counts.entries()]
    .map(([cluster, count]) => ({ cluster, count }))
    .sort((a, b) => b.count - a.count)
  return { count: idx.length, clusters }
})

async function loadPoints(): Promise<{ x: Float32Array; y: Float32Array; z: Float32Array }> {
  const n = props.pointCount ?? 50000
  const res = await fetch(`/api/points?ref=${encodeURIComponent(props.pointsRef)}&n=${n}`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`points fetch failed: HTTP ${res.status}`)
  const buf = await res.arrayBuffer()
  const f = new Float32Array(buf) // interleaved [x, y, cluster] * count
  const count = Math.floor(f.length / 3)
  const x = new Float32Array(count)
  const y = new Float32Array(count)
  const z = new Float32Array(count)
  const c = new Uint16Array(count)
  for (let i = 0; i < count; i++) {
    x[i] = f[i * 3] ?? 0
    y[i] = f[i * 3 + 1] ?? 0
    const cat = f[i * 3 + 2] ?? 0
    z[i] = cat
    c[i] = cat
  }
  cats = c
  total.value = count
  return { x, y, z }
}

function initScatterplot(width: number, height: number): void {
  const canvas = canvasEl.value
  if (!canvas) return

  scatterplot = createScatterplot({
    canvas,
    width,
    height,
    pointSize: 3,
    pointColor: PALETTE,
    colorBy: 'z',
    backgroundColor: '#0f1420',
    lassoColor: '#5eead4',
    lassoOnLongPress: true,
    mouseMode: 'panZoom',
  })

  scatterplot.subscribe('select', ({ points }) => {
    selectedIdx.value = points
    const stats = selStats.value // recomputes synchronously on access
    setSelection({
      pointsRef: props.pointsRef,
      count: points.length,
      indices: points,
      clusters: stats?.clusters ?? [],
    })
  })
  scatterplot.subscribe('deselect', () => {
    selectedIdx.value = []
    clearSelection()
  })
}

async function render(): Promise<void> {
  status.value = 'loading'
  errorMsg.value = null
  try {
    const pts = await loadPoints()
    const el = container.value
    if (!scatterplot) initScatterplot(el?.clientWidth ?? 640, el?.clientHeight ?? 380)
    await scatterplot?.draw({ x: pts.x, y: pts.y, z: pts.z })
    status.value = 'ready'
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : String(err)
    status.value = 'error'
  }
}

function clearSel(): void {
  scatterplot?.deselect() // fires 'deselect' → clears store + selectedIdx
}

onMounted(() => {
  render()
  ro = new ResizeObserver(() => {
    const el = container.value
    if (scatterplot && el && el.clientWidth > 0) {
      scatterplot.set({ width: el.clientWidth, height: el.clientHeight })
    }
  })
  if (container.value) ro.observe(container.value)
})

onBeforeUnmount(() => {
  ro?.disconnect()
  clearSelection()
  scatterplot?.destroy()
  scatterplot = null
})

watch(
  () => [props.pointsRef, props.pointCount],
  () => {
    selectedIdx.value = []
    clearSelection()
    render()
  },
)
</script>

<template>
  <div class="embed">
    <div class="embed-head">
      <span class="badge">HERO · WebGL</span>
      <button type="button" class="lasso-btn" :class="{ active: lassoMode }" @click="toggleLasso">
        {{ lassoMode ? '◉ Lasso on' : '◯ Lasso' }}
      </button>
      <span class="ref">{{ pointsRef }} · {{ total.toLocaleString() }} pts</span>
    </div>

    <div class="embed-main">
      <div ref="container" class="canvas-wrap">
        <canvas ref="canvasEl" />
        <div v-if="status === 'loading'" class="overlay">loading embedding…</div>
        <div v-else-if="status === 'error'" class="overlay err">{{ errorMsg }}</div>
        <div class="hint">{{ lassoMode ? 'drag to select a region' : 'scroll to zoom · drag to pan' }}</div>
      </div>

      <aside class="detail">
        <h4>Selection</h4>
        <template v-if="selStats">
          <div class="sel-count">{{ selStats.count.toLocaleString() }} <span>points</span></div>
          <div class="bar">
            <span
              v-for="c in selStats.clusters"
              :key="c.cluster"
              :style="{ width: (c.count / selStats.count) * 100 + '%', background: colorFor(c.cluster) }"
            />
          </div>
          <ul class="legend">
            <li v-for="c in selStats.clusters" :key="c.cluster">
              <span class="sw" :style="{ background: colorFor(c.cluster) }" />
              cluster {{ nameFor(c.cluster) }}
              <span class="n">{{ c.count.toLocaleString() }}</span>
            </li>
          </ul>
          <button class="clear" @click="clearSel">Clear selection</button>
        </template>
        <p v-else class="empty">Toggle <strong>Lasso</strong>, then drag on the plot to select a region — the selection becomes live context for the agent.</p>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.embed {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.75rem;
  width: 100%;
  max-width: 720px;
}
.embed-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.6rem;
}
.lasso-btn {
  font-size: 0.66rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: var(--text-2);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.22rem 0.55rem;
  cursor: pointer;
}
.lasso-btn:hover {
  border-color: var(--accent);
}
.lasso-btn.active {
  color: var(--bg);
  background: var(--accent);
  border-color: transparent;
}
.ref {
  margin-left: auto;
}
.badge {
  font-size: 0.62rem;
  letter-spacing: 0.08em;
  font-weight: 700;
  color: var(--bg);
  background: var(--accent);
  padding: 0.2rem 0.45rem;
  border-radius: 5px;
}
.ref {
  font-family: ui-monospace, monospace;
  font-size: 0.72rem;
  color: var(--muted);
}

.embed-main {
  display: flex;
  gap: 0.75rem;
}
.canvas-wrap {
  position: relative;
  flex: 1;
  min-width: 0;
  height: 380px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.canvas-wrap canvas {
  display: block;
  width: 100%;
  height: 100%;
}
.overlay {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: 0.82rem;
  color: var(--muted);
  background: rgba(15, 20, 32, 0.6);
}
.overlay.err {
  color: var(--neg);
}
.hint {
  position: absolute;
  left: 8px;
  bottom: 8px;
  font-size: 0.68rem;
  color: var(--muted);
  background: rgba(11, 14, 20, 0.7);
  padding: 0.2rem 0.45rem;
  border-radius: 5px;
  pointer-events: none;
}

.detail {
  width: 188px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}
.detail h4 {
  margin: 0.15rem 0 0.6rem;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--muted);
}
.sel-count {
  font-size: 1.5rem;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}
.sel-count span {
  font-size: 0.8rem;
  font-weight: 400;
  color: var(--muted);
}
.bar {
  display: flex;
  height: 8px;
  margin: 0.6rem 0;
  border-radius: 4px;
  overflow: hidden;
  background: var(--bg);
}
.bar span {
  display: block;
  height: 100%;
}
.legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  font-size: 0.76rem;
  color: var(--text-2);
}
.legend li {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.legend .sw {
  width: 9px;
  height: 9px;
  border-radius: 2px;
}
.legend .n {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
}
.clear {
  margin-top: 0.85rem;
  padding: 0.4rem 0.6rem;
  font-size: 0.74rem;
  color: var(--text);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.clear:hover {
  border-color: var(--neg);
  color: var(--neg);
}
.empty {
  margin: 0;
  font-size: 0.76rem;
  color: var(--muted);
  line-height: 1.5;
}
@media (max-width: 640px) {
  .embed-main {
    flex-direction: column;
  }
  .detail {
    width: auto;
  }
}
</style>
