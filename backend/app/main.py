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

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import VERSION, ml
from . import datasets as ds_mod
from .orchestrator import run_stream
from .runs import RUNS

logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title="LatentLens backend", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


class RunBody(BaseModel):
    goal: str | None = None
    datasetId: str | None = None


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
    return StreamingResponse(run_stream(goal, body.datasetId), media_type="text/event-stream", headers=SSE_HEADERS)


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
