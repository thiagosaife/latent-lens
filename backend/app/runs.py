"""Live-run registry + interrupt gates + a resumable event buffer.

Each run holds asyncio Futures the resume POSTs resolve (the held-stream pattern,
the shape a LangGraph `interrupt()` / `Command(resume=)` takes). It ALSO owns an
append-only buffer of the SSE frames it has emitted, so a dropped connection can
reconnect and replay what it missed — execution is decoupled from any one HTTP
response. A background task drives the graph and `emit()`s frames; HTTP responses
are just `subscribe()`rs that replay-then-follow.

Single uvicorn worker ⇒ one event loop ⇒ the producer task, the subscribers, and
the resume handlers all share this state safely without locks.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .events import sse

# Idle gap after which a subscriber emits an SSE heartbeat comment, so a held
# gate wait doesn't get reaped by an intermediary proxy or the browser. Low in
# tests via env so the heartbeat path is observable without a 15s wait.
HEARTBEAT_S = float(os.environ.get("LATENTLENS_HEARTBEAT_S", "15"))
# Keep a finished run's buffer around this long so a client that dropped right
# before the end can still reconnect and replay the tail.
RESUME_TTL_S = float(os.environ.get("LATENTLENS_RESUME_TTL_S", "60"))


@dataclass
class RunState:
    run_id: str
    trace_id: str
    plan_gate: asyncio.Future  # resolves to list[str] of step ids (the edited plan)
    decision_gates: dict[str, asyncio.Future] = field(default_factory=dict)  # stepId -> 'approve'|'skip'|'cancel'
    cancelled: bool = False

    # ── resumable stream ────────────────────────────────────────────────────
    buffer: list[str] = field(default_factory=list)  # emitted SSE frames, each prefixed with its `id:` line
    seq: int = 0  # last assigned sequence number; buffer[k] carries seq k+1
    done: bool = False  # producer finished (no more frames will be emitted)
    _waiters: set[asyncio.Event] = field(default_factory=set)  # per-subscriber wakeups

    def emit(self, event: dict) -> None:
        """Append one AgentEvent as an SSE frame and wake any subscribers.

        The `id:` line makes the sequence number resumable (a reconnecting client
        asks for everything `after` the last id it saw)."""
        self.seq += 1
        self.buffer.append(f"id: {self.seq}\n{sse(event)}")
        self._wake()

    def finish(self) -> None:
        """Mark the run complete so subscribers can drain and close."""
        self.done = True
        self._wake()

    def _wake(self) -> None:
        for w in self._waiters:
            w.set()

    async def subscribe(self, after: int = 0) -> AsyncIterator[str]:
        """Yield buffered frames with seq > `after`, then follow live until done.

        Emits a heartbeat comment when the buffer stays idle (e.g. while the run
        is held at a gate). Multiple subscribers can follow the same run; each
        tracks its own position, so a reconnect never duplicates or skips frames."""
        idx = max(0, min(after, len(self.buffer)))
        wake = asyncio.Event()
        self._waiters.add(wake)
        try:
            while True:
                wake.clear()  # clear BEFORE reading len() so no emit is lost between drain and wait
                while idx < len(self.buffer):
                    yield self.buffer[idx]
                    idx += 1
                if self.done:
                    return
                try:
                    await asyncio.wait_for(wake.wait(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": hb\n\n"  # keep the idle (gate-held) connection alive
        finally:
            self._waiters.discard(wake)


RUNS: dict[str, RunState] = {}
