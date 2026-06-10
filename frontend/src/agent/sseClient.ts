import { parseAgentEvent, type AgentEvent } from './events'
import { authHeaders } from './http'

export interface StreamHandlers {
  onEvent: (event: AgentEvent) => void
  onError?: (err: unknown) => void
}

export interface StreamOptions {
  /** Endpoint that returns `text/event-stream`. Defaults to the dev-proxied path. */
  url?: string
  signal?: AbortSignal
  /** Analyze an uploaded dataset instead of the synthetic default. */
  datasetId?: string
}

// Resume-on-drop: if the stream dies mid-run (network blip), reconnect to the
// run's replay endpoint with backoff. A clean terminal event, an intentional
// abort, or a 404 (run reaped/unknown) all stop the loop.
const MAX_RECONNECTS = 6
const RECONNECT_BACKOFF_MS = [300, 600, 1200, 2400, 4000]

type Outcome = 'terminal' | 'dropped' | 'gone' | 'aborted' | 'failed'

/** Mutable cursor carried across the initial connection and any reconnects. */
interface StreamCtx {
  lastSeq: number
  runId: string | null
  terminal: boolean
  madeProgress: boolean
}

/**
 * Start an agent run and consume its SSE stream, transparently resuming if the
 * connection drops mid-run.
 *
 * Uses `fetch` + a ReadableStream reader rather than `EventSource` on purpose:
 * we POST the goal in the body (EventSource is GET-only) and can attach auth
 * headers. Each frame carries an `id:` sequence number; on a dropped stream we
 * reconnect to `GET /api/runs/:id/stream?after=<lastSeq>` and the server replays
 * only what we missed — no duplicates, no gaps. (The real backend supports the
 * replay endpoint; the dev mock 404s it, which we treat as 'give up'.)
 */
export async function streamRun(
  goal: string,
  handlers: StreamHandlers,
  opts: StreamOptions = {},
): Promise<void> {
  const baseUrl = opts.url ?? '/api/runs'
  const ctx: StreamCtx = { lastSeq: 0, runId: null, terminal: false, madeProgress: false }

  let outcome = await consume(
    () =>
      fetch(baseUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...authHeaders() },
        body: JSON.stringify(opts.datasetId ? { goal, datasetId: opts.datasetId } : { goal }),
        signal: opts.signal,
      }),
    ctx,
    handlers,
  )

  let attempt = 0
  while (outcome === 'dropped' && ctx.runId && !opts.signal?.aborted) {
    if (attempt >= MAX_RECONNECTS) {
      handlers.onError?.(new Error('stream lost — gave up reconnecting after several attempts'))
      return
    }
    const slept = await sleep(RECONNECT_BACKOFF_MS[Math.min(attempt, RECONNECT_BACKOFF_MS.length - 1)]!, opts.signal)
    if (!slept) return // aborted during backoff
    attempt++
    ctx.madeProgress = false
    outcome = await consume(
      () =>
        fetch(`/api/runs/${ctx.runId}/stream?after=${ctx.lastSeq}`, {
          method: 'GET',
          headers: { Accept: 'text/event-stream', ...authHeaders() },
          signal: opts.signal,
        }),
      ctx,
      handlers,
    )
    if (ctx.madeProgress) attempt = 0 // forward progress → reset the backoff
  }
}

/** Read one SSE response to its end. Returns how it ended so the caller can decide whether to resume. */
async function consume(doFetch: () => Promise<Response>, ctx: StreamCtx, handlers: StreamHandlers): Promise<Outcome> {
  let res: Response
  try {
    res = await doFetch()
  } catch (err) {
    if (isAbort(err)) return 'aborted'
    return 'dropped' // connection error → resumable
  }

  if (res.status === 404) return 'gone'
  if (!res.ok || !res.body) {
    handlers.onError?.(new Error(`Stream request failed: HTTP ${res.status}`))
    return 'failed'
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

      // SSE frames are separated by a blank line.
      let sep: number
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)

        const id = frameId(frame)
        if (id !== null) ctx.lastSeq = id // advance the resume cursor on every identified frame

        const event = decodeFrame(frame)
        if (!event) continue // comment-only frame (heartbeat) → keep-alive, no state change
        ctx.madeProgress = true
        if (event.type === 'run_started') ctx.runId = event.runId
        if (event.type === 'run_finished' || event.type === 'error') ctx.terminal = true
        handlers.onEvent(event)
      }
    }
  } catch (err) {
    if (isAbort(err)) return 'aborted'
    return 'dropped' // read error mid-stream → resumable
  } finally {
    reader.releaseLock()
  }

  // Server closed the stream cleanly: terminal if we saw the end, else resumable.
  return ctx.terminal ? 'terminal' : 'dropped'
}

/** The `id:` sequence number of an SSE frame, or null if it has none. */
function frameId(frame: string): number | null {
  for (const line of frame.split('\n')) {
    if (line.startsWith('id:')) {
      const n = Number(line.slice(3).trim())
      return Number.isFinite(n) ? n : null
    }
  }
  return null
}

/** Pull the `data:` lines out of one SSE frame, JSON-parse, and validate. */
function decodeFrame(frame: string): AgentEvent | null {
  const data = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).replace(/^ /, ''))
    .join('\n')

  if (!data) return null // comment-only frame (e.g. ": hb" heartbeat)
  try {
    return parseAgentEvent(JSON.parse(data))
  } catch {
    return null
  }
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

/** Sleep `ms`, resolving false if the signal aborts first (so we stop reconnecting). */
function sleep(ms: number, signal?: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve(false)
    const onAbort = () => {
      clearTimeout(timer)
      resolve(false)
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve(true)
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

/**
 * Client → server side-channel for resuming a held run (approve a plan, decide
 * an approval gate, cancel). Fire-and-forget POST; the server resolves the
 * paused step and the SSE stream picks back up.
 */
export async function sendCommand(path: string, body: unknown): Promise<void> {
  await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
}
