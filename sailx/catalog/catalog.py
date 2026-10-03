"""Demo metadata catalogue: descriptions from YAML plus a live column profile.

Stands in for OpenMetadata so the whole stack runs without it. Tool names and shapes
mirror what the agent needs from OpenMetadata (search, table details, column profile).
Profile hygiene: no min/max/top values for columns flagged sensitive.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


@dataclass
class Catalog:
    raw: dict[str, Any]
    profile_dsn: str | None = None
    min_top_value_count: int = 10
    _profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def load(cls, path: str | Path, profile_dsn: str | None = None) -> "Catalog":
        return cls(yaml.safe_load(Path(path).read_text()), profile_dsn)

    @property
    def schema(self) -> str:
        return self.raw.get("schema", "sail")

    @property
    def tables(self) -> dict[str, dict[str, Any]]:
        return {t["name"]: t for t in self.raw.get("tables", [])}

    def fqn(self, table: str) -> str:
        return f"{self.raw.get('service', 'sail')}.{self.raw.get('database', 'sail')}.{self.schema}.{table}"

    def _dataset_of(self, table: str) -> dict[str, Any] | None:
        for d in self.raw.get("datasets", []):
            if table in d.get("tables", []):
                return d
        return None

    # Tools ------------------------------------------------------------------------
    def list_datasets(self) -> list[dict[str, Any]]:
        return [{"name": d["name"], "title": d.get("title"), "description": d.get("description"),
                 "tables": d.get("tables", [])} for d in self.raw.get("datasets", [])]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = _tokens(query)
        if not q:
            return []
        hits: list[tuple[float, dict[str, Any]]] = []

        def score(*texts: str, weight: float = 1.0) -> float:
            hay = set().union(*(_tokens(t or "") for t in texts))
            s = len(q & hay)
            # light prefix matching: "smok" ~ "smoking"
            s += 0.5 * sum(1 for w in q for h in hay if w != h and len(w) > 3 and h.startswith(w))
            return weight * s

        for d in self.raw.get("datasets", []):
            s = score(d["name"], d.get("title", ""), d.get("description", ""), weight=1.2)
            if s:
                hits.append((s, {"type": "dataset", "name": d["name"], "title": d.get("title"),
                                 "description": d.get("description"), "tables": d.get("tables")}))
        for t in self.raw.get("tables", []):
            s = score(t["name"], t.get("description", ""), " ".join(t.get("topics", [])), weight=1.5)
            if s:
                hits.append((s, {"type": "table", "table": t["name"], "fqn": self.fqn(t["name"]),
                                 "description": t.get("description"), "topics": t.get("topics", [])}))
            for c in t.get("columns", []):
                s = score(c["name"], c.get("description", ""))
                if s:
                    hits.append((s, {"type": "column", "table": t["name"], "column": c["name"],
                                     "description": c.get("description")}))
        for g in self.raw.get("glossary", []):
            s = score(g["term"], g.get("description", ""), weight=0.8)
            if s:
                hits.append((s, {"type": "glossary", "term": g["term"], "description": g.get("description")}))
        hits.sort(key=lambda h: -h[0])
        return [h for _, h in hits[:limit]]

    def table_details(self, table: str) -> dict[str, Any]:
        table = table.split(".")[-1].lower()
        t = self.tables.get(table)
        if t is None:
            raise KeyError(f"Unknown table {table!r}. Use search_metadata or list_datasets first.")
        profile = self.profile(table)
        columns = []
        for c in t.get("columns", []):
            col = {k: v for k, v in c.items() if k != "identifier"}
            col["profile"] = profile.get("columns", {}).get(c["name"])
            columns.append(col)
        ds = self._dataset_of(table)
        return {
            "table": table, "fqn": self.fqn(table), "dataset": ds["name"] if ds else None,
            "description": t.get("description"), "grain": t.get("grain"), "coverage": t.get("coverage"),
            "topics": t.get("topics", []), "known_issues": t.get("known_issues", []),
            "row_count": profile.get("row_count"), "columns": columns,
        }

    def column_profile(self, table: str, column: str) -> dict[str, Any]:
        table = table.split(".")[-1].lower()
        prof = self.profile(table).get("columns", {}).get(column.lower())
        if prof is None:
            raise KeyError(f"No profile for {table}.{column}")
        return {"table": table, "column": column.lower(), **prof}

    # Profiling ----------------------------------------------------------------------
    def profile(self, table: str) -> dict[str, Any]:
        if not self.profile_dsn:
            return {}
        with self._lock:
            if table not in self._profiles:
                self._profiles[table] = self._compute_profile(table)
            return self._profiles[table]

    def _compute_profile(self, table: str) -> dict[str, Any]:
        import psycopg
        from psycopg import sql as psql

        t = self.tables[table]
        ident = psql.Identifier(self.schema, table)
        result: dict[str, Any] = {"columns": {}}
        with psycopg.connect(self.profile_dsn) as conn:
            types = dict(conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s", (self.schema, table)).fetchall())
            total = conn.execute(psql.SQL("SELECT count(*) FROM {}").format(ident)).fetchone()[0]
            result["row_count"] = total
            for c in t.get("columns", []):
                name = c["name"]
                col = psql.Identifier(name)
                dtype = types.get(name, "")
                nulls, distinct = conn.execute(psql.SQL(
                    "SELECT count(*) FILTER (WHERE {c} IS NULL), count(DISTINCT {c}) FROM {t}").format(
                    c=col, t=ident)).fetchone()
                prof: dict[str, Any] = {
                    "data_type": dtype,
                    "null_proportion": round(nulls / total, 4) if total else None,
                    "distinct_count": distinct,
                }
                if not c.get("sensitive"):
                    if dtype in ("date", "integer", "smallint", "bigint", "numeric", "double precision", "real"):
                        lo, hi = conn.execute(psql.SQL("SELECT min({c}), max({c}) FROM {t}").format(
                            c=col, t=ident)).fetchone()
                        prof["min"], prof["max"] = str(lo) if lo is not None else None, str(hi) if hi is not None else None
                    if distinct and distinct <= 50 or c.get("code_system"):
                        top = conn.execute(psql.SQL(
                            "SELECT {c}::text, count(*) FROM {t} WHERE {c} IS NOT NULL GROUP BY 1 "
                            "HAVING count(*) >= %s ORDER BY 2 DESC LIMIT 15").format(c=col, t=ident),
                            (self.min_top_value_count,)).fetchall()
                        prof["top_values"] = [{"value": v, "rows": n} for v, n in top]
                result["columns"][name] = prof
        return result
