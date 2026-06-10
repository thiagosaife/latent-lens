# LatentLens — frontend

Vue 3 + TypeScript generative-UI client. The agent streams typed **UI intents**
over SSE; a **Pattern Registry** validates their props with Zod and renders only
vetted components. See the [root README](../README.md) for the full
problem→approach→architecture writeup.

## Run

```bash
nvm use --lts
npm install

npm run dev:all     # Vite :5173 + zero-dep Node mock SSE server :8787  (no Python)
npm run dev:api     # Vite :5173 + the real FastAPI backend :8787       (see ../backend)
npm run dev         # Vite only (expects a backend already on :8787)
```

```bash
npm run build              # vue-tsc typecheck + vite build
npm run verify:browser     # Playwright (system Chrome) drives the synthetic flow → e2e/shots/
npm run verify:upload      # Playwright drives the CSV-upload flow
```

> A stale dev server on `:8787` causes silent, confusing failures
> ([POSTMORTEM](../POSTMORTEM.md)). Run `pkill -f 'agent-server.mjs|uvicorn|vite'`
> first if a run looks off.

## Layout

```
src/
├── patterns/                 ← the constrained generation surface (the centerpiece)
│   ├── registry.ts           ·  vetted patterns; resolveIntent() is the single gate to the DOM
│   ├── types.ts              ·  PatternDef, RawIntent, hero|generated kinds
│   ├── IntentRenderer.vue    ·  renders a resolved intent (or an explicit rejection)
│   └── components/           ·  StatTile · SummaryCard · EmbeddingScatter (WebGL hero) · UnknownIntent
├── agent/
│   ├── events.ts             ·  Zod AgentEvent schema — the wire contract, validated at the boundary
│   ├── sseClient.ts          ·  POST + ReadableStream SSE reader; resumes a dropped stream by runId (?after=seq) with backoff
│   ├── useAgentRun.ts        ·  phase state machine: planning→awaiting_plan→executing⇄awaiting_approval→done
│   ├── PlanEditor.vue        ·  drag-reorder / rename / delete the proposed plan
│   ├── ApprovalGate.vue      ·  cost/time estimate → approve · skip · cancel
│   ├── DelegationTrace.vue   ·  attributed sub-agent tool calls (inputs→outputs)
│   ├── TraceInspector.vue    ·  bottom-drawer observability: spans, durations, raw I/O
│   ├── DatasetUpload.vue     ·  CSV/Parquet upload → POST /api/datasets
│   ├── selection.ts          ·  reactive lasso-selection store (shared, not Vue events)
│   └── http.ts               ·  optional bearer auth header (VITE_API_TOKEN, off by default)
└── App.vue                   ·  console layout wiring it together
```

## How a frame becomes UI

```
SSE frame ──▶ parseAgentEvent()       (events.ts — Zod validates the envelope)
          ──▶ useAgentRun reducer     (folds the stream into steps/intents/trace)
          ──▶ resolveIntent()         (registry.ts — Zod validates the PROPS)
          ──▶ IntentRenderer          (renders the vetted component, or a typed rejection)
```

Untrusted at every hop: the envelope's `intent.props` stay `unknown` until the
registry checks them against the chosen pattern's schema. An unknown component
name or bad props is surfaced as a rejection, never rendered.

## Toolchain

Vite 8 · TypeScript 6 · Vue 3.5 · Zod 4 · `regl-scatterplot` 1.16 · Playwright
(system Chrome, no Chromium download). `tsconfig` runs with
`noUncheckedIndexedAccess` + `erasableSyntaxOnly`.
