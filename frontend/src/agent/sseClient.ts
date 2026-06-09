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

/**
 * Start an agent run and consume its SSE stream.
 *
 * Uses `fetch` + a ReadableStream reader rather than `EventSource` on purpose:
 * we POST the goal in the body (EventSource is GET-only) and can attach auth
 * headers later. This is the transport a real AG-UI / FastAPI backend speaks.
 */
export async function streamRun(
  goal: string,
  handlers: StreamHandlers,
  opts: StreamOptions = {},
): Promise<void> {
  const url = opts.url ?? '/api/runs'

  let res: Response
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...authHeaders() },
      body: JSON.stringify(opts.datasetId ? { goal, datasetId: opts.datasetId } : { goal }),
      signal: opts.signal,
    })
  } catch (err) {
    if (!isAbort(err)) handlers.onError?.(err)
    return
  }

  if (!res.ok || !res.body) {
    handlers.onError?.(new Error(`Stream request failed: HTTP ${res.status}`))
    return
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
        const event = decodeFrame(frame)
        if (event) handlers.onEvent(event)
      }
    }
  } catch (err) {
    if (!isAbort(err)) handlers.onError?.(err)
  } finally {
    reader.releaseLock()
  }
}

/** Pull the `data:` lines out of one SSE frame, JSON-parse, and validate. */
function decodeFrame(frame: string): AgentEvent | null {
  const data = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).replace(/^ /, ''))
    .join('\n')

  if (!data) return null // comment-only frame (e.g. ": keep-alive")
  try {
    return parseAgentEvent(JSON.parse(data))
  } catch {
    return null
  }
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

/**
 * Client → server side-channel for resuming a held run (approve a plan, decide
 * an approval gate). Fire-and-forget POST; the server resolves the paused step
 * and the SSE stream picks back up.
 */
export async function sendCommand(path: string, body: unknown): Promise<void> {
  await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
}
