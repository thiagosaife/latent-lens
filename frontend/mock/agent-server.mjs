// Zero-dependency mock agent server — a STAND-IN for the future FastAPI /
// LangGraph + MCP backend. Streams an interruptible plan-and-execute run as
// Server-Sent Events. The stream is HELD at two pause points (plan approval and
// per-step approval gates) and resumed via side-channel POSTs keyed by runId —
// the shape of a real LangGraph human-in-the-loop interrupt.
//
//   POST /api/runs                  body { goal }            -> text/event-stream
//   POST /api/runs/:id/plan         body { steps:[{id}] }    -> resume w/ edited plan
//   POST /api/runs/:id/decision     body { stepId, decision }-> resolve a gate
//   GET  /api/points?ref&n                                   -> binary Float32 cloud
//
// Run:  npm run mock   (Vite dev-proxies /api → here)

import { createServer } from 'node:http'
import { performance } from 'node:perf_hooks'
import {
  VERSION,
  loadConfig,
  createLogger,
  createRateLimiter,
  createAlerter,
  clientKey,
  bearer,
} from './hardening.mjs'

const cfg = loadConfig()
const PORT = cfg.port
const log = createLogger({ pretty: cfg.logPretty })
const rateLimit = createRateLimiter(cfg.rateLimitPerMin)
const alert = createAlerter(log, { slowMs: cfg.slowMs, errorSpike: cfg.errorSpike })
const startedAt = Date.now()

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const deferred = () => {
  let resolve
  const promise = new Promise((r) => { resolve = r })
  return { promise, resolve }
}

/** Live runs, so the resume POSTs can unblock the held SSE stream. */
const runs = new Map() // runId -> { planGate, decisionGates: Map, cancelled }

/* ── Step catalog: behavior is owned by the SERVER ─────────────────────────
 * The client can reorder / delete / relabel steps, but cannot redefine what a
 * step *does* — same constrained-surface principle as the Pattern Registry. */
const STEP_CATALOG = {
  profile: {
    title: 'Profile dataset',
    description: 'Types, distributions, missingness',
    intents: () => [
      { component: 'stat_tile', props: { label: 'Rows profiled', value: 48211 } },
      { component: 'stat_tile', props: { label: 'Missing cells', value: 0.037, format: 'percent', delta: -0.012 } },
      {
        component: 'summary_card',
        props: {
          title: 'Profiling complete',
          body: '12 numeric and 4 categorical columns. Two columns exceed 30% missingness and are flagged for cleaning.',
          bullets: ['signup_source — 41% missing', 'last_login — heavy right skew', 'No duplicate customer ids'],
          tone: 'positive',
        },
      },
    ],
  },
  clean: {
    title: 'Clean & impute',
    description: 'Impute missing, drop dupes',
    delegate: {
      agent: 'cleaning-agent',
      tools: [
        { tool: 'impute', args: { column: 'signup_source', strategy: 'mode' }, result: '1,784 cells imputed' },
        { tool: 'drop_duplicates', args: { key: 'customer_id' }, result: '0 duplicate rows' },
        { tool: 'log_scale', args: { column: 'last_login' }, result: 'applied' },
      ],
    },
    intents: () => [
      {
        component: 'summary_card',
        props: {
          title: 'Cleaning complete',
          body: 'Median-imputed 2 numeric columns; mode-imputed signup_source. Log-scaled last_login.',
          bullets: ['1,784 cells imputed', '0 rows dropped'],
          tone: 'neutral',
        },
      },
    ],
  },
  reduce: {
    title: 'Reduce dimensions (UMAP)',
    description: 'Project to 2D for visualization',
    needsApproval: true,
    estimate: { rows: 1_000_000, seconds: 42, cost: '$0.00 (local)' },
    intents: () => [
      { component: 'embedding_scatter', props: { pointsRef: 'umap://run_8f3a', colorBy: 'cluster', pointCount: 50000 } },
    ],
  },
  cluster: {
    title: 'Cluster (HDBSCAN)',
    description: 'Density-based segment discovery',
    delegate: {
      agent: 'segmentation-agent',
      tools: [
        { tool: 'run_hdbscan', args: { min_cluster_size: 50, metric: 'euclidean' }, result: '6 clusters · 3.1% noise' },
        { tool: 'label_segments', args: { method: 'centroid_features' }, result: '6 labels assigned' },
      ],
    },
    intents: () => [
      { component: 'stat_tile', props: { label: 'Clusters found', value: 6 } },
      {
        component: 'summary_card',
        props: {
          title: 'Six segments discovered',
          body: 'HDBSCAN found 6 dense segments and 3.1% noise points.',
          bullets: ['Largest: 31% of rows', 'Smallest: 4% of rows', 'Noise: 3.1%'],
          tone: 'neutral',
        },
      },
    ],
  },
  summarize: {
    title: 'Summarize segments',
    description: 'Name & describe each segment',
    intents: () => [
      {
        component: 'summary_card',
        props: {
          title: 'Segment summary',
          body: 'Segments split cleanly on recency and spend. The top segment is recent, high-spend, low-support-contact.',
          bullets: ['A — recent / high spend', 'B — lapsed / price-sensitive', 'C — new / exploratory'],
          tone: 'positive',
        },
      },
    ],
  },
}

