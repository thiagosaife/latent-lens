"""In-process MCP client session to the analysis server (mcp_tools).

Uses the MCP SDK's in-memory transport: a real client↔server session over the
MCP protocol (ListTools / CallTool), but the server runs in this process so the
tools share the live dataset registry. Opened at app startup, closed at
shutdown. The analysis genuinely flows through MCP tool calls.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session as _connect

from .mcp_tools import mcp as _server

log = logging.getLogger("latentlens.mcp")

_stack: AsyncExitStack | None = None
_session: ClientSession | None = None


async def startup() -> None:
    global _stack, _session
    _stack = AsyncExitStack()
    _session = await _stack.enter_async_context(_connect(_server._mcp_server))
    tools = await _session.list_tools()
    log.info(json.dumps({"msg": "mcp.ready", "tools": [t.name for t in tools.tools]}))


async def shutdown() -> None:
    global _stack, _session
    if _stack is not None:
        await _stack.aclose()
    _stack = None
    _session = None


async def call(name: str, **args: Any) -> dict:
    """Invoke an MCP tool and return its structured result as a dict."""
    if _session is None:
        raise RuntimeError("MCP session not started")
    res = await _session.call_tool(name, args)
    data = res.structuredContent
    if data is None and res.content:
        data = json.loads(res.content[0].text)
    if isinstance(data, dict) and set(data.keys()) == {"result"}:
        data = data["result"]  # FastMCP wraps non-object returns under "result"
    return data or {}
