"""Phenotype Library MCP server. Read-only and holds no research data. Run with: sailx-phenotype-mcp"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from ..mcp_common import make_server
from .client import PhenotypeClient

mcp = make_server(
    "sailx-phenotypes",
    "Look up curated, versioned clinical code lists (phenotypes) from the HDR UK Phenotype Library. "
    "Use this to turn a clinical concept into codes; never invent codes.",
    default_port=8102,
)


@lru_cache(maxsize=1)
def client() -> PhenotypeClient:
    return PhenotypeClient()


@mcp.tool()
def search_phenotypes(query: str, limit: int = 10) -> dict[str, Any]:
    """Search the Phenotype Library for a clinical concept, e.g. "type 2 diabetes" or "current smoker".
    Returns phenotype IDs, versions, names and the coding systems each covers."""
    return {"query": query, "results": client().search(query, limit=min(max(limit, 1), 25))}


@mcp.tool()
def get_phenotype(phenotype_id: str, version_id: str | None = None) -> dict[str, Any]:
    """Get the definition and summary of one phenotype (optionally a specific version)."""
    return client().detail(phenotype_id, version_id)


@mcp.tool()
def get_phenotype_codes(phenotype_id: str, version_id: str | None = None,
                        coding_system: str | None = None) -> dict[str, Any]:
    """Get the code list for a phenotype, grouped by coding system. Filter with coding_system,
    e.g. "Read" for GP data (WLGP) or "ICD10" for hospital (PEDW) and death (ADDE) data.
    Always report the phenotype ID and version you used."""
    return client().codes(phenotype_id, version_id, coding_system)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