const DEFAULT_PLAN = ['profile', 'clean', 'reduce', 'cluster', 'summarize']

function proposePlan() {
  return DEFAULT_PLAN.map((id) => ({
    id,
    title: STEP_CATALOG[id].title,
    description: STEP_CATALOG[id].description,
    needsApproval: Boolean(STEP_CATALOG[id].needsApproval),
    estimate: STEP_CATALOG[id].estimate,
  }))
}

const isFollowUp = (goal) => /explain|selected|in common|this segment/i.test(goal)

function explainStep(goal) {
  const m = goal.match(/(\d[\d,]*)/)
  const count = m ? Number(m[1].replace(/,/g, '')) : 0
  return {
    id: 'explain',
    title: 'Explain selection',
    intents: () => [
      {
        component: 'summary_card',
        props: {
          title: 'Selected region',
          body: 'These points form a coherent sub-cluster: tighter spread on the first UMAP axis and higher recency than the population. Reads as a recently-active, high-value segment.',
          bullets: ['Above-median recency', 'Lower signup_source missingness', 'Dominated by 2 clusters'],
          tone: 'neutral',
        },
      },
      { component: 'stat_tile', props: { label: 'Segment size', value: count || 142 } },
      { component: 'stat_tile', props: { label: 'Avg recency', value: 0.82, format: 'percent', delta: 0.14 } },
    ],
  }
}

