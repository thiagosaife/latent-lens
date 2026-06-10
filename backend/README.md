# LatentLens backend

A real FastAPI service behind the **same `AgentEvent` SSE protocol** the Vue
frontend already speaks — a drop-in replacement for the Node mock on `:8787`.

The orchestration is a **LangGraph** plan-and-execute `StateGraph` with the
plan-approval and per-step approval gates as first-class `interrupt()`s. Analysis
runs over **MCP** tools (FastMCP, connected in-process). The work is real: numpy
profiling, PCA (SVD) + k-means over a 50k-row synthetic dataset *or your uploaded
CSV/Parquet*, a Claude-generated plan, and a Claude-generated summary card — each
with a deterministic offline fallback so it runs fully without a key.

See [`app/orchestrator.py`](app/orchestrator.py) for the graph and the SSE bridge;
[`../README.md`](../README.md) for the whole-project writeup.

## Setup

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# optional: enable live Claude (planner + summaries). Without a key the app
# uses deterministic fallbacks and still runs fully.
cp .env.example .env      # then put YOUR Anthropic API key in .env
```

`.env` is gitignored — each tester supplies their own key. Get one at
<https://console.anthropic.com/> (the account needs a non-zero credit balance,
or calls return `400 credit balance too low` and the app falls back).

## Run

```bash
# backend alone (:8787)
.venv/bin/python -m uvicorn app.main:app --port 8787

# backend + Vite together (from frontend/)
cd ../frontend && npm run dev:api      # → http://localhost:5173
```

The Vite proxy already targets `:8787`, so this swaps cleanly with `npm run dev:all`
(the Node mock). Verify in a browser with `npm run verify:browser`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/runs` | start a run → `text/event-stream` (held at plan + approval gates) |
| `POST` | `/api/runs/:id/plan` | resume with the edited plan `{ steps: [{ id }] }` |
| `POST` | `/api/runs/:id/decision` | resolve a gate `{ stepId, decision }` |
| `GET`  | `/api/runs/:id/stream?after=N` | reconnect to a dropped run → replay frames after seq `N`, then follow live |
| `POST` | `/api/runs/:id/cancel` | intentional stop — tear the run down server-side |
| `POST` | `/api/datasets` | upload a CSV/Parquet file → `{ datasetId, … }` to analyze |
| `GET`  | `/api/points?ref&n` | binary `Float32` cloud `[x, y, cluster] * n` |
| `GET`  | `/health` | `{ status, version, activeRuns }` |

## Resilient streaming

A run's execution is **decoupled from any one HTTP connection**: `POST /api/runs`
spawns a background task that drives the LangGraph graph and appends each
AgentEvent to a per-run, `id:`-sequenced buffer; the HTTP response just
*subscribes* (replay-then-follow). So:

- **Heartbeats** — a subscriber emits an SSE comment (`: hb`) whenever the buffer
  is idle, keeping a connection held at a gate alive past proxy/browser idle
  timeouts.
- **Resume-by-runId** — if the stream drops, the client reconnects to
  `GET /api/runs/:id/stream?after=<last seq>` and the server replays exactly what
  was missed (contiguous, no gaps, no dupes) then follows live — even frames the
  run produced while *no one* was connected. The frontend transport does this
  automatically with backoff.
- **Bounded** — an unanswered gate times out (`LATENTLENS_GATE_TTL_S`, default
  600s) and a finished run's buffer is reaped after `LATENTLENS_RESUME_TTL_S`
  (default 60s), so abandoned runs can't leak.

(The Node mock streams the same protocol and adds the heartbeats; the resumable
buffer is a real-backend feature — the mock 404s `/stream`, which the client
treats as "run gone, stop retrying".)

## Real vs. wired

- **Real now:** the synthetic dataset (latent segments) *or your uploaded
  CSV/Parquet* flows through MCP tools — `profile_dataset` (counts, missingness,
  duplicates) → `reduce_dimensions` (PCA via SVD + k-means, served by ref) →
  `cluster_segments` (sizes). Tool-call args/results reflect actual numbers (e.g.
  imputed cell count = missing-fraction × rows).
- **Real delegation:** the `clean`/`cluster` steps delegate to specialist
  sub-agents that invoke *fine-grained* MCP tools for real — the cleaning-agent
  calls `impute_missing`/`drop_duplicates`/`standardize_columns`, the
  segmentation-agent calls `run_kmeans`/`label_segments`. The delegation trace is
  those genuine calls (real args, real results), not an illustrative mock-up, and
  their returns ARE the step's result.
- **Claude (wired, needs a key):** two places. `generate_plan` decomposes the
  goal into an ordered plan, enum-constrained to the step catalog; the
  `summarize` step writes the `summary_card` props — both via
  `claude-opus-4-8` structured outputs (the "constrained generation surface" made
  real). Without `ANTHROPIC_API_KEY` (or with a $0-balance account) each falls
  back to deterministic, goal-aware logic, so the pipeline runs fully offline.
  Set the key to light up real generation.

## Next (not yet built)

Durable persistence — swap `MemorySaver` → a Postgres/SQLite LangGraph
checkpointer + a shared run buffer (e.g. Redis) + object-store embeddings, so
resume survives a process restart or multiple workers (a config swap now that
LangGraph owns the orchestration and the buffer is already abstracted).
