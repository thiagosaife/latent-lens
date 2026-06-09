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
        "needsApproval": True,
        "estimate": {"rows": 1_000_000, "seconds": 42, "cost": "$0.00 (local)"},
    },
    "cluster": {"title": "Cluster (k-means)", "description": "Discover dense segments", "delegate": "segmentation-agent"},
    "summarize": {"title": "Summarize segments", "description": "Name & describe each segment"},
}


def catalog_meta() -> list[dict]:
    """Step ids + titles + descriptions for the planner to choose from."""
    return [{"id": sid, "title": c["title"], "description": c["description"]} for sid, c in CATALOG.items()]


def _step(sid: str, description: str) -> dict:
    # Omit `estimate` when absent (the frontend Zod schema treats it as optional
    # → must be undefined, not null).
    c = CATALOG[sid]
    step = {"id": sid, "title": c["title"], "description": description, "needsApproval": bool(c.get("needsApproval"))}
    if c.get("estimate"):
        step["estimate"] = c["estimate"]
    return step


def build_plan(planned: list[dict]) -> list[dict]:
    """Turn the planner's [{id, reason}] into plan_proposed steps. The reason
    becomes the step description — the agent's rationale, shown in the editor."""
    plan = [_step(item["id"], item.get("reason") or CATALOG[item["id"]]["description"]) for item in planned if item.get("id") in CATALOG]
    if not plan:
        plan = [_step(sid, CATALOG[sid]["description"]) for sid in DEFAULT_PLAN]
    return plan
