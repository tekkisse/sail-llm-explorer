"""SQL execution MCP server: the only component that can reach research data.

Every call must carry a service token naming the user and project. The server checks
project membership itself, applies guardrails, runs the query in the project's scope,
applies disclosure control, and writes an audit record. Run with: sailx-sql-mcp
"""
from __future__ import annotations

import time
import uuid
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import Context

from ..audit import AuditLog
from ..config import IdentitySettings, SdcSettings, SqlSettings
from ..identity import Caller, TokenError, verify_token
from ..mcp_common import bearer_token, make_server
from ..projects import Project, ProjectRegistry
from .backends import QueryRefused, make_backend
from .disclosure import DisclosureError, apply_sdc
from .guardrails import GuardrailError, check_sql

mcp = make_server(
    "sailx-sql",
    "Runs read-only, aggregated SQL against the caller's approved project views, with "
    "disclosure control. Unqualified table names resolve to the project schema.",
    default_port=8103,
)


class Services:
    def __init__(self) -> None:
        self.sql = SqlSettings()
        self.sdc = SdcSettings()
        self.identity = IdentitySettings()
        self.registry = ProjectRegistry.load(self.sql.projects_file)
        self.audit = AuditLog(self.sql.audit_log)
        self._backend = None

    @property
    def backend(self):
        if self._backend is None:
            self._backend = make_backend(self.sql)
        return self._backend


@lru_cache(maxsize=1)
def services() -> Services:
    return Services()


def _caller(ctx: Context) -> tuple[Caller, Project]:
    svc = services()
    token = bearer_token(ctx)
    if not token:
        raise PermissionError("Missing service token.")
    try:
        caller = verify_token(token, svc.identity)
    except TokenError as exc:
        raise PermissionError(str(exc)) from exc
    project = svc.registry.authorise(caller.user, caller.project)
    return caller, project


def _check(sql: str, project: Project):
    svc = services()
    checked = check_sql(sql, dialect=svc.sql.dialect, allowed_schemas=project.schemas,
                        default_schema=project.default_schema, max_rows=svc.sql.max_rows,
                        require_aggregation=svc.sql.require_aggregation)
    cost = svc.backend.explain_cost(checked.sql, project)
    if cost is not None and cost > svc.sql.max_cost:
        raise GuardrailError(f"Estimated query cost {cost:,.0f} exceeds the limit of {svc.sql.max_cost:,.0f}. "
                             "Narrow the query (date range, fewer joins, filter on codes first).")
    return checked, cost


@mcp.tool()
def describe_project_schema(ctx: Context) -> dict[str, Any]:
    """List the tables and columns available in the caller's project. Use the names exactly as shown
    (schema prefix optional). Descriptions and meaning come from the metadata catalogue, not here."""
    caller, project = _caller(ctx)
    tables = services().backend.describe(project)
    return {"project": project.id, "default_schema": project.default_schema, "tables": tables}


@mcp.tool()
def validate_sql(sql: str, ctx: Context) -> dict[str, Any]:
    """Check a query against the guardrails and estimate its cost without running it."""
    caller, project = _caller(ctx)
    try:
        checked, cost = _check(sql, project)
    except (GuardrailError, QueryRefused) as exc:
        return {"valid": False, "error": str(exc)}
    return {"valid": True, "normalised_sql": checked.sql, "tables": checked.tables, "estimated_cost": cost}


@mcp.tool()
def run_query(sql: str, purpose: str, ctx: Context) -> dict[str, Any]:
    """Run one read-only, aggregated SELECT in the caller's project and return disclosure-controlled
    results. Always include a person count such as COUNT(DISTINCT alf_pe) AS n for each group.
    `purpose` is a one-line description of what the query answers (recorded in the audit log)."""
    svc = services()
    caller, project = _caller(ctx)
    query_id = uuid.uuid4().hex[:12]
    base = {"query_id": query_id, "user": caller.user, "project": project.id, "channel": caller.channel,
            "purpose": purpose, "sql": sql}

    if caller.channel == "slack" and not project.allow_slack_queries:
        svc.audit.write("query_refused", **base, reason="slack_channel")
        return {"ok": False, "query_id": query_id,
                "error": "Data queries are not available from Slack for this project. Ask the user to open the web explorer."}
    started = time.monotonic()
    try:
        checked, cost = _check(sql, project)
        raw = svc.backend.execute(checked.wrapped_sql, project, svc.sql.max_rows)
        sdc = apply_sdc(raw.columns, raw.rows, svc.sdc)
    except (GuardrailError, QueryRefused, DisclosureError) as exc:
        svc.audit.write("query_refused", **base, reason=str(exc),
                        duration_ms=int((time.monotonic() - started) * 1000))
        return {"ok": False, "query_id": query_id, "error": str(exc)}

    notes = list(sdc.notes)
    if raw.truncated:
        notes.append(f"Result truncated to {svc.sql.max_rows} rows.")
    svc.audit.write("query_run", **base, normalised_sql=checked.sql, tables=checked.tables,
                    estimated_cost=cost, rows=len(sdc.rows), suppressed_cells=sdc.suppressed_cells,
                    duration_ms=raw.duration_ms)
    return {
        "ok": True,
        "query_id": query_id,
        "sql_executed": checked.sql,
        "columns": sdc.columns,
        "rows": sdc.rows,
        "row_count": len(sdc.rows),
        "truncated": raw.truncated,
        "suppressed_cells": sdc.suppressed_cells,
        "count_columns": sdc.count_columns,
        "notes": notes,
    }


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
