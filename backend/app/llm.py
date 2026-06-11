"""The generative-UI moment: the LLM writes the summary_card props under a
constrained JSON schema (structured outputs) from the real pipeline stats. This
is the "constrained generation surface" made real — the model can only emit props
that match the schema, and the frontend Pattern Registry re-validates as the
final gate.

Provider-agnostic: the actual call goes through `providers.get_provider()`, which
selects Anthropic / OpenAI / Gemini / any OpenAI-compatible endpoint from env
(see providers.py). Graceful by design — if no provider is configured (or the
call fails), a deterministic fallback keeps the pipeline running offline. Set any
one of ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY to light up real
generation.
"""

from __future__ import annotations

import json
import logging

from .providers import get_provider

log = logging.getLogger("latentlens.llm")

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


def _card(kind: str, system: str, prompt: str, fallback) -> dict:
    """Generate one summary_card's props via the configured LLM (structured output
    against SUMMARY_SCHEMA), or fall back to a deterministic template. Same graceful
    pattern everywhere — the model writes the prose; the registry re-validates."""
    try:
        provider = get_provider()
        if provider is None:
            raise RuntimeError("no LLM provider configured")
        props = provider.generate_json(system, prompt, SUMMARY_SCHEMA)
        log.info(json.dumps({"msg": f"llm.{kind}", "source": provider.name, "model": provider.model}))
        return props
    except Exception as e:  # no provider, missing key, network, schema, parse — all non-fatal
        log.warning(json.dumps({"msg": f"llm.{kind}", "source": "fallback", "reason": str(e)[:160]}))
        return fallback()


def generate_profile_summary(profile: dict) -> dict:
    """summary_card props describing the profiling results."""
    flagged = profile.get("flagged_high_missing", [])[:4]
    system = (
        "You are a data-analysis agent writing a UI card after PROFILING a dataset. "
        "Concise and specific. Body under ~35 words. 2-4 crisp bullets. tone 'warning' if "
        "missingness or duplicates are notable, else 'positive'."
    )
    prompt = (
        f"{profile.get('rows', 0):,} rows; {profile.get('numeric', 0)} numeric + {profile.get('categorical', 0)} categorical columns; "
        f"{profile.get('missing_fraction', 0) * 100:.1f}% of cells missing; {profile.get('duplicates', 0):,} duplicate rows; "
        f"columns over 30% missing: {flagged or 'none'}."
    )
    return _card("profile", system, prompt, lambda: _fallback_profile(profile, flagged))


def generate_clean_summary(clean: dict) -> dict:
    """summary_card props describing the cleaning actions."""
    miss, dup, num = clean.get("missing_cells", 0), clean.get("duplicates", 0), clean.get("numeric", 0)
    system = (
        "You are a data-cleaning agent writing a UI card after CLEANING a dataset. "
        "Concise. Body under ~30 words. 2-3 bullets stating what was done. tone 'neutral'."
    )
    prompt = f"Imputed {miss:,} missing cells; removed {dup:,} duplicate rows; standardized {num} numeric columns."
    return _card("clean", system, prompt, lambda: _fallback_clean(miss, dup, num))


def generate_cluster_summary(sizes: list[int]) -> dict:
    """summary_card props describing the discovered segments (the cluster step)."""
    top = sorted(sizes, reverse=True)[:3]
    system = (
        "You are a segmentation agent writing a UI card after k-means CLUSTERING a 2D embedding. "
        "Concise. Body under ~30 words. 2-3 bullets on the segment sizes. tone 'neutral'."
    )
    prompt = f"k-means found {len(sizes)} dense segments; sizes {sizes} (top three {top})."
    return _card("cluster", system, prompt, lambda: _fallback_cluster(sizes, top))


def generate_segment_summary(goal: str, profile: dict, sizes: list[int]) -> dict:
    """summary_card props for the final segment summary (the summarize step)."""
    total = sum(sizes) or 1
    biggest, smallest = (max(sizes), min(sizes)) if sizes else (0, 0)
    system = (
        "You are a data-analysis agent writing a UI summary card about customer segments. "
        "Write a concise, specific card. Body under ~40 words. 3-4 crisp bullet findings. "
        "Use tone 'positive' when the segment structure is clean."
    )
    prompt = (
        f"User goal: {goal!r}\n"
        f"Dataset: {profile.get('rows', 0):,} rows, {profile.get('numeric', 0)} numeric + "
        f"{profile.get('categorical', 0)} categorical columns, "
        f"{profile.get('missing_fraction', 0) * 100:.1f}% missing cells before cleaning.\n"
        f"Clustering found {len(sizes)} segments, sizes {sizes} "
        f"(largest {biggest / total * 100:.0f}% of rows, smallest {smallest / total * 100:.0f}%)."
    )
    return _card("summary", system, prompt, lambda: _fallback_summary(profile, sizes))


