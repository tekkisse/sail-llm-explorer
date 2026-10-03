"""Static checks applied to every model-generated query before it reaches the database.

These are defence in depth. The database itself enforces read-only access and project
scope (READ ONLY transaction + project role); these checks give fast, explainable
refusals the model can correct, and stop obviously unsafe SQL early.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# Statement and clause types that must never appear in an explorer query.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    t for t in (
        getattr(exp, name, None)
        for name in ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
                     "Command", "Into", "Lock", "Copy", "Grant", "TruncateTable", "Set", "Use",
                     "Transaction", "Commit", "Rollback", "LoadData", "Pragma")
    ) if t is not None
)

_BLOCKED_FUNCTION_PREFIXES = ("pg_", "dblink", "lo_", "sys", "xp_")
_BLOCKED_FUNCTIONS = {
    "current_setting", "set_config", "query_to_xml", "copy", "load_file", "sleep", "benchmark",
}

# sqlglot has no DB2 dialect; DB2 SQL is parsed with the generic ANSI dialect.
_DIALECTS = {"postgres": "postgres", "db2": None, "ansi": None}


class GuardrailError(ValueError):
    """Raised with a message written for the model, so it can fix the query."""


@dataclass
class CheckedQuery:
    original_sql: str
    sql: str                     # normalised, schema-qualified SQL
    wrapped_sql: str             # with row cap applied
    tables: list[str] = field(default_factory=list)


def _dialect(name: str) -> str | None:
    return _DIALECTS.get(name.lower(), name.lower())


def _is_aggregated(select: exp.Select) -> bool:
    if select.args.get("group"):
        return True
    for projection in select.expressions:
        for agg in projection.find_all(exp.AggFunc):
            if agg.find_ancestor(exp.Window) is None:
                return True
    return False


def _top_level_selects(query: exp.Expression) -> list[exp.Select]:
    if isinstance(query, exp.SetOperation):
        return _top_level_selects(query.left) + _top_level_selects(query.right)
    if isinstance(query, exp.Subquery):
        return _top_level_selects(query.this)
    if isinstance(query, exp.Select):
        return [query]
    return []


def check_sql(
    sql: str,
    *,
    dialect: str,
    allowed_schemas: list[str] | tuple[str, ...],
    default_schema: str,
    max_rows: int,
    require_aggregation: bool = True,
) -> CheckedQuery:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise GuardrailError("The query is empty.")
    read = _dialect(dialect)
    try:
        statements = [s for s in sqlglot.parse(sql, read=read) if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise GuardrailError(f"The SQL could not be parsed: {exc}".splitlines()[0]) from exc
    if len(statements) != 1:
        raise GuardrailError("Send exactly one SQL statement.")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise GuardrailError("Only SELECT queries are allowed.")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise GuardrailError(f"{type(node).__name__.upper()} is not allowed; only read-only SELECT queries.")

    for func in tree.find_all(exp.Anonymous):
        name = (func.name or "").lower()
        if name in _BLOCKED_FUNCTIONS or name.startswith(_BLOCKED_FUNCTION_PREFIXES):
            raise GuardrailError(f"Function {name}() is not allowed.")

    allowed = {s.lower() for s in allowed_schemas}
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        schema = (table.db or "").lower()
        if not name:
            continue
        if not schema and name in cte_names:
            continue
        if table.catalog:
            raise GuardrailError("Cross-database references are not allowed.")
        if not schema:
            table.set("db", exp.to_identifier(default_schema))
            schema = default_schema.lower()
        if schema not in allowed:
            raise GuardrailError(
                f"Table {schema}.{name} is outside this project. Use tables in: {', '.join(sorted(allowed))} "
                f"(unqualified names resolve to {default_schema}).")
        tables.append(f"{schema}.{name}")

    if require_aggregation:
        selects = _top_level_selects(tree)
        if not selects or not all(_is_aggregated(s) for s in selects):
            raise GuardrailError(
                "Results must be aggregated (GROUP BY with counts, or aggregate functions such as COUNT, AVG). "
                "Row-level results are not returned by the explorer. Include COUNT(DISTINCT alf_pe) AS n "
                "so small numbers can be checked.")

    normalised = tree.sql(dialect=read, pretty=True) if read else tree.sql(pretty=True)
    cap = max_rows + 1  # one extra row tells us the result was truncated
    if (dialect or "").lower() == "db2":
        wrapped = f"SELECT * FROM ({normalised}) AS sailx_q FETCH FIRST {cap} ROWS ONLY"
    else:
        wrapped = f"SELECT * FROM ({normalised}) AS sailx_q LIMIT {cap}"
    return CheckedQuery(original_sql=sql, sql=normalised, wrapped_sql=wrapped, tables=sorted(set(tables)))