function fmtRows(n) {
  if (n == null) return 'all'
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M` : n.toLocaleString()
}

/** Execute one step. Returns 'next' to continue or 'cancel' to abort the run. */
async function execStep(send, reg, step, isOpen) {
  send({ type: 'step_started', stepId: step.id, title: step.title })
  await delay(400)

  if (step.needsApproval) {
    const est = step.estimate ?? {}
    send({
      type: 'approval_required',
      stepId: step.id,
      title: step.title,
      message: `${step.title} would process ${fmtRows(est.rows)} rows (~${est.seconds ?? '?'}s) before it proceeds.`,
      estimate: est,
    })
    const gate = deferred()
    reg.decisionGates.set(step.id, gate)
    const decision = await gate.promise // 'approve' | 'skip' | 'cancel'
    reg.decisionGates.delete(step.id)
    if (!isOpen() || decision === 'cancel') return 'cancel'
    if (decision === 'skip') {
      send({ type: 'step_finished', stepId: step.id, skipped: true })
      await delay(150)
      return 'next'
    }
  }

  // Delegate to a specialist sub-agent, which runs attributed tool calls.
  if (step.delegate) {
    send({ type: 'delegation_started', stepId: step.id, agent: step.delegate.agent })
    await delay(350)
    let i = 0
    for (const t of step.delegate.tools) {
      if (!isOpen()) return 'cancel'
      const callId = `${step.id}-${i++}`
      send({ type: 'tool_call_started', stepId: step.id, agent: step.delegate.agent, callId, tool: t.tool, args: t.args })
      await delay(520)
      send({ type: 'tool_call_finished', stepId: step.id, callId, result: t.result })
      await delay(220)
    }
    send({ type: 'delegation_finished', stepId: step.id, agent: step.delegate.agent })
    await delay(250)
  }

  for (const intent of step.intents()) {
    if (!isOpen()) return 'cancel'
    send({ type: 'ui_intent', stepId: step.id, intent })
    await delay(450)
  }
  send({ type: 'step_finished', stepId: step.id })
  await delay(200)
  return 'next'
}

async function streamRun(res, goal, runId, traceId) {
  const reg = { planGate: deferred(), decisionGates: new Map(), cancelled: false }
  runs.set(runId, reg)
  const t0 = Date.now()

  let open = true
  const isOpen = () => open && !reg.cancelled
  const send = (event) => { if (open) res.write(`data: ${JSON.stringify(event)}\n\n`) }

  res.on('close', () => {
    open = false
    reg.cancelled = true
    reg.planGate.resolve(null) // unblock any pending awaits
    for (const g of reg.decisionGates.values()) g.resolve('cancel')
  })

  log('info', { msg: 'run.start', runId, traceId, goal })
  send({ type: 'run_started', runId, goal, traceId })
  await delay(250)

  if (isFollowUp(goal)) {
    // Lightweight follow-up: no plan phase, execute directly.
    await execStep(send, reg, explainStep(goal), isOpen)
  } else {
    send({ type: 'plan_proposed', runId, steps: proposePlan() })

    const edited = await reg.planGate.promise // resolved by POST /plan
    if (isOpen()) {
      const ids = Array.isArray(edited) && edited.length ? edited.map((s) => s.id) : DEFAULT_PLAN
      const planSteps = ids.filter((id) => STEP_CATALOG[id]).map((id) => ({ id, ...STEP_CATALOG[id] }))
      for (const step of planSteps) {
        if (!isOpen()) break
        const result = await execStep(send, reg, step, isOpen)
        if (result === 'cancel') break
      }
    }
  }

  if (open) {
    send({ type: 'run_finished', runId })
    res.end()
  }
  runs.delete(runId)
  log('info', { msg: 'run.finish', runId, traceId, duration_ms: Date.now() - t0, outcome: reg.cancelled ? 'cancelled' : 'completed' })
}

function readBody(req, done) {
  let body = ''
  req.on('data', (chunk) => { body += chunk })
  req.on('end', () => {
    try {
      done(JSON.parse(body || '{}'))
    } catch {
      done({})
    }
  })
}

/* ── Embedding point generation (unchanged) ────────────────────────────────
 * Deterministic gaussian blobs seeded by `ref`, normalized to [-1, 1], as an
 * interleaved Float32 buffer [x, y, cluster] * n. */
function makePrng(ref) {
  let h = 2166136261
  for (let i = 0; i < ref.length; i++) {
    h ^= ref.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  let s = h >>> 0
  return () => {
    s |= 0
    s = (s + 0x6d2b79f5) | 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function generatePoints(ref, n) {
  const rng = makePrng(ref)
  const K = 6
  const centers = Array.from({ length: K }, () => ({
    x: (rng() * 2 - 1) * 0.7,
    y: (rng() * 2 - 1) * 0.7,
    s: 0.05 + rng() * 0.1,
  }))
  const gauss = () => {
    let u = 0
    let v = 0
    while (u === 0) u = rng()
    while (v === 0) v = rng()
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
  }
  const clamp = (x) => Math.max(-1, Math.min(1, x))

  const out = new Float32Array(n * 3)
  for (let i = 0; i < n; i++) {
    const k = i % K
    const c = centers[k]
    out[i * 3] = clamp(c.x + gauss() * c.s)
    out[i * 3 + 1] = clamp(c.y + gauss() * c.s)
    out[i * 3 + 2] = k
  }
  return out
}

const server = createServer((req, res) => {
  const startT = performance.now()
  const url = new URL(req.url ?? '/', `http://localhost:${PORT}`)
  const path = url.pathname
  let isStream = false
  let traceId

  // Structured access log + alerting, once the response completes.
  res.on('finish', () => {
    const latencyMs = performance.now() - startT
    log('info', { msg: 'request', method: req.method, path, status: res.statusCode, latency_ms: Math.round(latencyMs), key: clientKey(req, bearer(req)), stream: isStream || undefined })
    alert({ method: req.method, path, status: res.statusCode, latencyMs, isStream, traceId })
  })

  res.setHeader('Access-Control-Allow-Origin', '*')
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization')

  if (req.method === 'OPTIONS') {
    res.writeHead(204)
    res.end()
    return
  }

  // Health check — no auth, no rate limit. Remediation for the stale-server
  // incident (POSTMORTEM.md): callers can assert which build they're hitting.
  if (req.method === 'GET' && path === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ status: 'ok', version: VERSION, uptime_s: Math.round((Date.now() - startedAt) / 1000), activeRuns: runs.size }))
    return
  }

  // Rate limiting (per token, else per IP).
  const token = bearer(req)
  const rl = rateLimit(clientKey(req, token))
  res.setHeader('X-RateLimit-Limit', String(rl.limit))
  res.setHeader('X-RateLimit-Remaining', String(rl.remaining))
  if (!rl.ok) {
    res.writeHead(429, { 'Content-Type': 'application/json', 'Retry-After': String(rl.retryAfter) })
    res.end(JSON.stringify({ error: 'rate_limited', retryAfter: rl.retryAfter }))
    return
  }

  // Bearer auth — only enforced when API_TOKEN is configured (off by default so
  // the demo runs unchanged; the control exists and is exercised in tests).
  if (cfg.apiToken && path.startsWith('/api/') && token !== cfg.apiToken) {
    res.writeHead(401, { 'Content-Type': 'application/json', 'WWW-Authenticate': 'Bearer' })
    res.end(JSON.stringify({ error: 'unauthorized' }))
    return
  }

  // Embedding points by reference, as a binary Float32 buffer.
  if (req.method === 'GET' && path === '/api/points') {
    const ref = url.searchParams.get('ref') ?? 'default'
    let n = Number(url.searchParams.get('n')) || 20000
    n = Math.max(1, Math.min(200000, Math.floor(n)))
    const points = generatePoints(ref, n)
    const body = Buffer.from(points.buffer, points.byteOffset, points.byteLength)
    res.writeHead(200, {
      'Content-Type': 'application/octet-stream',
      'Cache-Control': 'no-cache',
      'X-Point-Count': String(n),
    })
    res.end(body)
    return
  }

  // Resume a held run: edited plan approval.
  let m = path.match(/^\/api\/runs\/([^/]+)\/plan$/)
  if (req.method === 'POST' && m) {
    const reg = runs.get(m[1])
    readBody(req, (json) => {
      if (reg) reg.planGate.resolve(Array.isArray(json.steps) ? json.steps : [])
      res.writeHead(reg ? 204 : 404)
      res.end()
    })
    return
  }

  // Resume a held run: approval-gate decision.
  m = path.match(/^\/api\/runs\/([^/]+)\/decision$/)
  if (req.method === 'POST' && m) {
    const reg = runs.get(m[1])
    readBody(req, (json) => {
      const decision = json.decision === 'skip' || json.decision === 'cancel' ? json.decision : 'approve'
      const gate = reg && (reg.decisionGates.get(json.stepId) ?? [...reg.decisionGates.values()][0])
      if (gate) gate.resolve(decision)
      res.writeHead(reg ? 204 : 404)
      res.end()
    })
    return
  }

  // Start a run (long-lived SSE stream).
  if (req.method === 'POST' && path === '/api/runs') {
    isStream = true
    readBody(req, (json) => {
      const goal = typeof json.goal === 'string' && json.goal.trim() ? json.goal.trim() : 'Explore this dataset.'
      const runId = 'run_' + Math.random().toString(36).slice(2, 8)
      traceId = 'trace_' + Math.random().toString(36).slice(2, 10)
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache, no-transform',
        Connection: 'keep-alive',
      })
      res.flushHeaders?.()
      streamRun(res, goal, runId, traceId)
    })
    return
  }

  res.writeHead(404, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify({ error: 'not found' }))
})

// Fail loud and clear on a port clash — the stale-server incident (POSTMORTEM.md).
server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    log('error', { msg: 'port_in_use', port: PORT, hint: 'another mock instance is running — pkill -f agent-server.mjs' })
    process.exit(1)
  }
  throw err
})

server.listen(PORT, () => {
  log('info', { msg: 'server.start', version: VERSION, port: PORT, auth: cfg.apiToken ? 'required' : 'disabled', rate_limit_per_min: cfg.rateLimitPerMin })
})

function shutdown(signal) {
  log('info', { msg: 'server.stop', signal })
  server.close(() => process.exit(0))
  setTimeout(() => process.exit(0), 1000).unref()
}
process.on('SIGINT', () => shutdown('SIGINT'))
process.on('SIGTERM', () => shutdown('SIGTERM'))
