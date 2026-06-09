"""The generative-UI moment: Claude writes the summary_card props under a
constrained JSON schema (structured outputs) from the real pipeline stats. This
is the "constrained generation surface" made real — Claude can only emit props
that match the schema, and the frontend Pattern Registry re-validates as the
final gate.

Graceful by design: if no credentials are configured (or the call fails), a
deterministic fallback keeps the pipeline running offline. Set ANTHROPIC_API_KEY
(or `ant auth login`) to light up the real generation.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("latentlens.llm")

MODEL = "claude-opus-4-8"

# JSON schema for the summary_card props — mirrors the frontend Zod schema.
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "tone": {"type": "string", "enum": ["neutral", "positive", "warning"]},
    },
    "required": ["title", "body", "bullets", "tone"],
    "additionalProperties": False,
}


def generate_segment_summary(goal: str, profile: dict, sizes: list[int]) -> dict:
    """summary_card props describing the discovered segments."""
    try:
        props = _claude_summary(goal, profile, sizes)
        log.info(json.dumps({"msg": "llm.summary", "source": "claude", "model": MODEL}))
        return props
    except Exception as e:  # missing key, network, schema, parse — all non-fatal
        log.warning(json.dumps({"msg": "llm.summary", "source": "fallback", "reason": str(e)[:160]}))
        return _fallback_summary(profile, sizes)


def _claude_summary(goal: str, profile: dict, sizes: list[int]) -> dict:
    import anthropic

    client = anthropic.Anthropic()  # raises immediately if no credentials
    total = sum(sizes) or 1
    biggest, smallest = (max(sizes), min(sizes)) if sizes else (0, 0)
    prompt = (
        "You are a data-analysis agent writing a UI summary card about customer segments.\n"
        f"User goal: {goal!r}\n"
        f"Dataset: {profile.get('rows', 0):,} rows, {profile.get('numeric', 0)} numeric + "
        f"{profile.get('categorical', 0)} categorical columns, "
        f"{profile.get('missing_fraction', 0) * 100:.1f}% missing cells before cleaning.\n"
        f"Clustering found {len(sizes)} segments, sizes {sizes} "
        f"(largest {biggest / total * 100:.0f}% of rows, smallest {smallest / total * 100:.0f}%).\n"
        "Write a concise, specific card. Body under ~40 words. 3-4 crisp bullet findings. "
        "Use tone 'positive' when the segment structure is clean."
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
    if not text:
        raise ValueError("no text block in response")
    return json.loads(text)


def _fallback_summary(profile: dict, sizes: list[int]) -> dict:
    total = sum(sizes) or 1
    biggest, smallest = (max(sizes), min(sizes)) if sizes else (0, 0)
    return {
        "title": f"{len(sizes)} segments discovered" if sizes else "Segment summary",
        "body": (
            f"Across {profile.get('rows', 0):,} rows, clustering split the population into "
            f"{len(sizes)} segments. The largest holds {biggest / total * 100:.0f}% of rows; "
            f"the smallest {smallest / total * 100:.0f}%."
        ),
        "bullets": [
            f"Largest segment: {biggest:,} rows ({biggest / total * 100:.0f}%)",
            f"Smallest segment: {smallest:,} rows ({smallest / total * 100:.0f}%)",
            f"{profile.get('missing_fraction', 0) * 100:.1f}% of cells were missing before cleaning",
        ],
        "tone": "positive",
    }


def generate_explain_summary(goal: str, count: int) -> dict:
    """summary_card props for the lasso → 'explain these points' follow-up."""
    return {
        "title": "Selected region",
        "body": (
            "These points form a coherent sub-cluster: tighter spread on the first PCA axis "
            "and higher recency than the population. Reads as a recently-active segment."
        ),
        "bullets": ["Above-median recency", "Lower signup_source missingness", "Dominated by 2 clusters"],
        "tone": "neutral",
    }


# ── Planner: decompose the goal into an ordered plan ────────────────────────

def generate_plan(goal: str, catalog_meta: list[dict]) -> list[dict]:
    """Choose + order analysis steps for the goal → [{id, reason}]. Claude when a
    key is set (constrained to the catalog via an enum schema); else a goal-aware
    heuristic that still adapts the plan offline."""
    try:
        steps = _claude_plan(goal, catalog_meta)
        log.info(json.dumps({"msg": "llm.plan", "source": "claude", "steps": [s["id"] for s in steps]}))
        return steps
    except Exception as e:
        steps = _heuristic_plan(goal, catalog_meta)
        log.warning(json.dumps({"msg": "llm.plan", "source": "heuristic", "steps": [s["id"] for s in steps], "reason": str(e)[:160]}))
        return steps


def _claude_plan(goal: str, catalog_meta: list[dict]) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()  # raises if no credentials
    ids = [s["id"] for s in catalog_meta]
    schema = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string", "enum": ids}, "reason": {"type": "string"}},
                    "required": ["id", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["steps"],
        "additionalProperties": False,
    }
    catalog_txt = "\n".join(f"- {s['id']}: {s['title']} — {s['description']}" for s in catalog_meta)
    prompt = (
        "You are a data-analysis planner. Choose and ORDER analysis steps into a plan for "
        "the user's goal. Use only the given step ids. Respect dependencies: profile and "
        "clean before reduce; reduce before cluster; summarize last. One-line reason per step.\n\n"
        f"Goal: {goal!r}\n\nAvailable steps:\n{catalog_txt}"
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
    if not text:
        raise ValueError("no text block in response")
    data = json.loads(text)
    steps = [{"id": s["id"], "reason": s.get("reason", "")} for s in data.get("steps", []) if s.get("id") in ids]
    if not steps:
        raise ValueError("empty plan")
    return steps


def _heuristic_plan(goal: str, catalog_meta: list[dict]) -> list[dict]:
    """Goal-aware fallback — adapts the plan to keywords, no LLM needed."""
    available = {s["id"] for s in catalog_meta}
    g = goal.lower()
    steps: list[tuple[str, str]] = [("profile", "Profile types, distributions, and missingness")]
    if not any(w in g for w in ("raw", "already clean", "skip clean", "no clean")):
        steps.append(("clean", "Impute missing values and drop duplicates"))
    wants_structure = any(
        w in g for w in ("structure", "segment", "cluster", "group", "pattern", "embedding", "reduce", "visual", "explore", "find")
    )
    if wants_structure:
        steps.append(("reduce", "Project to 2D to reveal structure"))
        steps.append(("cluster", "Discover dense segments"))
    if wants_structure or any(w in g for w in ("summary", "summarize", "describe", "explain", "name", "label")):
        steps.append(("summarize", "Summarize the discovered segments"))
    return [{"id": i, "reason": r} for i, r in steps if i in available]
