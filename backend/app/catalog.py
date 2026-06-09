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


def propose_plan() -> list[dict]:
    """Build the plan_proposed payload. Omit `estimate` when absent (the frontend
    Zod schema treats it as optional → must be undefined, not null)."""
    plan: list[dict] = []
    for sid in DEFAULT_PLAN:
        c = CATALOG[sid]
        step = {"id": sid, "title": c["title"], "description": c["description"], "needsApproval": bool(c.get("needsApproval"))}
        if c.get("estimate"):
            step["estimate"] = c["estimate"]
        plan.append(step)
    return plan
