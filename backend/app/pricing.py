"""Best-effort LLM cost estimation for the approval gate.

The heavy compute (PCA/k-means) is local numpy → genuinely $0. The real spend is
the LLM generation calls (the planner + the summary cards). This turns the active
provider/model + the number of generation calls in the plan into an estimated $
figure — labeled "est." because token counts are approximated pre-flight, and a
local/unconfigured run is honestly $0.

Prices are $ per 1M tokens (input, output) — best-effort as of early 2026 and a
moving target. Override for ANY model with `LLM_PRICE_IN` / `LLM_PRICE_OUT`
(dollars per 1M tokens).
"""

from __future__ import annotations

import os

# Matched by model-id prefix; most specific first. (input $/1M, output $/1M)
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),   # from the Claude pricing table
    "claude-opus": (5.0, 25.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (1.0, 5.0),
    "gpt-5": (1.25, 10.0),            # approximate
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.5, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),  # approximate
    "gemini-2.5-pro": (1.25, 10.0),    # approximate
    "gemini": (0.30, 2.50),            # flash-class default
}
_DEFAULT = (1.0, 5.0)  # unknown model → conservative

# Rough tokens per structured-output generation call (system + prompt + schema in; one card out).
_IN_TOKENS, _OUT_TOKENS = 700, 280


def _price(model: str | None) -> tuple[float, float]:
    env_in, env_out = os.getenv("LLM_PRICE_IN"), os.getenv("LLM_PRICE_OUT")
    if env_in and env_out:
        return float(env_in), float(env_out)
    if model:
        for prefix, price in _PRICES.items():
            if model.startswith(prefix):
                return price
    return _DEFAULT


def estimate_run_cost(model: str | None, n_calls: int) -> float:
    """Estimated dollars for `n_calls` LLM generation calls on `model`."""
    p_in, p_out = _price(model)
    return max(0, n_calls) * (_IN_TOKENS / 1e6 * p_in + _OUT_TOKENS / 1e6 * p_out)


def format_cost(dollars: float, model: str | None) -> str:
    """Short label for the gate's cost cell. No model (offline) → genuinely $0 local."""
    if not model:
        return "$0.00 (local)"
    return f"~${dollars:.4f}" if dollars < 0.1 else f"~${dollars:.2f}"
