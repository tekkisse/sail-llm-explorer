"""End-to-end tests against the demo database with a scripted LLM (no model needed)."""
from __future__ import annotations

import json

import pytest

from tests.conftest import requires_pg

pytestmark = requires_pg

T2DM_BY_WIMD = """
SELECT p.wimd_2019_quintile, COUNT(DISTINCT p.alf_pe) AS n
FROM wdsd_ar_pers p
JOIN wlgp_gp_event_cleansed e ON e.alf_pe = p.alf_pe
WHERE e.event_cd IN ('C10F.', 'C109.')
GROUP BY p.wimd_2019_quintile
ORDER BY p.wimd_2019_quintile
"""


class ScriptedLLM:
    """Plays a fixed sequence of tool calls, reading ids from earlier tool results."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.seen_tools: list[list[str]] = []

    async def complete(self, messages, tools):
        self.seen_tools.append([t["function"]["name"] for t in tools])
        step = self.steps.pop(0)
        if callable(step):
            step = step(messages)
        if isinstance(step, str):
            return {"content": step, "tool_calls": []}
        name, args = step
        return {"content": None, "tool_calls": [
            {"id": f"call_{len(self.steps)}", "name": name, "arguments": json.dumps(args)}]}


def _last_tool_json(messages):
    return json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])


def _agent(llm, urls, **overrides):
    from sailx.agent.agent import Agent
    from sailx.config import AgentSettings

    settings = AgentSettings()
    settings.metadata_mcp_url, settings.phenotype_mcp_url, settings.sql_mcp_url = urls["meta"], urls["pheno"], urls["sql"]
    for k, v in overrides.items():
        setattr(settings, k, v)
    return Agent(settings=settings, llm=llm)


async def test_full_question_with_approval_and_chart(mcp_servers):
    from sailx.identity import Caller

    llm = ScriptedLLM([
        ("meta__search_metadata", {"query": "diabetes deprivation"}),
        ("meta__get_table_details", {"table": "wlgp_gp_event_cleansed"}),
        ("pheno__search_phenotypes", {"query": "type 2 diabetes"}),
        ("pheno__get_phenotype_codes", {"phenotype_id": "PH-DEMO-1", "coding_system": "Read"}),
        ("sql__run_query", {"sql": T2DM_BY_WIMD, "purpose": "People with T2DM by WIMD quintile"}),
        lambda m: ("create_chart", {"query_id": _last_tool_json(m)["query_id"], "chart_type": "bar",
                                    "x": "wimd_2019_quintile", "y": "n",
                                    "title": "More people with type 2 diabetes in deprived areas"}),
        "Type 2 diabetes is more common in more deprived areas.",
    ])
    agent = _agent(llm, mcp_servers)
    caller = Caller("alice@example.org", "demo", "web")

    first = await agent.run("How does type 2 diabetes vary by deprivation?", caller)
    assert first.status == "awaiting_approval"
    assert "C10F." in first.pending_sql
    assert first.queries == []

    # Metadata tools came back with a profile and no write tools were offered
    tool_names = llm.seen_tools[0]
    assert "meta__search_metadata" in tool_names and "sql__run_query" in tool_names
    assert not any(w in n for n in tool_names for w in ("create_glossary", "patch"))

    done = await agent.resume(first.state, caller, approved=True)
    assert done.status == "done"
    assert done.answer.startswith("Type 2 diabetes")
    [q] = done.queries
    assert q["columns"] == ["wimd_2019_quintile", "n"]
    assert len(q["rows"]) == 5
    assert all(isinstance(r[1], int) and r[1] % 5 == 0 for r in q["rows"])   # rounded counts
    assert done.phenotypes[0]["phenotype_id"] == "PH-DEMO-1"
    assert done.charts and done.charts[0]["data"][0]["type"] == "bar"


async def test_declined_query_is_not_run(mcp_servers):
    from sailx.identity import Caller

    llm = ScriptedLLM([
        ("sql__run_query", {"sql": T2DM_BY_WIMD, "purpose": "test"}),
        "Understood, I won't run it.",
    ])
    agent = _agent(llm, mcp_servers)
    caller = Caller("alice@example.org", "demo", "web")
    first = await agent.run("q", caller)
    done = await agent.resume(first.state, caller, approved=False)
    assert done.status == "done" and done.queries == []
    tool_msg = [m for m in done.state["messages"] if m["role"] == "tool"][-1]
    assert "declined" in tool_msg["content"]


async def test_model_self_corrects_after_refusal(mcp_servers):
    from sailx.identity import Caller

    llm = ScriptedLLM([
        ("sql__run_query", {"sql": "SELECT alf_pe FROM wdsd_ar_pers", "purpose": "bad"}),
        lambda m: ("sql__run_query", {"sql": "SELECT gndr_cd, COUNT(DISTINCT alf_pe) AS n FROM wdsd_ar_pers GROUP BY gndr_cd",
                                      "purpose": "by sex"}) if "aggregated" in _last_tool_json(m)["error"] else "fail",
        "Done.",
    ])
    agent = _agent(llm, mcp_servers, require_sql_approval=False)
    result = await agent.run("people by sex", Caller("alice@example.org", "demo", "web"))
    assert result.status == "done"
    [q] = result.queries
    sexes = {r[0]: r[1] for r in q["rows"]}
    assert sexes[9] == "<10" or isinstance(sexes[9], int)   # rare category handled by SDC


async def test_slack_channel_gets_no_sql_tools(mcp_servers):
    from sailx.identity import Caller

    llm = ScriptedLLM(["Open the web explorer to run this."])
    agent = _agent(llm, mcp_servers)
    await agent.run("how many people have diabetes", Caller("alice@example.org", "demo", "slack"))
    assert not any(n.startswith("sql__") for n in llm.seen_tools[0])
    assert "create_chart" not in llm.seen_tools[0]


async def test_project_isolation_is_enforced_by_the_database(demo_env):
    from sailx.config import SqlSettings
    from sailx.projects import ProjectRegistry
    from sailx.sql.backends import PostgresBackend, QueryRefused

    registry = ProjectRegistry.load(demo_env["PROJECTS_FILE"])
    backend = PostgresBackend(SqlSettings())
    other = registry.projects["other"]
    # Bypassing the guardrails entirely: the project role still cannot see another project
    with pytest.raises(QueryRefused, match="permission denied"):
        backend.execute("SELECT count(*) FROM proj_demo.wdsd_ar_pers", other, 10)
    with pytest.raises(QueryRefused, match="permission denied"):
        backend.execute("SELECT count(*) FROM sail.wdsd_ar_pers", other, 10)
    with pytest.raises(QueryRefused, match="read-only"):
        backend.execute("CREATE TABLE proj_other.x (a int)", other, 10)
    ok = backend.execute("SELECT count(*) AS n FROM adde_deaths", other, 10)
    assert ok.rows[0][0] > 0


async def test_sql_server_rejects_wrong_project_and_bad_tokens(mcp_servers, demo_env):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    from sailx.config import IdentitySettings
    from sailx.identity import Caller, mint_token

    async def call(token):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with create_mcp_http_client(headers=headers) as http, \
                streamable_http_client(mcp_servers["sql"], http_client=http) as (r, w, _), \
                ClientSession(r, w) as session:
            await session.initialize()
            return await session.call_tool("describe_project_schema", {})

    good = mint_token(Caller("alice@example.org", "demo"), IdentitySettings())
    assert not (await call(good)).isError
    wrong_project = mint_token(Caller("alice@example.org", "other"), IdentitySettings())
    assert (await call(wrong_project)).isError
    assert (await call("not-a-token")).isError
    assert (await call(None)).isError


async def test_catalog_profile_hides_sensitive_values(demo_env):
    from sailx.catalog.catalog import Catalog

    cat = Catalog.load(demo_env["CATALOG_FILE"], demo_env["PROFILE_DSN"])
    details = cat.table_details("wdsd_ar_pers")
    cols = {c["name"]: c for c in details["columns"]}
    assert details["row_count"] == 20000
    assert "min" not in cols["alf_pe"]["profile"] and "top_values" not in cols["lsoa2011_cd"]["profile"]
    assert 0.35 < cols["ethn_cat"]["profile"]["null_proportion"] < 0.45
    assert cols["wob"]["profile"]["min"] == "1900-01-01"   # the placeholder shows up in the profile
    hits = cat.search("smoking status")
    assert hits[0]["type"] == "table" and hits[0]["table"] == "wlgp_gp_event_cleansed"


async def test_agent_api_requires_key_and_runs_in_discovery_mode(mcp_servers, monkeypatch):
    import httpx

    from sailx.agent import api

    llm = ScriptedLLM(["WLGP holds GP diagnoses coded in Read v2."])
    monkeypatch.setenv("AGENT_API_KEY", "k")
    monkeypatch.setattr(api, "agent", lambda: _agent(llm, mcp_servers))
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        body = {"question": "Is diabetes recorded?", "user_email": "alice@example.org"}
        assert (await client.post("/v1/ask", json=body)).status_code == 401
        resp = await client.post("/v1/ask", json=body, headers={"X-API-Key": "k"})
    assert resp.status_code == 200 and resp.json()["answer"].startswith("WLGP")
    assert not any(n.startswith("sql__") for n in llm.seen_tools[0])
