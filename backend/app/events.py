"""SSE framing for the AgentEvent protocol.

The event dicts are built inline by the orchestrator with the exact camelCase
keys the frontend's Zod `agentEventSchema` validates. This keeps one source of
truth for the wire shape (the frontend) rather than duplicating a schema here.
"""

import json
from typing import Any


def sse(event: dict[str, Any]) -> str:
    """Serialize one event as a Server-Sent Events `data:` frame.

    Compact separators keep frames small and byte-match the Node mock's output.
    (Whitespace is irrelevant to the frontend, which JSON-parses the payload.)
    """
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
