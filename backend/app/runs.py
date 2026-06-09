"""Live-run registry + interrupt gates.

Each run holds asyncio Futures the resume POSTs resolve — the same held-stream
pattern as the mock, and the shape a LangGraph `interrupt()` / `Command(resume=)`
would take. Single uvicorn worker ⇒ one event loop ⇒ the SSE generator and the
resume handlers share this state safely.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class RunState:
    run_id: str
    trace_id: str
    plan_gate: asyncio.Future  # resolves to list[str] of step ids (the edited plan)
    decision_gates: dict[str, asyncio.Future] = field(default_factory=dict)  # stepId -> 'approve'|'skip'|'cancel'
    cancelled: bool = False


RUNS: dict[str, RunState] = {}