def generate_explain_summary(
    goal: str, count: int, composition: list[dict] | None = None, features: list[dict] | None = None
) -> dict:
    """summary_card props for the lasso → 'explain these points' follow-up, grounded
    in the selection's REAL cluster composition (`composition`: [{label, count, share}])
    AND the features that most distinguish it from the population (`features`:
    [{feature, z, direction}] — z-scores of the selected rows' means)."""
    comp = composition or []
    feats = features or []
    comp_txt = ", ".join(f"cluster {c['label']} {c['share'] * 100:.0f}%" for c in comp) or "composition unknown"
    feat_txt = ", ".join(f"{f['feature']} {f['z']:+.1f}σ ({f['direction']} average)" for f in feats[:4]) or "feature deltas unavailable"
    system = (
        "You are a data-analysis agent explaining what a lasso-selected region of a 2D customer "
        "embedding has in common. Ground the explanation in the real DISTINGUISHING FEATURES "
        "(z-scores vs the population) and the CLUSTER COMPOSITION. Concise; body under ~35 words; "
        "2-3 bullets; tone 'neutral'."
    )
    prompt = (
        f"The user lassoed {count:,} points and asked: {goal!r}.\n"
        f"Features that most distinguish this region (z-score vs population): {feat_txt}.\n"
        f"Selection cluster composition (descending): {comp_txt}.\n"
        "Explain what this region likely shares — name the 1-2 features that stand out and their "
        "direction, and reference the dominant cluster(s)."
    )
    return _card("explain", system, prompt, lambda: _fallback_explain(count, comp, feats))


# ── Deterministic fallbacks (used offline / on any LLM failure) ──────────────

def _fallback_profile(profile: dict, flagged: list[str]) -> dict:
    dup = profile.get("duplicates", 0)
    miss_by = profile.get("missing_by_col", {})
    bullets = [f"{c} — {int(miss_by.get(c, 0) * 100)}% missing" for c in flagged]
    bullets.append(f"{dup:,} duplicate rows" if dup else "No duplicate rows")
    return {
        "title": "Profiling complete",
        "body": (
            f"{profile.get('numeric', 0)} numeric and {profile.get('categorical', 0)} categorical columns. "
            f"{len(flagged)} columns exceed 30% missingness and are flagged for cleaning."
        ),
        "bullets": bullets,
        "tone": "positive",
    }


def _fallback_clean(miss: int, dup: int, num: int) -> dict:
    return {
        "title": "Cleaning complete",
        "body": f"Imputed {miss:,} missing cells, removed {dup} duplicate rows, and standardized {num} numeric columns.",
        "bullets": [f"{miss:,} cells imputed", f"{dup} duplicate rows removed", f"{num} columns standardized"],
        "tone": "neutral",
    }


def _fallback_cluster(sizes: list[int], top: list[int]) -> dict:
    return {
        "title": f"{len(sizes)} segments discovered" if sizes else "Clustering complete",
        "body": f"k-means over the 2D embedding found {len(sizes)} dense segments.",
        "bullets": [f"Segment {chr(65 + i)}: {s:,} rows" for i, s in enumerate(top)],
        "tone": "neutral",
    }


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


def _fallback_explain(count: int = 0, composition: list[dict] | None = None, features: list[dict] | None = None) -> dict:
    comp = composition or []
    feats = features or []
    if feats:
        phrases = [f"{f['feature']} ({f['z']:+.1f}σ {f['direction']} average)" for f in feats[:2]]
        tail = f", concentrating in cluster {comp[0]['label']} ({comp[0]['share'] * 100:.0f}%)" if comp else ""
        return {
            "title": "What distinguishes these points",
            "body": f"These {count:,} points stand out on {' and '.join(phrases)}{tail} — a coherent sub-population.",
            "bullets": [f"{f['feature']}: {f['z']:+.1f}σ {f['direction']} the population mean" for f in feats[:4]],
            "tone": "neutral",
        }
    if comp:
        top = comp[0]
        return {
            "title": "Selected region",
            "body": (
                f"These {count:,} points concentrate in cluster {top['label']} "
                f"({top['share'] * 100:.0f}% of the selection), spanning {len(comp)} cluster(s) — "
                "a coherent sub-population on the embedding."
            ),
            "bullets": [f"Cluster {c['label']}: {c['count']:,} pts ({c['share'] * 100:.0f}%)" for c in comp[:3]],
            "tone": "neutral",
        }
    return {
        "title": "Selected region",
        "body": "These points form a coherent sub-cluster on the embedding.",
        "bullets": [f"{count:,} points selected"] if count else ["A region of the embedding"],
        "tone": "neutral",
    }


# ── Planner: decompose the goal into an ordered plan ────────────────────────

def generate_plan(goal: str, catalog_meta: list[dict]) -> list[dict]:
    """Choose + order analysis steps for the goal → [{id, reason}]. The configured
    LLM when a key is set (constrained to the catalog via an enum schema); else a
    goal-aware heuristic that still adapts the plan offline."""
    try:
        steps = _llm_plan(goal, catalog_meta)
        provider = get_provider()
        log.info(json.dumps({"msg": "llm.plan", "source": provider.name, "model": provider.model, "steps": [s["id"] for s in steps]}))
        return steps
    except Exception as e:
        steps = _heuristic_plan(goal, catalog_meta)
        log.warning(json.dumps({"msg": "llm.plan", "source": "heuristic", "steps": [s["id"] for s in steps], "reason": str(e)[:160]}))
        return steps


def _llm_plan(goal: str, catalog_meta: list[dict]) -> list[dict]:
    provider = get_provider()
    if provider is None:
        raise RuntimeError("no LLM provider configured")
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
    system = (
        "You are a data-analysis planner. Choose and ORDER analysis steps into a plan for "
        "the user's goal. Use only the given step ids. Respect dependencies: profile and "
        "clean before reduce; reduce before cluster; summarize last. One-line reason per step."
    )
    prompt = f"Goal: {goal!r}\n\nAvailable steps:\n{catalog_txt}"
    data = provider.generate_json(system, prompt, schema)
    # Post-filter is the enum guarantee for providers that don't enforce enums natively.
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
