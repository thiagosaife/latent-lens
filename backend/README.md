# LatentLens backend

A real FastAPI service behind the **same `AgentEvent` SSE protocol** the Vue
frontend already speaks — a drop-in replacement for the Node mock on `:8787`.
Does real work: numpy profiling, PCA + k-means over a 50k-row synthetic dataset,
and a Claude-generated summary card (with an offline fallback).

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

## Endpoints (identical to the mock)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/runs` | start a run → `text/event-stream` (held at plan + approval gates) |
| `POST` | `/api/runs/:id/plan` | resume with the edited plan `{ steps: [{ id }] }` |
| `POST` | `/api/runs/:id/decision` | resolve a gate `{ stepId, decision }` |
| `GET`  | `/api/points?ref&n` | binary `Float32` cloud `[x, y, cluster] * n` |
| `GET`  | `/health` | `{ status, version, activeRuns }` |

## Real vs. wired

- **Real now:** numpy dataset with latent segments → `profile_dataset` (counts,
  missingness) → `build_embedding` (PCA via SVD + k-means, served by ref) →
  `cluster` sizes. Tool-call args/results reflect actual numbers (e.g. imputed
  cell count = missing-fraction × rows).
- **Claude (wired, needs a key):** the `summarize` step asks Claude
  (`claude-opus-4-8`) to write the `summary_card` props under a constrained JSON
  schema (structured outputs) — the "constrained generation surface" made real.
  Without `ANTHROPIC_API_KEY` (or `ant auth login`) it falls back to a
  deterministic summary, so the pipeline runs fully offline. Set the key to
  light up real generation.

## Next (not yet built)

CSV/Parquet upload (replace the synthetic dataset); Claude-generated *plans*
(not just summaries); LangGraph + MCP for the orchestration/tool layer;
heartbeats + resume-by-runId for dropped streams; Postgres checkpointer +
object-store embeddings for production.
