"""HTTP API for non-web channels (n8n / Slack). Run with: sailx-agent-api

Slack is outside the TRE, so requests from it run in discovery mode: SQL tools are not
offered unless the project sets allow_slack_queries. Results never contain row data.
"""
from __future__ import annotations

import hmac
import os
from functools import lru_cache
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ..audit import AuditLog
from ..config import AgentSettings
from ..identity import Caller
from .agent import Agent

app = FastAPI(title="SAIL Data Explorer agent API", version="0.1.0")


@lru_cache(maxsize=1)
def agent() -> Agent:
    return Agent()


@lru_cache(maxsize=1)
def audit() -> AuditLog:
    return AuditLog(os.environ.get("AUDIT_LOG", "-"))


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = AgentSettings().api_key
    if not expected:
        raise HTTPException(503, "AGENT_API_KEY is not configured")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "Invalid API key")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    user_email: str
    project: str | None = None
    channel: str = "slack"


class AskResponse(BaseModel):
    answer: str
    status: str
    web_url: str
    used_tables: list[str] = []
    phenotypes: list[dict[str, Any]] = []


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/ask", response_model=AskResponse, dependencies=[Depends(require_api_key)])
async def ask(req: AskRequest) -> AskResponse:
    a = agent()
    user = req.user_email.lower()
    projects = a.registry.for_user(user)
    project = req.project
    if project and project not in {p.id for p in projects}:
        raise HTTPException(403, "User is not a member of that project")
    if project is None and len(projects) == 1:
        project = projects[0].id
    caller = Caller(user=user, project=project or "", channel=req.channel)
    audit().write("ask", user=user, project=project, channel=req.channel, question=req.question)
    result = await a.run(req.question, caller)
    answer = result.answer or ""
    if result.status == "awaiting_approval":
        answer = ("This needs a data query, which must be approved in the web explorer: "
                  f"{a.settings.web_ui_url}")
    tables: list[str] = []
    for call in result.tool_calls:
        if call["name"].endswith("get_table_details") and call["arguments"].get("table"):
            tables.append(call["arguments"]["table"])
    return AskResponse(answer=answer, status=result.status, web_url=a.settings.web_ui_url,
                       used_tables=sorted(set(tables)), phenotypes=result.phenotypes)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=os.environ.get("API_HOST", "127.0.0.1"), port=int(os.environ.get("API_PORT", "8104")))


if __name__ == "__main__":
    main()
