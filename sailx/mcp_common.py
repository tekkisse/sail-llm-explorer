"""Shared helpers for the MCP servers."""
from __future__ import annotations

import os

from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


def make_server(name: str, instructions: str, default_port: int) -> FastMCP:
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", default_port))
    server = FastMCP(name, instructions=instructions, host=host, port=port,
                     stateless_http=True, json_response=True)

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": name})

    return server


def bearer_token(ctx: Context) -> str | None:
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        return None
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else None
