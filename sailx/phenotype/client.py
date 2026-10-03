"""Client for the HDR UK Phenotype Library API (Concept Library), with an offline fixture mode.

Endpoint paths are configurable because they differ between library versions; defaults
target the public v1 API. TREs usually have no internet access, so in production point
PHENOTYPE_API_BASE at an internal mirror of the library, or use a fixture export.
Response parsing is deliberately tolerant of field-name differences.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "phenotypes", "codes", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return []


def _summary(p: dict[str, Any]) -> dict[str, Any]:
    coding = _first(p, "coding_system", "coding_systems", default=[])
    if isinstance(coding, list):
        coding = [c.get("name", c) if isinstance(c, dict) else c for c in coding]
    return {
        "phenotype_id": _first(p, "phenotype_id", "id", "entity_id"),
        "version_id": _first(p, "phenotype_version_id", "version_id", "history_id"),
        "name": _first(p, "name", "title", "phenotype_name"),
        "author": _first(p, "author", "authors"),
        "coding_systems": coding,
        "definition": (_first(p, "definition", "description", default="") or "")[:600],
    }


def _code(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": _first(c, "code", "concept_code"),
        "description": _first(c, "description", "code_description", "term"),
        "coding_system": _first(c, "coding_system", "coding_system_name", "coding_system_description"),
    }


class PhenotypeClient:
    def __init__(self, base_url: str | None = None, fixtures: str | None = None,
                 token: str | None = None, timeout: float = 20.0, transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or os.environ.get("PHENOTYPE_API_BASE",
                         "https://phenotypes.healthdatagateway.org/api/v1")).rstrip("/")
        self.search_path = os.environ.get("PHENOTYPE_SEARCH_PATH", "/phenotypes/?search={query}&format=json")
        self.detail_path = os.environ.get("PHENOTYPE_DETAIL_PATH", "/phenotypes/{id}/detail/?format=json")
        self.version_detail_path = os.environ.get(
            "PHENOTYPE_VERSION_DETAIL_PATH", "/phenotypes/{id}/version/{version}/detail/?format=json")
        self.codes_path = os.environ.get("PHENOTYPE_CODES_PATH", "/phenotypes/{id}/export/codes/?format=json")
        self.version_codes_path = os.environ.get(
            "PHENOTYPE_VERSION_CODES_PATH", "/phenotypes/{id}/version/{version}/export/codes/?format=json")
        fixtures = os.environ.get("PHENOTYPE_FIXTURES") if fixtures is None else fixtures  # "" disables
        self.fixtures = json.loads(Path(fixtures).read_text())["phenotypes"] if fixtures else None
        headers = {"Accept": "application/json"}
        token = token or os.environ.get("PHENOTYPE_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)
        self._cache: dict[str, tuple[float, Any]] = {}

    def _get(self, path: str) -> Any:
        hit = self._cache.get(path)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        resp = self.http.get(path)
        resp.raise_for_status()
        data = resp.json()
        self._cache[path] = (time.time(), data)
        return data

    # Fixture mode ---------------------------------------------------------------
    def _fixture(self, phenotype_id: str) -> dict[str, Any]:
        for p in self.fixtures or []:
            if p["phenotype_id"].lower() == phenotype_id.lower():
                return p
        raise KeyError(f"Phenotype {phenotype_id} not found")

    # Public API -------------------------------------------------------------------
    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if self.fixtures is not None:
            q = query.lower()
            words = [w for w in q.split() if len(w) > 2]
            scored = []
            for p in self.fixtures:
                hay = " ".join([p["name"], p.get("definition", ""), *p.get("keywords", [])]).lower()
                score = (3 if q in hay else 0) + sum(w in hay for w in words)
                if score:
                    scored.append((score, p))
            scored.sort(key=lambda s: -s[0])
            return [_summary(p) | {"coding_systems": sorted({c["coding_system"] for c in p["codes"]})}
                    for _, p in scored[:limit]]
        data = self._get(self.search_path.format(query=quote_plus(query)))
        return [_summary(p) for p in _items(data)[:limit]]

    def detail(self, phenotype_id: str, version_id: int | str | None = None) -> dict[str, Any]:
        if self.fixtures is not None:
            p = self._fixture(phenotype_id)
            return _summary(p) | {"definition": p.get("definition", ""), "code_count": len(p["codes"])}
        path = (self.version_detail_path.format(id=phenotype_id, version=version_id) if version_id
                else self.detail_path.format(id=phenotype_id))
        items = _items(self._get(path))
        return _summary(items[0]) if items else {}

    def codes(self, phenotype_id: str, version_id: int | str | None = None,
              coding_system: str | None = None) -> dict[str, Any]:
        if self.fixtures is not None:
            p = self._fixture(phenotype_id)
            codes, version = [_code(c) for c in p["codes"]], p["version_id"]
        else:
            path = (self.version_codes_path.format(id=phenotype_id, version=version_id) if version_id
                    else self.codes_path.format(id=phenotype_id))
            data = self._get(path)
            codes = [_code(c) for c in _items(data)]
            version = version_id
        if coding_system:
            cs = coding_system.lower().replace(" ", "")
            codes = [c for c in codes if cs in (c["coding_system"] or "").lower().replace(" ", "")]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for c in codes:
            grouped.setdefault(c["coding_system"] or "unknown", []).append(
                {"code": c["code"], "description": c["description"]})
        return {"phenotype_id": phenotype_id, "version_id": version, "code_count": len(codes),
                "codes_by_system": grouped}
