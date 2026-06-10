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
import os
import re
import secrets
import time
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import datasets as ds_mod
from . import llm, mcp_client, ml
from .catalog import CATALOG, build_plan, catalog_meta
from .runs import RESUME_TTL_S, RUNS, RunState

log = logging.getLogger("latentlens.run")
_FOLLOW_UP = ("selected points", "in common")
# Bound a gate wait so an abandoned run (held at a gate, no one ever answers and
# no client reconnects) can't leak the background task forever.
GATE_TTL_S = float(os.environ.get("LATENTLENS_GATE_TTL_S", "600"))


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

    agent = c.get("delegate")
    if agent:
        # Real delegation: the specialist sub-agent actually invokes MCP tools and
        # the trace shows those real calls (real args, real results) — and their
        # returns ARE the step's result, not a separate mock-up.
        w({"type": "delegation_started", "stepId": sid, "agent": agent})
        await asyncio.sleep(0.3)
        updates = await _run_delegated(sid, state, w, agent)
        w({"type": "delegation_finished", "stepId": sid, "agent": agent})
        await asyncio.sleep(0.25)
    else:
        updates = await _run_step_tool(sid, state)

    merged: dict[str, Any] = {**state, **updates}
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


async def _traced_call(w, sid: str, agent: str, tool: str, args: dict, summarize) -> dict:
    """Make a REAL MCP tool call, bracketed by trace events carrying the actual
    args and a one-line summary of the actual result."""
    cid = f"{sid}-{tool}"
    w({"type": "tool_call_started", "stepId": sid, "agent": agent, "callId": cid, "tool": tool, "args": args})
    await asyncio.sleep(0.4)  # pace the trace animation; the in-process call itself is sub-ms
    result = await mcp_client.call(tool, **args)
    w({"type": "tool_call_finished", "stepId": sid, "callId": cid, "result": summarize(result)})
    await asyncio.sleep(0.2)
    return result


async def _run_delegated(sid: str, state: GraphState, w, agent: str) -> dict:
    """Run a delegated step as a sequence of real MCP tool calls; the tools'
    actual returns become the step's result."""
    did = state["dataset_id"]
    if sid == "clean":
        imp = await _traced_call(w, sid, agent, "impute_missing", {"dataset_id": did, "strategy": "mean"},
                                 lambda r: f"{r['imputed_cells']:,} cells imputed")
        dup = await _traced_call(w, sid, agent, "drop_duplicates", {"dataset_id": did},
                                 lambda r: f"{r['removed']} duplicate rows removed")
        std = await _traced_call(w, sid, agent, "standardize_columns", {"dataset_id": did},
                                 lambda r: f"{r['columns']} numeric columns scaled")
        return {"clean": {"missing_cells": imp["imputed_cells"], "duplicates": dup["removed"], "numeric": std["columns"]}}
    if sid == "cluster":
        ref = state.get("points_ref")
        if not ref:  # cluster reordered before reduce → nothing to segment
            return {}
        k = len(state.get("sizes") or []) or ml.K_CLUSTERS
        km = await _traced_call(w, sid, agent, "run_kmeans", {"points_ref": ref, "k": k},
                                lambda r: f"{len(r['sizes'])} clusters · sizes {r['sizes']}")
        await _traced_call(w, sid, agent, "label_segments", {"points_ref": ref},
                           lambda r: f"{r['segments']} segments labeled")
        return {"sizes": km["sizes"]}
    return {}


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


# ── Run lifecycle: a background task produces; HTTP responses subscribe ───────

def start_run(goal: str, dataset_id: str | None = None) -> RunState:
    """Set up a run and start driving it in the background, returning its state.

    The HTTP response streams `state.subscribe()` — so execution is decoupled
    from any one connection: a dropped client can reconnect (GET the run's stream
    with `after=<last seq>`) and replay what it missed, even mid-gate."""
    run_id, trace_id = _newid("run_"), _newid("trace_")
    loop = asyncio.get_running_loop()
    state = RunState(run_id=run_id, trace_id=trace_id, plan_gate=loop.create_future())
    RUNS[run_id] = state
    asyncio.create_task(_drive(state, goal, dataset_id))
    return state


