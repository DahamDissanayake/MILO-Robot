"""Thin async wrapper over the official MCP SDK's Streamable HTTP client,
scoped to one robot's movement/face/speech/IMU MCP server for the lifetime
of one cognition session (see session.py's CognitionSessionFactory).
"""
from __future__ import annotations

import contextlib
import json
from typing import Any


def _to_ollama_tool(tool: Any) -> dict:
    """``tool``: an mcp.types.Tool (name, description, inputSchema)."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


def _tool_result_to_dict(result: Any) -> dict:
    """``result``: an mcp.types.CallToolResult. Structured content wins;
    falls back to parsing the first text block as JSON, else wraps it as
    {"text": ...} so a caller always gets a dict back."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []):
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return {"text": block.text}
    return {}


class MiloMcpClient:
    """One connection to a single robot's bridge MCP server, held open for
    the lifetime of one RobotCognitionSession."""

    def __init__(self, base_url: str, token: str, peer_id: str):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._peer_id = peer_id
        self._stack: contextlib.AsyncExitStack | None = None
        self._session: Any = None

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        self._stack = contextlib.AsyncExitStack()
        headers = {"Authorization": f"Bearer {self._token}", "X-Milo-Peer": self._peer_id}
        read, write, _ = await self._stack.enter_async_context(
            streamablehttp_client(f"{self._base_url}/mcp", headers=headers)
        )
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def list_tools(self) -> list[dict]:
        result = await self._session.list_tools()
        return [_to_ollama_tool(tool) for tool in result.tools]

    async def call_tool(self, tool_name: str, **arguments: Any) -> dict:
        # Parameter deliberately named tool_name, not name -- several tools
        # (run_pose, set_mode, set_face) take a kwarg literally called
        # `name`, which would collide with a same-named parameter here
        # (e.g. call_tool("set_face", name="excited") would raise
        # "got multiple values for argument 'name'" otherwise).
        result = await self._session.call_tool(tool_name, arguments)
        return _tool_result_to_dict(result)
