"""Database backends. Each runs one checked query inside the caller's project scope."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..config import SqlSettings
from ..projects import Project


class QueryRefused(ValueError):
    """Raised with a message written for the model."""


@dataclass
class RawResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    duration_ms: int
    estimated_cost: float | None = None


class Backend(Protocol):
    def explain_cost(self, sql: str, project: Project) -> float | None: ...
    def execute(self, sql: str, project: Project, max_rows: int) -> RawResult: ...
    def describe(self, project: Project) -> list[dict[str, Any]]: ...


class PostgresBackend:
    """Connects as a NOINHERIT gateway login and SET ROLEs into the project role per query.

    Every query runs in a READ ONLY transaction with a statement timeout and the project
    schema as search_path, so the database enforces scope even if a check above is missed.
    """

    def __init__(self, settings: SqlSettings):
        from psycopg_pool import ConnectionPool

        self.settings = settings
        self.pool = ConnectionPool(settings.dsn, min_size=1, max_size=8, open=True)

    def _scoped(self, conn, project: Project):
        from psycopg import sql as psql

        if not project.db_role:
            raise QueryRefused(f"Project {project.id} has no database role configured.")
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(psql.SQL("SET LOCAL ROLE {}").format(psql.Identifier(project.db_role)))
        conn.execute(psql.SQL("SET LOCAL statement_timeout = {}").format(psql.Literal(self.settings.timeout_ms)))
        conn.execute(psql.SQL("SET LOCAL search_path = {}").format(
            psql.SQL(", ").join(psql.Identifier(s) for s in project.schemas)))

    def explain_cost(self, sql: str, project: Project) -> float | None:
        with self.pool.connection() as conn, conn.transaction(force_rollback=True):
            self._scoped(conn, project)
            plan = conn.execute(f"EXPLAIN (FORMAT JSON) {sql}").fetchone()[0]
            if isinstance(plan, str):
                plan = json.loads(plan)
            return float(plan[0]["Plan"]["Total Cost"])

    def execute(self, sql: str, project: Project, max_rows: int) -> RawResult:
        import psycopg

        start = time.monotonic()
        with self.pool.connection() as conn, conn.transaction(force_rollback=True):
            self._scoped(conn, project)
            try:
                cur = conn.execute(sql)
            except psycopg.errors.QueryCanceled as exc:
                raise QueryRefused(f"The query exceeded the {self.settings.timeout_ms} ms time limit. "
                                   "Narrow it, for example with a date range or fewer joins.") from exc
            except psycopg.Error as exc:
                raise QueryRefused(f"Database error: {str(exc).strip().splitlines()[0]}") from exc
            columns = [d.name for d in cur.description or []]
            rows = [list(r) for r in cur.fetchmany(max_rows + 1)]
        truncated = len(rows) > max_rows
        return RawResult(columns, rows[:max_rows], truncated, int((time.monotonic() - start) * 1000))

    def describe(self, project: Project) -> list[dict[str, Any]]:
        q = """
            SELECT c.table_schema, c.table_name, c.column_name, c.data_type
            FROM information_schema.columns c
            WHERE c.table_schema = ANY(%s)
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
        """
        with self.pool.connection() as conn, conn.transaction(force_rollback=True):
            self._scoped(conn, project)
            rows = conn.execute(q, (list(project.schemas),)).fetchall()
        tables: dict[str, dict[str, Any]] = {}
        for schema, table, column, dtype in rows:
            t = tables.setdefault(f"{schema}.{table}", {"table": f"{schema}.{table}", "columns": []})
            t["columns"].append({"name": column, "type": dtype})
        return list(tables.values())


class Db2Backend:
    """IBM DB2 backend (UNTESTED reference implementation).

    Uses per-project read-only credentials mounted as files:
        <DB2_CREDENTIALS_DIR>/<db_credentials_secret>/username|password
    Cost limits on DB2 are better enforced with DB2 Workload Manager thresholds
    (ESTIMATEDSQLCOST / ACTIVITYTOTALTIME) on the project users than with EXPLAIN here.
    Requires the optional dependency: pip install ibm_db
    """

    def __init__(self, settings: SqlSettings):
        self.settings = settings

    def _connect(self, project: Project):
        import ibm_db_dbi  # type: ignore

        if not project.db_credentials_secret:
            raise QueryRefused(f"Project {project.id} has no DB2 credentials configured.")
        base = Path(self.settings.db2_credentials_dir) / project.db_credentials_secret
        user = (base / "username").read_text().strip()
        password = (base / "password").read_text().strip()
        conn = ibm_db_dbi.connect(f"{self.settings.dsn};UID={user};PWD={password};", "", "")
        cur = conn.cursor()
        cur.execute(f"SET CURRENT SCHEMA = {project.default_schema.upper()}")
        return conn

    def explain_cost(self, sql: str, project: Project) -> float | None:
        return None

    def execute(self, sql: str, project: Project, max_rows: int) -> RawResult:
        start = time.monotonic()
        conn = self._connect(project)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [d[0].lower() for d in cur.description or []]
            rows = [list(r) for r in cur.fetchmany(max_rows + 1)]
        except Exception as exc:  # ibm_db raises its own exception types
            raise QueryRefused(f"Database error: {str(exc).splitlines()[0]}") from exc
        finally:
            conn.rollback()
            conn.close()
        truncated = len(rows) > max_rows
        return RawResult(columns, rows[:max_rows], truncated, int((time.monotonic() - start) * 1000))

    def describe(self, project: Project) -> list[dict[str, Any]]:
        conn = self._connect(project)
        try:
            cur = conn.cursor()
            schemas = ",".join(f"'{s.upper()}'" for s in project.schemas)
            cur.execute(
                "SELECT TABSCHEMA, TABNAME, COLNAME, TYPENAME FROM SYSCAT.COLUMNS "
                f"WHERE TABSCHEMA IN ({schemas}) ORDER BY TABSCHEMA, TABNAME, COLNO")
            tables: dict[str, dict[str, Any]] = {}
            for schema, table, column, dtype in cur.fetchall():
                key = f"{schema.strip().lower()}.{table.strip().lower()}"
                t = tables.setdefault(key, {"table": key, "columns": []})
                t["columns"].append({"name": column.strip().lower(), "type": dtype.strip().lower()})
            return list(tables.values())
        finally:
            conn.close()


def make_backend(settings: SqlSettings) -> Backend:
    if settings.backend == "postgres":
        return PostgresBackend(settings)
    if settings.backend == "db2":
        return Db2Backend(settings)
    raise ValueError(f"Unknown SQL_BACKEND {settings.backend!r}")
