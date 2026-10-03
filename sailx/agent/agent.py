"""The explorer agent: an LLM tool loop over the metadata, phenotype and SQL MCP servers.

Security properties:
  * The agent never holds database credentials. SQL tools are called with a short-lived
    token naming the user and project; the SQL MCP server re-checks membership.
  * Write tools on the metadata server are filtered out before the model sees them.
  * With REQUIRE_SQL_APPROVAL on, every query pauses for the researcher to approve it.
  * In Slack (outside the TRE) SQL tools are not offered unless the project allows it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from ..config import AgentSettings, IdentitySettings
from ..identity import Caller, mint_token
from ..projects import Project, ProjectRegistry
from .charts import CHART_TOOL, ChartError, build_chart
from .llm import LLM, OpenAICompatibleLLM
from .mcp_bridge import MCPToolset, ServerSpec, ToolError
from .prompts import build_system_prompt

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
RUN_QUERY = "sql__run_query"
MAX_TOOL_RESULT_CHARS = 12000
MAX_ROWS_TO_MODEL = 100


async def _noop(kind: str, data: dict[str, Any]) -> None:
    return None


@dataclass
class AgentResult:
    status: str                                   # "done" | "awaiting_approval" | "error"
    answer: str | None = None
    queries: list[dict[str, Any]] = field(default_factory=list)
    charts: list[dict[str, Any]] = field(default_factory=list)
    phenotypes: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    pending_sql: str | None = None
    pending_purpose: str | None = None
    state: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Agent:
    def __init__(self, settings: AgentSettings | None = None, identity: IdentitySettings | None = None,
                 llm: LLM | None = None, registry: ProjectRegistry | None = None):
        self.settings = settings or AgentSettings()
        self.identity = identity or IdentitySettings()
        self.llm = llm or OpenAICompatibleLLM(self.settings)
        self.registry = registry or ProjectRegistry.load(self.settings.projects_file)

    # Setup ------------------------------------------------------------------------
    def _project(self, caller: Caller) -> Project | None:
        if not caller.project:
            return None
        return self.registry.authorise(caller.user, caller.project)

    def _queries_enabled(self, caller: Caller, project: Project | None) -> bool:
        if project is None:
            return False
        return caller.channel != "slack" or project.allow_slack_queries

    def _specs(self, caller: Caller, project: Project | None) -> list[ServerSpec]:
        s = self.settings
        meta_headers = {"Authorization": f"Bearer {s.metadata_mcp_token}"} if s.metadata_mcp_token else {}
        specs = [
            ServerSpec("meta", s.metadata_mcp_url, meta_headers, s.metadata_tool_allowlist, s.metadata_tool_denylist),
            ServerSpec("pheno", s.phenotype_mcp_url),
        ]
        if self._queries_enabled(caller, project):
            token = mint_token(caller, self.identity)
            specs.append(ServerSpec("sql", s.sql_mcp_url, {"Authorization": f"Bearer {token}"},
                                    allow=["describe_project_schema", "validate_sql", "run_query"]))
        return specs

    def _system(self, caller: Caller, project: Project | None) -> dict[str, Any]:
        return {"role": "system", "content": build_system_prompt(
            user=caller.user, project=project.id if project else None,
            project_title=project.title if project else None, channel=caller.channel,
            dialect=self.settings.sql_dialect_name, schema=project.default_schema if project else None,
            queries_enabled=self._queries_enabled(caller, project), web_url=self.settings.web_ui_url)}

    # Public API -------------------------------------------------------------------
    async def run(self, question: str, caller: Caller, history: list[dict[str, Any]] | None = None,
                  on_event: EventHandler | None = None) -> AgentResult:
        project = self._project(caller)
        messages = [self._system(caller, project), *(history or []), {"role": "user", "content": question}]
        state = {"messages": messages, "pending": [], "queries": [], "charts": [], "phenotypes": [],
                 "tool_calls": [], "steps": 0}
        return await self._loop(state, caller, project, on_event or _noop)

    async def resume(self, state: dict[str, Any], caller: Caller, approved: bool,
                     sql_override: str | None = None, on_event: EventHandler | None = None) -> AgentResult:
        project = self._project(caller)
        pending = state["pending"]
        if not pending or pending[0]["name"] != RUN_QUERY:
            raise ValueError("Nothing is awaiting approval")
        if approved:
            pending[0]["approved"] = True
            if sql_override:
                args = json.loads(pending[0]["arguments"] or "{}")
                args["sql"] = sql_override
                pending[0]["arguments"] = json.dumps(args)
        else:
            pending[0]["declined"] = True
        return await self._loop(state, caller, project, on_event or _noop)

    def history_from(self, result: AgentResult) -> list[dict[str, Any]]:
        """Conversation turns (without the system prompt) to pass as history to the next run."""
        if not result.state:
            return []
        # Keep only the visible conversation; tool traffic is re-fetched if needed. This keeps
        # history small and avoids orphaned tool messages when it is trimmed.
        return [{"role": m["role"], "content": m["content"]} for m in result.state["messages"]
                if m["role"] == "user" or (m["role"] == "assistant" and not m.get("tool_calls") and m["content"])]

    # Loop -------------------------------------------------------------------------
    async def _loop(self, state: dict[str, Any], caller: Caller, project: Project | None,
                    emit: EventHandler) -> AgentResult:
        async with MCPToolset(self._specs(caller, project)) as tools:
            openai_tools = tools.openai_tools + ([CHART_TOOL] if self._queries_enabled(caller, project) else [])
            # Finish any tool calls left over from a paused turn
            paused = await self._drain_pending(state, tools, emit)
            if paused:
                return paused
            while state["steps"] < self.settings.max_steps:
                state["steps"] += 1
                try:
                    reply = await self.llm.complete(state["messages"], openai_tools)
                except Exception as exc:  # openai.APIError and transport errors
                    answer = _llm_failure_message(exc, self.settings.llm_base_url, self.settings.llm_model)
                    await emit("error", {"message": answer})
                    return self._result("error", state, answer=answer)
                calls = reply.get("tool_calls") or []
                assistant: dict[str, Any] = {"role": "assistant", "content": reply.get("content") or ""}
                if calls:
                    assistant["tool_calls"] = [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
                state["messages"].append(assistant)
                if not calls:
                    await emit("final", {"answer": assistant["content"]})
                    return self._result("done", state, answer=assistant["content"])
                state["pending"] = [dict(c) for c in calls]
                paused = await self._drain_pending(state, tools, emit)
                if paused:
                    return paused
            answer = "I could not finish within the step limit. Try a narrower question."
            state["messages"].append({"role": "assistant", "content": answer})
            return self._result("error", state, answer=answer)

    async def _drain_pending(self, state: dict[str, Any], tools: MCPToolset, emit: EventHandler) -> AgentResult | None:
        while state["pending"]:
            call = state["pending"][0]
            name = call["name"]
            if (name == RUN_QUERY and self.settings.require_sql_approval
                    and not call.get("approved") and not call.get("declined")):
                args = _parse_args(call["arguments"])
                await emit("approval_needed", {"sql": args.get("sql"), "purpose": args.get("purpose")})
                return self._result("awaiting_approval", state, pending_sql=args.get("sql"),
                                    pending_purpose=args.get("purpose"))
            state["pending"].pop(0)
            if call.get("declined"):
                content = {"ok": False, "error": "The researcher declined to run this query. Ask what to change, "
                                                 "or answer from metadata."}
            else:
                content = await self._execute(call, state, tools, emit)
            state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": _clip(content)})
        return None

    async def _execute(self, call: dict[str, Any], state: dict[str, Any], tools: MCPToolset,
                       emit: EventHandler) -> Any:
        name, args = call["name"], _parse_args(call["arguments"])
        await emit("tool_start", {"name": name, "arguments": args})
        record = {"name": name, "arguments": args, "ok": True}
        try:
            if name == "create_chart":
                result = self._chart(args, state)
            elif tools.has(name):
                result = await tools.call(name, args)
            else:
                raise ToolError(f"Unknown tool {name}")
        except (ToolError, ChartError, PermissionError) as exc:
            result = {"ok": False, "error": str(exc)}
            record["ok"] = False
        state["tool_calls"].append(record | {"result_preview": _clip(result, 600)})

        if name == RUN_QUERY and isinstance(result, dict) and result.get("ok"):
            query = {k: result.get(k) for k in ("query_id", "sql_executed", "columns", "rows", "row_count",
                                               "truncated", "suppressed_cells", "notes")}
            query["purpose"] = args.get("purpose")
            state["queries"].append(query)
            await emit("query_result", query)
            # Only a sample of rows goes back to the model; the full result stays with the UI
            if len(result.get("rows", [])) > MAX_ROWS_TO_MODEL:
                result = result | {"rows": result["rows"][:MAX_ROWS_TO_MODEL],
                                   "note_to_model": f"Showing first {MAX_ROWS_TO_MODEL} rows"}
        if name == "pheno__get_phenotype_codes" and isinstance(result, dict) and "phenotype_id" in result:
            state["phenotypes"].append({"phenotype_id": result["phenotype_id"], "version_id": result.get("version_id"),
                                        "code_count": result.get("code_count")})
        await emit("tool_end", {"name": name, "ok": record["ok"], "result": result})
        return result

    def _chart(self, args: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        query = next((q for q in state["queries"] if q["query_id"] == args.get("query_id")), None)
        if query is None:
            raise ChartError(f"No query result with query_id {args.get('query_id')!r}. Run the query first.")
        fig = build_chart(query, args.get("chart_type", "bar"), args["x"], args["y"], args.get("title", ""),
                          args.get("series"))
        state["charts"].append(fig)
        return {"ok": True, "chart_index": len(state["charts"]) - 1}

    def _result(self, status: str, state: dict[str, Any], **kw: Any) -> AgentResult:
        return AgentResult(status=status, queries=list(state["queries"]), charts=list(state["charts"]),
                           phenotypes=list(state["phenotypes"]), tool_calls=list(state["tool_calls"]),
                           state=state, **kw)


def _llm_failure_message(exc: Exception, url: str, model: str) -> str:
    kind = type(exc).__name__
    if "Timeout" in kind or "Connection" in kind:
        hint = (f"The language model at {url} could not be reached ({kind}). Check LLM_BASE_URL is reachable "
                "from this container, and that the model server is running and listening on that address.")
    elif "NotFound" in kind:
        hint = f"The model server at {url} does not know the model {model!r}. Check LLM_MODEL (and pull the model)."
    elif "Authentication" in kind or "PermissionDenied" in kind:
        hint = f"The model server at {url} rejected the API key. Check LLM_API_KEY."
    else:
        hint = f"The language model call failed ({kind}: {str(exc)[:200]})."
    return hint + " No data was queried."


def _parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _clip(value: Any, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + " …[truncated]"
