"""SAIL Data Explorer web UI (Chainlit). Runs inside the TRE.

    chainlit run ui/app.py --host 0.0.0.0 --port 8000

Authentication (UI_AUTH_MODE):
  header   - behind an authenticating proxy (oauth2-proxy, Keycloak gatekeeper); reads the
             user's email from UI_AUTH_HEADER (default X-Auth-Request-Email). Recommended.
  oauth    - Chainlit's built-in OAuth (configure OAUTH_* env vars per Chainlit docs).
  password - demo only: UI_DEMO_USERS="alice@example.org:password,bob@example.org:password".
"""
from __future__ import annotations

import json
import os
from typing import Any

import chainlit as cl
import plotly.graph_objects as go

from sailx.agent.agent import Agent, AgentResult
from sailx.identity import Caller

AUTH_MODE = os.environ.get("UI_AUTH_MODE", "password")
AUTH_HEADER = os.environ.get("UI_AUTH_HEADER", "X-Auth-Request-Email")
ALLOWED_DOMAINS = [d.strip().lower() for d in os.environ.get("UI_ALLOWED_EMAIL_DOMAINS", "").split(",") if d.strip()]
MAX_TABLE_ROWS = 50

TOOL_LABELS = {
    "meta__search_metadata": "Searching the metadata catalogue",
    "meta__get_table_details": "Reading table metadata and profile",
    "meta__get_column_profile": "Checking a column profile",
    "meta__list_datasets": "Listing datasets",
    "pheno__search_phenotypes": "Searching the Phenotype Library",
    "pheno__get_phenotype": "Reading a phenotype definition",
    "pheno__get_phenotype_codes": "Fetching phenotype codes",
    "sql__describe_project_schema": "Checking project tables",
    "sql__validate_sql": "Validating SQL",
    "sql__run_query": "Running the query",
    "create_chart": "Drawing a chart",
}

_agent: Agent | None = None


def agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent


def _allowed(email: str | None) -> bool:
    if not email:
        return False
    return not ALLOWED_DOMAINS or email.lower().rsplit("@", 1)[-1] in ALLOWED_DOMAINS


# Authentication -------------------------------------------------------------------
if AUTH_MODE == "header":
    @cl.header_auth_callback
    async def header_auth(headers) -> cl.User | None:
        email = headers.get(AUTH_HEADER)
        return cl.User(identifier=email.lower(), metadata={"provider": "header"}) if _allowed(email) else None

elif AUTH_MODE == "oauth":
    @cl.oauth_callback
    async def oauth(provider_id: str, token: str, raw_user_data: dict, default_user: cl.User,
                    id_token: str | None = None) -> cl.User | None:
        email = raw_user_data.get("email") or default_user.identifier
        return cl.User(identifier=email.lower(), metadata={"provider": provider_id}) if _allowed(email) else None

else:
    _demo_users = dict(u.split(":", 1) for u in os.environ.get("UI_DEMO_USERS", "").split(",") if ":" in u)

    @cl.password_auth_callback
    async def password_auth(username: str, password: str) -> cl.User | None:
        if _demo_users.get(username.lower()) == password and _allowed(username):
            return cl.User(identifier=username.lower(), metadata={"provider": "demo"})
        return None


# Session --------------------------------------------------------------------------
@cl.on_chat_start
async def start() -> None:
    user = cl.user_session.get("user")
    email = user.identifier if user else ""
    projects = agent().registry.for_user(email)
    project_id = None
    if len(projects) == 1:
        project_id = projects[0].id
    elif len(projects) > 1:
        choice = await cl.AskActionMessage(
            content="Which approved project are you working in?",
            actions=[cl.Action(name="project", payload={"id": p.id}, label=p.title) for p in projects],
            timeout=600,
        ).send()
        project_id = choice["payload"]["id"] if choice else None
    cl.user_session.set("caller", Caller(user=email, project=project_id or "", channel="web"))
    cl.user_session.set("history", [])

    if project_id:
        title = next(p.title for p in projects if p.id == project_id)
        intro = (f"You're working in **{title}**. Ask what's in the data (\"Is ethnicity well recorded?\") "
                 "or ask a data question (\"How does type 2 diabetes vary by deprivation?\"). "
                 "Every query is shown to you before it runs.")
    else:
        intro = ("You're not a member of an approved project, so I can answer questions about what's in "
                 "the datasets but can't query the data.")
    await cl.Message(content=intro).send()


