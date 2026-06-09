// Production-hardening middleware for the mock agent server. These are the same
// controls the real FastAPI backend would run as middleware — implemented here
// against Node's http so they're genuinely exercised, not hand-waved.
//
// Configure via env (all optional):
//   API_TOKEN            when set, /api/* requires `Authorization: Bearer <token>`
//   RATE_LIMIT_PER_MIN   requests/min per client key (default 240)
//   SLOW_MS              slow-request alert threshold, non-stream (default 1500)
//   ERROR_SPIKE          5xx count within 60s that fires an alert (default 5)
//   LOG_PRETTY=1         human-readable logs instead of JSON lines

export const VERSION = '0.6.0'

export function loadConfig(env = process.env) {
  const num = (v, d) => (v ? Number(v) : d)
  return {
    port: num(env.MOCK_PORT, 8787),
    apiToken: env.API_TOKEN || null,
    rateLimitPerMin: num(env.RATE_LIMIT_PER_MIN, 240),
    slowMs: num(env.SLOW_MS, 1500),
    errorSpike: num(env.ERROR_SPIKE, 5),
    logPretty: env.LOG_PRETTY === '1',
  }
}

/** Structured logger: one JSON object per line (or pretty for local dev). */
export function createLogger({ pretty }) {
  return function log(level, fields) {
    const rec = { ts: new Date().toISOString(), level, ...fields }
    if (!pretty) {
      console.log(JSON.stringify(rec))
      return
    }
    const { ts, msg, ...rest } = rec
    const tail = Object.keys(rest).length ? ' ' + JSON.stringify(rest) : ''
    console.log(`${ts} ${level.toUpperCase().padEnd(5)} ${msg ?? ''}${tail}`)
  }
}

/** Stable per-caller key: the bearer token if present, else the remote IP. */
export function clientKey(req, token) {
  if (token) return `tok:${token.slice(0, 8)}`
  return `ip:${req.socket.remoteAddress ?? 'unknown'}`
}

export function bearer(req) {
  const h = req.headers['authorization']
  return typeof h === 'string' && h.startsWith('Bearer ') ? h.slice(7).trim() : null
}

/** Fixed-window rate limiter, per key, per 60s. */
export function createRateLimiter(limitPerMin) {
  const windows = new Map() // key -> { start, count }
  return function take(key) {
    const now = Date.now()
    let w = windows.get(key)
    if (!w || now - w.start >= 60_000) {
      w = { start: now, count: 0 }
      windows.set(key, w)
    }
    w.count++
    const ok = w.count <= limitPerMin
    return {
      ok,
      limit: limitPerMin,
      remaining: Math.max(0, limitPerMin - w.count),
      retryAfter: ok ? 0 : Math.ceil((w.start + 60_000 - now) / 1000),
    }
  }
}

/**
 * Threshold alerting: emits a structured ALERT log line on slow requests and on
 * 5xx spikes within a rolling 60s window (debounced). A real deployment wires
 * these to PagerDuty/Slack; here they go to the log so they're observable.
 */
export function createAlerter(log, { slowMs, errorSpike }) {
  const recent5xx = []
  let lastSpikeAlert = 0
  return function record({ method, path, status, latencyMs, isStream, traceId }) {
    if (!isStream && latencyMs > slowMs) {
      log('warn', {
        msg: 'ALERT slow_request',
        alert: 'slow_request',
        method,
        path,
        status,
        latency_ms: Math.round(latencyMs),
        threshold_ms: slowMs,
        traceId,
      })
    }
    if (status >= 500) {
      const now = Date.now()
      recent5xx.push(now)
      while (recent5xx.length && now - recent5xx[0] > 60_000) recent5xx.shift()
      if (recent5xx.length >= errorSpike && now - lastSpikeAlert > 30_000) {
        lastSpikeAlert = now
        log('error', { msg: 'ALERT error_spike', alert: 'error_spike', count_60s: recent5xx.length, threshold: errorSpike })
      }
    }
  }
}
