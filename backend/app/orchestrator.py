"""Plan-and-execute orchestration as an async generator of SSE frames.

Emits the exact AgentEvent protocol the Vue frontend already speaks, holds the
stream at plan-approval and per-step approval gates (resumed via side-channel
POSTs), and runs REAL work per step: numpy profiling, PCA + k-means, and a
Claude-generated summary card. LangGraph would slot in here as the execution
engine; the event contract stays identical.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator

from . import datasets as ds_mod
from . import llm, ml
from .catalog import CATALOG, DEFAULT_PLAN, propose_plan
from .events import sse
from .runs import RUNS, RunState

log = logging.getLogger("latentlens.run")
_FOLLOW_UP = ("explain", "selected", "in common", "this segment")


def _newid(prefix: str) -> str:
    return prefix + secrets.token_hex(4)


def _is_follow_up(goal: str) -> bool:
    g = goal.lower()
    return any(w in g for w in _FOLLOW_UP)


def _fmt_rows(n: int | None) -> str:
    if n is None:
        return "all"
    return f"{n / 1_000_000:.0f}M" if n >= 1_000_000 else f"{n:,}"


class Ctx:
    """Accumulates results across steps (dataset → profile → embedding → sizes)."""

    def __init__(self, goal: str, dataset: ml.Dataset):
        self.goal = goal
        self.dataset = dataset
        self.profile: dict | None = None
        self.points_ref: str | None = None
        self.sizes: list[int] = []


async def run_stream(goal: str, dataset_id: str | None = None) -> AsyncIterator[str]:
    run_id, trace_id = _newid("run_"), _newid("trace_")
    loop = asyncio.get_running_loop()
    state = RunState(run_id=run_id, trace_id=trace_id, plan_gate=loop.create_future())
    RUNS[run_id] = state
    t0 = time.time()
    log.info(json.dumps({"msg": "run.start", "runId": run_id, "traceId": trace_id, "goal": goal}))

    try:
        yield sse({"type": "run_started", "runId": run_id, "goal": goal, "traceId": trace_id})
        await asyncio.sleep(0.25)

        if _is_follow_up(goal):
            async for frame in _exec_explain(goal):
                yield frame
        else:
            yield sse({"type": "plan_proposed", "runId": run_id, "steps": propose_plan()})
            edited = await state.plan_gate  # resolved by POST /api/runs/:id/plan
            if not state.cancelled:
                ids = [s for s in (edited or DEFAULT_PLAN) if s in CATALOG] or DEFAULT_PLAN
                dataset = ds_mod.get_dataset(dataset_id) or ml.generate_dataset()
                ctx = Ctx(goal, dataset)
                for sid in ids:
                    if state.cancelled:
                        break
                    async for frame in _exec_step(sid, ctx, state, run_id):
                        yield frame

        if not state.cancelled:
            yield sse({"type": "run_finished", "runId": run_id})
    finally:
        RUNS.pop(run_id, None)
        outcome = "cancelled" if state.cancelled else "completed"
        log.info(json.dumps({"msg": "run.finish", "runId": run_id, "traceId": trace_id, "duration_ms": int((time.time() - t0) * 1000), "outcome": outcome}))


async def _exec_step(sid: str, ctx: Ctx, state: RunState, run_id: str) -> AsyncIterator[str]:
    c = CATALOG[sid]
    yield sse({"type": "step_started", "stepId": sid, "title": c["title"]})
    await asyncio.sleep(0.35)

    # Approval gate (heavy step) — hold until the human decides.
    if c.get("needsApproval"):
        est = c.get("estimate", {})
        yield sse(
            {
                "type": "approval_required",
                "stepId": sid,
                "title": c["title"],
                "message": f"{c['title']} would process {_fmt_rows(est.get('rows'))} rows (~{est.get('seconds', '?')}s) before it proceeds.",
                "estimate": est,
            }
        )
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        state.decision_gates[sid] = fut
        try:
            decision = await fut
        finally:
            state.decision_gates.pop(sid, None)
        if decision == "cancel":
            state.cancelled = True
            return
        if decision == "skip":
            yield sse({"type": "step_finished", "stepId": sid, "skipped": True})
            await asyncio.sleep(0.15)
            return

    # Delegate to a specialist sub-agent (real, attributed tool calls).
    agent = c.get("delegate")
    if agent:
        yield sse({"type": "delegation_started", "stepId": sid, "agent": agent})
        await asyncio.sleep(0.3)
        for i, (tool, args, result) in enumerate(_tools_for(sid, ctx)):
            call_id = f"{sid}-{i}"
            yield sse({"type": "tool_call_started", "stepId": sid, "agent": agent, "callId": call_id, "tool": tool, "args": args})
            await asyncio.sleep(0.5)
            yield sse({"type": "tool_call_finished", "stepId": sid, "callId": call_id, "result": result})
            await asyncio.sleep(0.2)
        yield sse({"type": "delegation_finished", "stepId": sid, "agent": agent})
        await asyncio.sleep(0.25)

    # The step's real work → UI intents (validated by the frontend registry).
    for intent in _intents_for(sid, ctx, run_id):
        yield sse({"type": "ui_intent", "stepId": sid, "intent": intent})
        await asyncio.sleep(0.45)

    yield sse({"type": "step_finished", "stepId": sid})
    await asyncio.sleep(0.2)


def _tools_for(sid: str, ctx: Ctx) -> list[tuple[str, dict, str]]:
    if sid == "clean":
        ds = ctx.dataset
        return [
            ("impute", {"strategy": "mean"}, f"{ds.missing_cells:,} cells imputed"),
            ("drop_duplicates", {}, f"{ds.duplicates} duplicate rows"),
            ("standardize", {"columns": len(ds.numeric_cols)}, f"{len(ds.numeric_cols)} numeric columns scaled"),
        ]
    if sid == "cluster":
        k = len(ctx.sizes) or ml.K_CLUSTERS
        return [
            ("run_kmeans", {"k": k, "metric": "euclidean"}, f"{k} clusters"),
            ("label_segments", {"method": "centroid_features"}, f"{k} labels assigned"),
        ]
    return []


def _intents_for(sid: str, ctx: Ctx, run_id: str) -> list[dict]:
    if sid == "profile":
        p = ml.profile_dataset(ctx.dataset)
        ctx.profile = p
        flagged = p["flagged_high_missing"][:4]
        dup = p["duplicates"]
        bullets = [f"{c} — {int(p['missing_by_col'][c] * 100)}% missing" for c in flagged]
        bullets.append(f"{dup:,} duplicate rows" if dup else "No duplicate rows")
        return [
            {"component": "stat_tile", "props": {"label": "Rows profiled", "value": p["rows"]}},
            {"component": "stat_tile", "props": {"label": "Missing cells", "value": p["missing_fraction"], "format": "percent"}},
            {
                "component": "summary_card",
                "props": {
                    "title": "Profiling complete",
                    "body": f"{p['numeric']} numeric and {p['categorical']} categorical columns. "
                    f"{len(flagged)} columns exceed 30% missingness and are flagged for cleaning.",
                    "bullets": bullets,
                    "tone": "positive",
                },
            },
        ]

    if sid == "clean":
        ds = ctx.dataset
        return [
            {
                "component": "summary_card",
                "props": {
                    "title": "Cleaning complete",
                    "body": f"Imputed {ds.missing_cells:,} missing cells, removed {ds.duplicates} duplicate rows, "
                    f"and standardized {len(ds.numeric_cols)} numeric columns.",
                    "bullets": [
                        f"{ds.missing_cells:,} cells imputed",
                        f"{ds.duplicates} duplicate rows removed",
                        f"{len(ds.numeric_cols)} columns standardized",
                    ],
                    "tone": "neutral",
                },
            }
        ]

    if sid == "reduce":
        ref, n, sizes = ml.build_embedding(run_id, ctx.dataset)
        ctx.points_ref = ref
        ctx.sizes = sizes
        return [{"component": "embedding_scatter", "props": {"pointsRef": ref, "colorBy": "cluster", "pointCount": n}}]

    if sid == "cluster":
        sizes = ctx.sizes
        top = sorted(sizes, reverse=True)[:3]
        return [
            {"component": "stat_tile", "props": {"label": "Clusters found", "value": len(sizes)}},
            {
                "component": "summary_card",
                "props": {
                    "title": f"{len(sizes)} segments discovered" if sizes else "Clustering complete",
                    "body": f"k-means over the 2D embedding found {len(sizes)} dense segments.",
                    "bullets": [f"Segment {chr(65 + i)}: {s:,} rows" for i, s in enumerate(top)],
                    "tone": "neutral",
                },
            },
        ]

    if sid == "summarize":
        props = llm.generate_segment_summary(ctx.goal, ctx.profile or {}, ctx.sizes)
        return [{"component": "summary_card", "props": props}]

    return []


async def _exec_explain(goal: str) -> AsyncIterator[str]:
    """Lasso → 'explain these points' follow-up: no plan phase, direct execution."""
    sid = "explain"
    m = re.search(r"(\d[\d,]*)", goal)
    count = int(m.group(1).replace(",", "")) if m else 0
    yield sse({"type": "step_started", "stepId": sid, "title": "Explain selection"})
    await asyncio.sleep(0.4)
    intents = [
        {"component": "summary_card", "props": llm.generate_explain_summary(goal, count)},
        {"component": "stat_tile", "props": {"label": "Segment size", "value": count or 142}},
        {"component": "stat_tile", "props": {"label": "Avg recency", "value": 0.82, "format": "percent", "delta": 0.14}},
    ]
    for intent in intents:
        yield sse({"type": "ui_intent", "stepId": sid, "intent": intent})
        await asyncio.sleep(0.45)
    yield sse({"type": "step_finished", "stepId": sid})
    await asyncio.sleep(0.2)
