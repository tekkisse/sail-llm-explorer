from pathlib import Path

import httpx
import pytest

from sailx.agent.charts import ChartError, build_chart
from sailx.agent.mcp_bridge import ServerSpec
from sailx.config import IdentitySettings
from sailx.identity import Caller, TokenError, mint_token, verify_token
from sailx.phenotype.client import PhenotypeClient
from sailx.projects import ProjectRegistry

ROOT = Path(__file__).resolve().parents[1]


def test_metadata_write_tools_are_filtered():
    spec = ServerSpec("meta", "http://x", deny_words=["create", "update", "patch", "delete", "set"])
    assert spec.permits("search_metadata")
    assert spec.permits("list_datasets")          # "datasets" is not "set"
    assert spec.permits("get_entity_lineage")
    assert not spec.permits("create_glossary_term")
    assert not spec.permits("patch_entity")
    assert ServerSpec("m", "x", allow=["search_metadata"]).permits("get_entity_details") is False


def test_tokens_round_trip_and_reject_tampering():
    s = IdentitySettings(shared_secret="s" * 32)
    token = mint_token(Caller("Alice@Example.org", "demo", "web"), s)
    caller = verify_token(token, s)
    assert caller == Caller("alice@example.org", "demo", "web")
    with pytest.raises(TokenError):
        verify_token(token, IdentitySettings(shared_secret="t" * 32))
    with pytest.raises(TokenError):
        verify_token(token, IdentitySettings(shared_secret="s" * 32, audience="other"))


def test_registry_membership():
    reg = ProjectRegistry.load(ROOT / "demo/projects.yaml")
    assert [p.id for p in reg.for_user("ALICE@example.org")] == ["demo"]
    with pytest.raises(PermissionError):
        reg.authorise("alice@example.org", "other")


def test_chart_handles_suppressed_cells():
    result = {"query_id": "q1", "columns": ["year", "n"], "rows": [[2020, 100], [2021, "<10"], [2022, 120]]}
    fig = build_chart(result, "line", "year", "n", "Trend")
    assert fig["data"][0]["y"] == [100, None, 120]
    assert any("suppressed" in a["text"] for a in fig["layout"]["annotations"])
    with pytest.raises(ChartError):
        build_chart(result, "bar", "year", "missing", "x")


def test_phenotype_fixture_search_and_codes():
    client = PhenotypeClient(fixtures=str(ROOT / "demo/phenotypes.json"))
    [hit, *_] = client.search("type 2 diabetes")
    assert hit["phenotype_id"] == "PH-DEMO-1"
    codes = client.codes("PH-DEMO-1", coding_system="Read")
    assert list(codes["codes_by_system"]) == ["Read codes v2"]
    assert {c["code"] for c in codes["codes_by_system"]["Read codes v2"]} == {"C10F.", "C109."}


def test_phenotype_http_mode_is_tolerant_of_response_shapes():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/export/codes/" in request.url.path:
            return httpx.Response(200, json=[{"code": "C10F.", "description": "T2DM",
                                              "coding_system": "Read codes v2"}])
        if request.url.path.endswith("/phenotypes/"):
            assert request.url.params["search"] == "type 2 diabetes"
            return httpx.Response(200, json={"results": [{"phenotype_id": "PH1", "phenotype_version_id": 2,
                                                          "name": "Type 2 diabetes"}]})
        return httpx.Response(404)

    client = PhenotypeClient(base_url="https://example.test/api/v1", fixtures="", transport=httpx.MockTransport(handler))
    [hit] = client.search("type 2 diabetes")
    assert hit == hit | {"phenotype_id": "PH1", "version_id": 2, "name": "Type 2 diabetes"}
    codes = client.codes("PH1", version_id=2)
    assert codes["code_count"] == 1 and codes["version_id"] == 2


def test_short_service_secret_is_rejected():
    with pytest.raises(TokenError, match="at least 32"):
        mint_token(Caller("a@b.org", "demo"), IdentitySettings(shared_secret="x" * 25))


async def test_unreachable_llm_gives_clear_message(tmp_path):
    from sailx.agent.agent import Agent
    from sailx.config import AgentSettings

    class DeadLLM:
        async def complete(self, messages, tools):
            import httpx
            raise httpx.ConnectTimeout("timed out")

    settings = AgentSettings()
    settings.metadata_mcp_url = settings.phenotype_mcp_url = "http://unused"
    agent = Agent(settings=settings, llm=DeadLLM(), registry=ProjectRegistry.load(ROOT / "demo/projects.yaml"))
    agent._specs = lambda caller, project: []          # no MCP servers needed for this test
    from sailx.identity import Caller as C
    result = await agent.run("hi", C("alice@example.org", "", "web"))
    assert result.status == "error"
    assert "could not be reached" in result.answer and "LLM_BASE_URL" in result.answer
