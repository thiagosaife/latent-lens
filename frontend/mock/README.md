# Mock agent server

A zero-dependency stand-in for the real FastAPI / LangGraph + MCP backend (see
[`../../backend`](../../backend)) — handy for running the frontend with no Python.
Streams an interruptible plan-and-execute run over SSE and serves embedding
points. The hardening controls here (`hardening.mjs`) are the same ones the real
backend runs as middleware.

It speaks the same `AgentEvent` protocol, so the frontend is unchanged either way.
It is deliberately a *frontend convenience*, not feature-parity: the real-backend
extras — CSV/Parquet upload, the feature-grounded explain (`feature_delta`), and
the resumable per-run buffer (`/stream` replay) — live only in the FastAPI service.

## Run

```bash
npm run mock        # this server on :8787
npm run dev:all     # mock + Vite together
```

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/health` | `{ status, version, uptime_s, activeRuns }` — no auth, no rate limit |
| `POST` | `/api/runs` | start a run → `text/event-stream` (held at plan + approval gates) |
| `POST` | `/api/runs/:id/plan` | resume with the edited plan `{ steps: [{ id }] }` |
| `POST` | `/api/runs/:id/decision` | resolve a gate `{ stepId, decision }` |
| `GET`  | `/api/points?ref&n` | binary `Float32` point cloud `[x, y, cluster] * n` |

## Hardening (env, all optional)

| Var | Default | Effect |
|-----|---------|--------|
| `MOCK_PORT` | `8787` | listen port |
| `API_TOKEN` | _unset_ | when set, `/api/*` requires `Authorization: Bearer <token>` |
| `RATE_LIMIT_PER_MIN` | `240` | per-client (token, else IP) fixed-window limit → `429` |
| `SLOW_MS` | `1500` | non-stream latency over this logs a `slow_request` alert |
| `ERROR_SPIKE` | `5` | `5xx` within 60s over this logs an `error_spike` alert |
| `LOG_PRETTY` | _unset_ | `1` = human logs; default is one JSON object per line |

Requests are logged structurally with method, path, status, and latency; runs
log `run.start` / `run.finish` correlated by `traceId`. The frontend attaches a
bearer token when `VITE_API_TOKEN` is set.

See [`../../POSTMORTEM.md`](../../POSTMORTEM.md) for why `/health` and the
`EADDRINUSE` handling exist.