def _event_handler():
    steps: list[cl.Step] = []

    async def on_event(kind: str, data: dict[str, Any]) -> None:
        if kind == "tool_start":
            step = cl.Step(name=TOOL_LABELS.get(data["name"], data["name"]), type="tool")
            step.input = json.dumps(data["arguments"], indent=2)
            await step.send()
            steps.append(step)
        elif kind == "tool_end" and steps:
            step = steps.pop()
            result = data["result"]
            text = json.dumps(result, indent=2, default=str) if not isinstance(result, str) else result
            step.output = text[:4000]
            step.is_error = not data["ok"]
            await step.update()
        elif kind == "query_result":
            await _show_query(data)

    return on_event


def _markdown_table(columns: list[str], rows: list[list[Any]]) -> str:
    head = "| " + " | ".join(columns) + " |\n| " + " | ".join("---" for _ in columns) + " |\n"
    body = "\n".join("| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows[:MAX_TABLE_ROWS])
    more = f"\n\n_{len(rows) - MAX_TABLE_ROWS} more rows not shown._" if len(rows) > MAX_TABLE_ROWS else ""
    return head + body + more


async def _show_query(q: dict[str, Any]) -> None:
    notes = "".join(f"\n> {n}" for n in q.get("notes") or [])
    await cl.Message(
        content=f"**Result** ({q['row_count']} rows) — {q.get('purpose') or ''}\n\n"
                f"{_markdown_table(q['columns'], q['rows'])}\n{notes}",
        elements=[cl.Text(name="SQL", content=q["sql_executed"], language="sql", display="inline")],
    ).send()


async def _ask_approval(result: AgentResult) -> tuple[bool, str | None]:
    await cl.Message(content=f"I'd like to run this query{(' to find ' + result.pending_purpose) if result.pending_purpose else ''}:\n\n"
                             f"```sql\n{result.pending_sql}\n```").send()
    choice = await cl.AskActionMessage(
        content="It runs read-only in your project, and small numbers are suppressed before you see them.",
        actions=[cl.Action(name="approve", payload={"v": True}, label="Run query"),
                 cl.Action(name="decline", payload={"v": False}, label="Don't run")],
        timeout=900,
    ).send()
    return bool(choice and choice["payload"].get("v")), None


async def _render(result: AgentResult) -> None:
    elements = []
    for i, fig in enumerate(result.charts):
        spec = {k: v for k, v in fig.items() if k != "_meta"}
        elements.append(cl.Plotly(name=f"chart_{i + 1}", figure=go.Figure(spec), display="inline", size="large"))
    provenance = ""
    if result.phenotypes:
        provenance = "\n\n**Phenotypes used:** " + ", ".join(
            f"{p['phenotype_id']} v{p['version_id']}" for p in result.phenotypes)
    await cl.Message(content=(result.answer or "") + provenance, elements=elements).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    caller: Caller = cl.user_session.get("caller")
    history = cl.user_session.get("history") or []
    a = agent()
    on_event = _event_handler()

    result = await a.run(message.content, caller, history=history, on_event=on_event)
    while result.status == "awaiting_approval":
        approved, edited = await _ask_approval(result)
        result = await a.resume(result.state, caller, approved=approved, sql_override=edited, on_event=on_event)

    await _render(result)
    # Keep the conversation (bounded) for follow-up questions
    cl.user_session.set("history", a.history_from(result)[-20:])
