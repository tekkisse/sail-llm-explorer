"""Connects to MCP servers and exposes their tools to the LLM as OpenAI-style function tools."""
from __future__ import annotations

import json
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

_TOKEN = re.compile(r"[a-z]+")


@dataclass
class ServerSpec:
    prefix: str                       # short name used in tool names, e.g. "meta"
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    allow: list[str] = field(default_factory=list)   # exact tool names; empty = all not denied
    deny_words: list[str] = field(default_factory=list)  # tool-name words that mark write tools

    def permits(self, tool_name: str) -> bool:
        if self.allow:
            return tool_name in self.allow
        words = _TOKEN.findall(tool_name.lower())
        return not any(w == d or w.startswith(d) for w in words for d in self.deny_words)


class ToolError(RuntimeError):
    pass


class MCPToolset:
    """Async context manager holding open sessions to several MCP servers."""

    def __init__(self, specs: list[ServerSpec]):
        self.specs = specs
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, tuple[str, str]] = {}   # exposed name -> (prefix, tool name)
        self.openai_tools: list[dict[str, Any]] = []

    async def __aenter__(self) -> "MCPToolset":
        for spec in self.specs:
            client = create_mcp_http_client(headers=spec.headers)
            await self._stack.enter_async_context(client)
            read, write, _ = await self._stack.enter_async_context(streamable_http_client(spec.url, http_client=client))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._sessions[spec.prefix] = session
            listed = await session.list_tools()
            for tool in listed.tools:
                if not spec.permits(tool.name):
                    continue
                exposed = f"{spec.prefix}__{tool.name}"[:64]
                self._tools[exposed] = (spec.prefix, tool.name)
                self.openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": exposed,
                        "description": (tool.description or "")[:1024],
                        "parameters": tool.inputSchema or {"type": "object", "properties": {}},
                    },
                })
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    def has(self, exposed_name: str) -> bool:
        return exposed_name in self._tools

    async def call(self, exposed_name: str, arguments: dict[str, Any]) -> Any:
        if exposed_name not in self._tools:
            raise ToolError(f"Unknown or disallowed tool {exposed_name}")
        prefix, name = self._tools[exposed_name]
        result = await self._sessions[prefix].call_tool(name, arguments)
        if result.structuredContent is not None:
            payload: Any = result.structuredContent
            # FastMCP wraps non-object returns as {"result": ...}
            if isinstance(payload, dict) and set(payload) == {"result"}:
                payload = payload["result"]
        else:
            texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
            text = "\n".join(texts)
            try:
                payload = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                payload = text
        if result.isError:
            raise ToolError(payload if isinstance(payload, str) else json.dumps(payload))
        return payload