async def _await_gate(state: RunState, fut: asyncio.Future) -> Any:
    """Await a gate decision, bounded so an abandoned run can't leak forever. On
    timeout the run is marked cancelled (the resume POSTs guard with `not done()`,
    so a late answer is harmlessly ignored)."""
    try:
        return await asyncio.wait_for(fut, timeout=GATE_TTL_S)
    except asyncio.TimeoutError:
        log.info(json.dumps({"msg": "run.gate_timeout", "runId": state.run_id}))
        state.cancelled = True
        return None


async def _drive(state: RunState, goal: str, dataset_id: str | None) -> None:
    """Produce the run's event stream into the resumable buffer (not an HTTP
    response). Survives subscriber disconnects; emits an `error` frame on failure
    so subscribers never hang, and always `finish()`es."""
    run_id, trace_id = state.run_id, state.trace_id
    t0 = time.time()
    log.info(json.dumps({"msg": "run.start", "runId": run_id, "traceId": trace_id, "goal": goal}))
    try:
        state.emit({"type": "run_started", "runId": run_id, "goal": goal, "traceId": trace_id})
        await asyncio.sleep(0.25)

        if _is_follow_up(goal):
            await _exec_explain(state, goal)
        else:
            loop = asyncio.get_running_loop()
            ds_id = dataset_id or ds_mod.register(ml.generate_dataset())
            config = {"configurable": {"thread_id": run_id}}
            inp: Any = {"goal": goal, "dataset_id": ds_id, "run_id": run_id}

            while True:
                async for ev in _graph.astream(inp, config, stream_mode="custom"):
                    state.emit(ev)
                snap = await _graph.aget_state(config)
                if not snap.interrupts:
                    break
                payload = snap.interrupts[0].value
                if payload["event"] == "plan_proposed":
                    state.emit({"type": "plan_proposed", "runId": run_id, "steps": payload["steps"]})
                    ids = await _await_gate(state, state.plan_gate)
                    if state.cancelled:
                        break
                    inp = Command(resume=ids)
                else:  # approval_required
                    state.emit(
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
                        decision = await _await_gate(state, fut)
                    finally:
                        state.decision_gates.pop(payload["stepId"], None)
                    if state.cancelled or decision == "cancel":
                        state.cancelled = True
                        break
                    inp = Command(resume=decision)

        if not state.cancelled:
            state.emit({"type": "run_finished", "runId": run_id})
    except Exception as e:  # surface as an error frame so subscribers don't hang
        log.exception("run failed")
        state.emit({"type": "error", "message": str(e)})
    finally:
        state.finish()
        outcome = "cancelled" if state.cancelled else "completed"
        log.info(json.dumps({"msg": "run.finish", "runId": run_id, "traceId": trace_id, "duration_ms": int((time.time() - t0) * 1000), "outcome": outcome}))
        asyncio.create_task(_reap(run_id))


async def _reap(run_id: str) -> None:
    """Drop a finished run's buffer after a grace period — late reconnects still
    replay the tail, but memory doesn't grow unbounded."""
    await asyncio.sleep(RESUME_TTL_S)
    RUNS.pop(run_id, None)


async def _exec_explain(state: RunState, goal: str) -> None:
    """Lasso → 'explain these points' follow-up: no plan phase, direct execution."""
    sid = "explain"
    m = re.search(r"(\d[\d,]*)", goal)
    count = int(m.group(1).replace(",", "")) if m else 0
    state.emit({"type": "step_started", "stepId": sid, "title": "Explain selection"})
    await asyncio.sleep(0.4)
    intents = [
        {"component": "summary_card", "props": llm.generate_explain_summary(goal, count)},
        {"component": "stat_tile", "props": {"label": "Segment size", "value": count or 142}},
        {"component": "stat_tile", "props": {"label": "Avg recency", "value": 0.82, "format": "percent", "delta": 0.14}},
    ]
    for intent in intents:
        state.emit({"type": "ui_intent", "stepId": sid, "intent": intent})
        await asyncio.sleep(0.45)
    state.emit({"type": "step_finished", "stepId": sid})
    await asyncio.sleep(0.2)
