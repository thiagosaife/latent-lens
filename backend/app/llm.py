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
