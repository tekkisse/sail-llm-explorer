"""Shared fixtures. Integration tests need the demo Postgres database:

    TEST_PG_DSN=postgresql://postgres@127.0.0.1:5432/sail pytest

The database must be loaded with demo/init/*.sql and 03_projects.sh.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADMIN_DSN = os.environ.get("TEST_PG_DSN", "postgresql://postgres@127.0.0.1:5432/sail")


def _pg_available() -> bool:
    try:
        import psycopg

        with psycopg.connect(ADMIN_DSN, connect_timeout=2) as conn:
            conn.execute("SELECT 1 FROM sail.wdsd_ar_pers LIMIT 1")
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _pg_available(), reason="demo Postgres not available (set TEST_PG_DSN)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def demo_env():
    host = ADMIN_DSN.split("@", 1)[1]
    env = {
        "SQL_DSN": f"postgresql://explorer_gateway:gateway@{host}",
        "PROFILE_DSN": f"postgresql://metadata_profiler:profiler@{host}",
        "PROJECTS_FILE": str(ROOT / "demo/projects.yaml"),
        "CATALOG_FILE": str(ROOT / "demo/catalog.yaml"),
        "PHENOTYPE_FIXTURES": str(ROOT / "demo/phenotypes.json"),
        "SERVICE_JWT_SECRET": "test-secret-at-least-32-bytes-long!!",
        "AUDIT_LOG": str(ROOT / "tests/.audit.jsonl"),
        "REQUIRE_SQL_APPROVAL": "true",
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    yield env
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session")
def mcp_servers(demo_env):
    """Start the catalogue, phenotype and SQL MCP servers on free ports."""
    import uvicorn

    from sailx.catalog import server as catalog_server
    from sailx.phenotype import server as phenotype_server
    from sailx.sql import server as sql_server

    servers, urls = [], {}
    for name, module in (("meta", catalog_server), ("pheno", phenotype_server), ("sql", sql_server)):
        port = _free_port()
        config = uvicorn.Config(module.mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning")
        srv = uvicorn.Server(config)
        threading.Thread(target=srv.run, daemon=True).start()
        servers.append(srv)
        urls[name] = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 15
    while time.time() < deadline and not all(s.started for s in servers):
        time.sleep(0.1)
    yield urls
    for s in servers:
        s.should_exit = True
