"""Plan-and-execute orchestration as a LangGraph StateGraph.

The graph IS the engine: a planner node, a plan-approval gate, then a per-step
execute loop with a per-step approval gate — both gates are first-class
`interrupt()`s, not bolted on. Nodes emit AgentEvents via the stream writer; the
analysis runs over MCP tools. A thin SSE bridge drives `graph.astream`, emits the
gate events from the interrupt payloads (so they aren't re-emitted when an
interrupted node re-runs on resume), and resumes with `Command(resume=...)` once
the client answers via the existing /plan and /decision side-channel POSTs.

The wire protocol (AgentEvent) and endpoints are unchanged — the frontend and
the mock both still speak it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import datasets as ds_mod
from . import llm, mcp_client, ml
from .catalog import CATALOG, build_plan, catalog_meta
from .events import sse
from .runs import RUNS, RunState

log = logging.getLogger("latentlens.run")
_FOLLOW_UP = ("selected points", "in common")


def _newid(prefix: str) -> str:
    return prefix + secrets.token_hex(4)


def _is_follow_up(goal: str) -> bool:
    g = goal.lower()
    return any(w in g for w in _FOLLOW_UP)


def _fmt_rows(n: int | None) -> str:
    if n is None:
        return "all"
    return f"{n / 1_000_000:.0f}M" if n >= 1_000_000 else f"{n:,}"


# ── Graph state ─────────────────────────────────────────────────────────────

class GraphState(TypedDict, total=False):
    goal: str
    dataset_id: str
    run_id: str
    proposed: list[dict]  # plan_proposed steps
    plan: list[str]       # approved step ids
    idx: int              # current step index
    profile: dict
    clean: dict
    points_ref: str
    point_count: int
    sizes: list[int]
    summary: dict
    decision: str         # current gate decision


# ── Nodes ───────────────────────────────────────────────────────────────────

async def _plan_node(state: GraphState) -> dict:
    """Planner decomposes the goal into an ordered plan (runs once, checkpointed)."""
    planned = await asyncio.to_thread(llm.generate_plan, state["goal"], catalog_meta())
    proposed = build_plan(planned)
    return {"proposed": proposed, "plan": [s["id"] for s in proposed], "idx": 0}


def _plan_gate_node(state: GraphState) -> dict:
    """Hold for plan approval. The bridge emits plan_proposed from this payload;
    resume carries the edited step ids."""
    edited = interrupt({"event": "plan_proposed", "steps": state["proposed"]})
    ids = [i for i in (edited or []) if i in CATALOG] or state["plan"]
    return {"plan": ids, "idx": 0}


async def _start_node(state: GraphState) -> dict:
    """Emit step_started before any gate (runs once, so it isn't re-emitted)."""
    sid = state["plan"][state["idx"]]
    get_stream_writer()({"type": "step_started", "stepId": sid, "title": CATALOG[sid]["title"]})
    await asyncio.sleep(0.35)
    return {}


def _gate_node(state: GraphState) -> dict:
    """Approval gate for a heavy step. The bridge emits approval_required from
    this payload; resume carries the decision."""
    sid = state["plan"][state["idx"]]
    est = CATALOG[sid].get("estimate", {})
    decision = interrupt(
        {
            "event": "approval_required",
            "stepId": sid,
            "title": CATALOG[sid]["title"],
            "message": f"{CATALOG[sid]['title']} would process {_fmt_rows(est.get('rows'))} rows (~{est.get('seconds', '?')}s) before it proceeds.",
            "estimate": est,
        }
    )
    return {"decision": decision}


async def _work_node(state: GraphState) -> dict:
    """Run the step (MCP tool) and emit its delegation trace + UI intents. Runs
    once per step (the gate is a separate node), so emissions don't repeat."""
    sid = state["plan"][state["idx"]]
    c = CATALOG[sid]
    w = get_stream_writer()
    nxt = state["idx"] + 1
    decision = state.get("decision")

    if c.get("needsApproval") and decision in ("skip", "cancel"):
        if decision == "cancel":
            return {"idx": len(state["plan"])}  # route to END
        w({"type": "step_finished", "stepId": sid, "skipped": True})
        await asyncio.sleep(0.15)
        return {"idx": nxt, "decision": None}

    updates = await _run_step_tool(sid, state)
    merged: dict[str, Any] = {**state, **updates}

    agent = c.get("delegate")
    if agent:
        w({"type": "delegation_started", "stepId": sid, "agent": agent})
        await asyncio.sleep(0.3)
        for i, (tool, args, result) in enumerate(_tools_for(sid, merged)):
            cid = f"{sid}-{i}"
            w({"type": "tool_call_started", "stepId": sid, "agent": agent, "callId": cid, "tool": tool, "args": args})
            await asyncio.sleep(0.5)
            w({"type": "tool_call_finished", "stepId": sid, "callId": cid, "result": result})
            await asyncio.sleep(0.2)
        w({"type": "delegation_finished", "stepId": sid, "agent": agent})
        await asyncio.sleep(0.25)

    for intent in _intents_for(sid, merged):
        w({"type": "ui_intent", "stepId": sid, "intent": intent})
        await asyncio.sleep(0.45)

    w({"type": "step_finished", "stepId": sid})
    await asyncio.sleep(0.2)
    return {**updates, "idx": nxt, "decision": None}


def _route_after_plan_gate(state: GraphState) -> str:
    return "start" if state.get("plan") else "end"


def _route_after_start(state: GraphState) -> str:
    sid = state["plan"][state["idx"]]
    return "gate" if CATALOG[sid].get("needsApproval") else "work"


def _route_after_work(state: GraphState) -> str:
    return "start" if state["idx"] < len(state["plan"]) else "end"


def _build_graph():
    b = StateGraph(GraphState)
    b.add_node("plan", _plan_node)
    b.add_node("plan_gate", _plan_gate_node)
    b.add_node("start", _start_node)
    b.add_node("gate", _gate_node)
    b.add_node("work", _work_node)
    b.add_edge(START, "plan")
    b.add_edge("plan", "plan_gate")
    b.add_conditional_edges("plan_gate", _route_after_plan_gate, {"start": "start", "end": END})
    b.add_conditional_edges("start", _route_after_start, {"gate": "gate", "work": "work"})
    b.add_edge("gate", "work")
    b.add_conditional_edges("work", _route_after_work, {"start": "start", "end": END})
    return b.compile(checkpointer=MemorySaver())


_graph = _build_graph()


# ── Step analysis (over MCP) + UI intents ───────────────────────────────────

async def _run_step_tool(sid: str, state: GraphState) -> dict:
    """Invoke the step's analysis tool over MCP → state updates."""
    did, rid = state["dataset_id"], state["run_id"]
    if sid == "profile":
        return {"profile": await mcp_client.call("profile_dataset", dataset_id=did)}
    if sid == "clean":
        return {"clean": await mcp_client.call("clean_dataset", dataset_id=did)}
    if sid == "reduce":
        r = await mcp_client.call("reduce_dimensions", run_id=rid, dataset_id=did)
        return {"points_ref": r["pointsRef"], "point_count": r["pointCount"], "sizes": r["sizes"]}
    if sid == "cluster":
        ref = state.get("points_ref")
        return {"sizes": (await mcp_client.call("cluster_segments", points_ref=ref))["sizes"]} if ref else {}
    if sid == "summarize":
        return {"summary": await mcp_client.call("summarize_segments", goal=state["goal"], profile=state.get("profile") or {}, sizes=state.get("sizes") or [])}
    return {}


def _tools_for(sid: str, st: dict) -> list[tuple[str, dict, str]]:
    if sid == "clean":
        cl = st.get("clean") or {}
        return [
            ("impute", {"strategy": "mean"}, f"{cl.get('missing_cells', 0):,} cells imputed"),
            ("drop_duplicates", {}, f"{cl.get('duplicates', 0)} duplicate rows"),
            ("standardize", {"columns": cl.get("numeric", 0)}, f"{cl.get('numeric', 0)} numeric columns scaled"),
        ]
    if sid == "cluster":
        k = len(st.get("sizes") or []) or ml.K_CLUSTERS
        return [
            ("run_kmeans", {"k": k, "metric": "euclidean"}, f"{k} clusters"),
            ("label_segments", {"method": "centroid_features"}, f"{k} labels assigned"),
        ]
    return []


def _intents_for(sid: str, st: dict) -> list[dict]:
    if sid == "profile":
        p = st.get("profile") or {}
        flagged = p.get("flagged_high_missing", [])[:4]
        dup = p.get("duplicates", 0)
        miss_by = p.get("missing_by_col", {})
        bullets = [f"{c} — {int(miss_by.get(c, 0) * 100)}% missing" for c in flagged]
        bullets.append(f"{dup:,} duplicate rows" if dup else "No duplicate rows")
        return [
            {"component": "stat_tile", "props": {"label": "Rows profiled", "value": p.get("rows", 0)}},
            {"component": "stat_tile", "props": {"label": "Missing cells", "value": p.get("missing_fraction", 0), "format": "percent"}},
            {
                "component": "summary_card",
                "props": {
                    "title": "Profiling complete",
                    "body": f"{p.get('numeric', 0)} numeric and {p.get('categorical', 0)} categorical columns. "
                    f"{len(flagged)} columns exceed 30% missingness and are flagged for cleaning.",
                    "bullets": bullets,
                    "tone": "positive",
                },
            },
        ]

    if sid == "clean":
        cl = st.get("clean") or {}
        miss, dup, num = cl.get("missing_cells", 0), cl.get("duplicates", 0), cl.get("numeric", 0)
        return [
            {
                "component": "summary_card",
                "props": {
                    "title": "Cleaning complete",
                    "body": f"Imputed {miss:,} missing cells, removed {dup} duplicate rows, and standardized {num} numeric columns.",
                    "bullets": [f"{miss:,} cells imputed", f"{dup} duplicate rows removed", f"{num} columns standardized"],
                    "tone": "neutral",
                },
            }
        ]

    if sid == "reduce":
        ref = st.get("points_ref")
        if not ref:
            return []
        return [{"component": "embedding_scatter", "props": {"pointsRef": ref, "colorBy": "cluster", "pointCount": st.get("point_count", 0)}}]

    if sid == "cluster":
        sizes = st.get("sizes") or []
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
        return [{"component": "summary_card", "props": st["summary"]}] if st.get("summary") else []

    return []


# ── SSE bridge: drive the graph, surface interrupts, resume ──────────────────

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
            ds_id = dataset_id or ds_mod.register(ml.generate_dataset())
            config = {"configurable": {"thread_id": run_id}}
            inp: Any = {"goal": goal, "dataset_id": ds_id, "run_id": run_id}

            while True:
                async for ev in _graph.astream(inp, config, stream_mode="custom"):
                    yield sse(ev)
                snap = await _graph.aget_state(config)
                if not snap.interrupts:
                    break
                payload = snap.interrupts[0].value
                if payload["event"] == "plan_proposed":
                    yield sse({"type": "plan_proposed", "runId": run_id, "steps": payload["steps"]})
                    ids = await state.plan_gate
                    if state.cancelled:
                        break
                    inp = Command(resume=ids)
                else:  # approval_required
                    yield sse(
                        {
                            "type": "approval_required",
                            "stepId": payload["stepId"],
                            "title": payload["title"],
                            "message": payload["message"],
                            "estimate": payload.get("estimate"),
                        }
                    )
                    fut = loop.create_future()
                    state.decision_gates[payload["stepId"]] = fut
                    try:
                        decision = await fut
                    finally:
                        state.decision_gates.pop(payload["stepId"], None)
                    if decision == "cancel":
                        state.cancelled = True
                        break
                    inp = Command(resume=decision)

        if not state.cancelled:
            yield sse({"type": "run_finished", "runId": run_id})
    finally:
        RUNS.pop(run_id, None)
        outcome = "cancelled" if state.cancelled else "completed"
        log.info(json.dumps({"msg": "run.finish", "runId": run_id, "traceId": trace_id, "duration_ms": int((time.time() - t0) * 1000), "outcome": outcome}))


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
