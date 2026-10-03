"""Demo catalogue MCP server (stand-in for the OpenMetadata MCP server). Run with: sailx-catalog-mcp"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from ..mcp_common import make_server
from .catalog import Catalog

mcp = make_server(
    "sailx-catalog",
    "Metadata about SAIL datasets: descriptions, coverage, known issues and column profiles "
    "(completeness, distinct counts, ranges, common values). Contains no row-level data.",
    default_port=8101,
)


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    return Catalog.load(os.environ.get("CATALOG_FILE", "demo/catalog.yaml"), os.environ.get("PROFILE_DSN"))


@mcp.tool()
def list_datasets() -> dict[str, Any]:
    """List the datasets in the catalogue with their tables."""
    return {"datasets": catalog().list_datasets()}


@mcp.tool()
def search_metadata(query: str, limit: int = 10) -> dict[str, Any]:
    """Search datasets, tables, columns and glossary terms by keyword, e.g. "smoking" or "deprivation"."""
    return {"query": query, "results": catalog().search(query, limit=min(max(limit, 1), 30))}


@mcp.tool()
def get_table_details(table: str) -> dict[str, Any]:
    """Get a table's description, grain, coverage, known issues and every column with its
    description and profile (null proportion, distinct count, min/max, common values)."""
    return catalog().table_details(table)


@mcp.tool()
def get_column_profile(table: str, column: str) -> dict[str, Any]:
    """Get the profile of one column: completeness (null proportion), distinct values, range and common values."""
    return catalog().column_profile(table, column)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
