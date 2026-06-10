"""Step catalog — the server owns what each step *does* (behavior); the client
can reorder/delete/relabel but not redefine. Same constrained-surface principle
as the Pattern Registry, applied to the plan."""

from __future__ import annotations

DEFAULT_PLAN = ["profile", "clean", "reduce", "cluster", "summarize"]

CATALOG: dict[str, dict] = {
    "profile": {"title": "Profile dataset", "description": "Types, distributions, missingness"},
    "clean": {"title": "Clean & impute", "description": "Impute missing, drop dupes", "delegate": "cleaning-agent"},
    "reduce": {
        "title": "Reduce dimensions (PCA)",
        "description": "Project to 2D for visualization",
        "needsApproval": True,  # estimate is computed per-run from the real dataset (see estimate_for)
    },
    "cluster": {"title": "Cluster (k-means)", "description": "Discover dense segments", "delegate": "segmentation-agent"},
    "summarize": {"title": "Summarize segments", "description": "Name & describe each segment"},
}

# Rough local PCA(SVD)+k-means throughput, used to turn a real row count into a
# time estimate. Order-of-magnitude only — the point is it tracks the data, not a
# fixed literal.
ROWS_PER_SEC = 25_000


def estimate_for(rows: int) -> dict:
    """Cost/time estimate for a heavy (approval-gated) step, derived from the
    REAL dataset size — not a hardcoded number. Cost is genuinely $0 (local numpy,
    no API spend)."""
    rows = max(0, int(rows))
    return {"rows": rows, "seconds": max(1, round(rows / ROWS_PER_SEC)), "cost": "$0.00 (local)"}


def catalog_meta() -> list[dict]:
    """Step ids + titles + descriptions for the planner to choose from."""
    return [{"id": sid, "title": c["title"], "description": c["description"]} for sid, c in CATALOG.items()]


def _step(sid: str, description: str, rows: int) -> dict:
    # Attach a real, per-run estimate to approval-gated steps; omit it otherwise
    # (the frontend Zod schema treats `estimate` as optional → must be undefined).
    c = CATALOG[sid]
    step = {"id": sid, "title": c["title"], "description": description, "needsApproval": bool(c.get("needsApproval"))}
    if c.get("needsApproval"):
        step["estimate"] = estimate_for(rows)
    return step


def build_plan(planned: list[dict], rows: int = 0) -> list[dict]:
    """Turn the planner's [{id, reason}] into plan_proposed steps. The reason
    becomes the step description — the agent's rationale, shown in the editor.
    `rows` is the real dataset size, used to compute each gate's estimate."""
    plan = [_step(item["id"], item.get("reason") or CATALOG[item["id"]]["description"], rows) for item in planned if item.get("id") in CATALOG]
    if not plan:
        plan = [_step(sid, CATALOG[sid]["description"], rows) for sid in DEFAULT_PLAN]
    return plan
