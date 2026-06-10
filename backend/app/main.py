"""FastAPI app exposing the AgentEvent protocol the Vue frontend already speaks.
Drop-in replacement for the Node mock on :8787 — same endpoints, same events.

  POST /api/runs                 { goal }              -> text/event-stream
  POST /api/runs/:id/plan        { steps: [{ id }] }   -> resume w/ edited plan
  POST /api/runs/:id/decision    { stepId, decision }  -> resolve a gate
  GET  /api/points?ref&n                               -> binary Float32 cloud
  GET  /health
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import VERSION, ml
from . import datasets as ds_mod
from . import mcp_client
from .orchestrator import start_run
from .runs import RUNS

logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open the in-process MCP session (analysis tools) for the app's lifetime.
    await mcp_client.startup()
    yield
    await mcp_client.shutdown()


app = FastAPI(title="LatentLens backend", version=VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


class RunBody(BaseModel):
    goal: str | None = None
    datasetId: str | None = None
    selection: dict | None = None  # lasso composition for the explain follow-up: {count, clusters:[{cluster,count}]}


class StepRef(BaseModel):
    id: str


class PlanBody(BaseModel):
    steps: list[StepRef] = []


class DecisionBody(BaseModel):
    stepId: str | None = None
    decision: str = "approve"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "activeRuns": len(RUNS)}


@app.post("/api/datasets")
async def upload_dataset(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if len(raw) > ds_mod.MAX_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    try:
        dataset = ds_mod.parse_upload(file.filename or "upload.csv", raw)
    except Exception as e:  # parse errors → 400 with the reason
        raise HTTPException(status_code=400, detail=f"could not parse dataset: {e}")
    dataset_id = ds_mod.register(dataset)
    return {"datasetId": dataset_id, "name": dataset.name, **ds_mod.preview(dataset)}


@app.post("/api/runs")
async def create_run(body: RunBody) -> StreamingResponse:
    goal = (body.goal or "").strip() or "Explore this dataset."
    state = start_run(goal, body.datasetId, body.selection)
    return StreamingResponse(state.subscribe(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/runs/{run_id}/stream")
async def resume_run(run_id: str, after: int = 0):
    """Reconnect to a still-running (or just-finished) run and replay everything
    after sequence `after` — the resume-on-dropped-stream path. 404 once the run
    has been reaped, which the client treats as 'gone, give up'."""
    state = RUNS.get(run_id)
    if state is None:
        return Response(status_code=404)
    return StreamingResponse(state.subscribe(after), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/runs/{run_id}/cancel", status_code=204)
async def cancel_run(run_id: str) -> Response:
    """Intentional stop (vs. an accidental drop): tear the run down server-side so
    it doesn't keep computing in the background. Resolves any held gate so the
    producer loop unwinds."""
    state = RUNS.get(run_id)
    if state is None:
        return Response(status_code=404)
    state.cancelled = True
    if not state.plan_gate.done():
        state.plan_gate.set_result([])
    for fut in list(state.decision_gates.values()):
        if not fut.done():
            fut.set_result("cancel")
    return Response(status_code=204)


@app.post("/api/runs/{run_id}/plan", status_code=204)
async def approve_plan(run_id: str, body: PlanBody) -> Response:
    state = RUNS.get(run_id)
    if state is None:
        return Response(status_code=404)
    if not state.plan_gate.done():
        state.plan_gate.set_result([s.id for s in body.steps])
    return Response(status_code=204)


@app.post("/api/runs/{run_id}/decision", status_code=204)
async def decide(run_id: str, body: DecisionBody) -> Response:
    state = RUNS.get(run_id)
    if state is None:
        return Response(status_code=404)
    decision = body.decision if body.decision in ("approve", "skip", "cancel") else "approve"
    fut = state.decision_gates.get(body.stepId or "")
    if fut is None and state.decision_gates:
        fut = next(iter(state.decision_gates.values()))
    if fut is not None and not fut.done():
        fut.set_result(decision)
    return Response(status_code=204)


@app.get("/api/points")
def points(ref: str = "", n: int = 0) -> Response:
    buf = ml.get_points(ref)
    if buf is None:
        return Response(status_code=404)
    return Response(
        content=buf,
        media_type="application/octet-stream",
        headers={"X-Point-Count": str(len(buf) // 12), "Cache-Control": "no-cache"},
    )
