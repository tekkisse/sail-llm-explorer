"""Connectivity check, run from inside the stack:

    docker compose exec ui python -m sailx.check

Checks the LLM endpoint (and that the model exists), each MCP server, and the service secret.
"""
from __future__ import annotations

import sys
from urllib.parse import urlsplit

import httpx

from .config import AgentSettings, IdentitySettings
from .identity import MIN_SECRET_LENGTH

OK, FAIL, WARN = "OK  ", "FAIL", "WARN"


def _health_url(mcp_url: str) -> str:
    parts = urlsplit(mcp_url)
    return f"{parts.scheme}://{parts.netloc}/healthz"


def check_llm(s: AgentSettings) -> bool:
    url = s.llm_base_url.rstrip("/") + "/models"
    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {s.llm_api_key}"}, timeout=10)
    except httpx.ConnectTimeout:
        print(f"{FAIL} LLM {s.llm_base_url}: connection timed out. The host is unreachable from this container "
              "(wrong address, firewall, or the server only listens on 127.0.0.1).")
        return False
    except httpx.ConnectError as exc:
        print(f"{FAIL} LLM {s.llm_base_url}: cannot connect ({exc}). Is the model server running? "
              "If it is the compose 'ollama' service, start it with --profile local-llm.")
        return False
    except httpx.HTTPError as exc:
        print(f"{FAIL} LLM {s.llm_base_url}: {type(exc).__name__}: {exc}")
        return False
    if resp.status_code != 200:
        print(f"{FAIL} LLM {url}: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    models = [m.get("id") for m in resp.json().get("data", [])]
    if s.llm_model not in models:
        print(f"{WARN} LLM reachable, but model {s.llm_model!r} is not listed. Available: {', '.join(models) or 'none'}"
              f"{' (for Ollama: ollama pull ' + s.llm_model + ')' if 'ollama' in s.llm_base_url or ':11434' in s.llm_base_url else ''}")
        return False
    print(f"{OK} LLM {s.llm_base_url} serves {s.llm_model}")
    return True


def check_mcp(name: str, url: str) -> bool:
    try:
        resp = httpx.get(_health_url(url), timeout=5)
        resp.raise_for_status()
        print(f"{OK} {name} {url}")
        return True
    except httpx.HTTPError as exc:
        # OpenMetadata's MCP endpoint has no /healthz; treat a reachable host as a pass.
        if isinstance(exc, httpx.HTTPStatusError):
            print(f"{OK} {name} {url} (reachable; no /healthz)")
            return True
        print(f"{FAIL} {name} {url}: {type(exc).__name__}: {exc}")
        return False


def check_secret(i: IdentitySettings) -> bool:
    if i.jwks_url:
        print(f"{OK} service tokens validated via JWKS {i.jwks_url}")
        return True
    secret = i.shared_secret or ""
    if len(secret.encode()) < MIN_SECRET_LENGTH:
        print(f"{FAIL} SERVICE_JWT_SECRET is {len(secret.encode())} bytes; it must be at least {MIN_SECRET_LENGTH}. "
              "Generate one with: openssl rand -hex 32")
        return False
    print(f"{OK} SERVICE_JWT_SECRET length")
    return True


def main() -> int:
    s, i = AgentSettings(), IdentitySettings()
    results = [
        check_secret(i),
        check_mcp("metadata MCP", s.metadata_mcp_url),
        check_mcp("phenotype MCP", s.phenotype_mcp_url),
        check_mcp("SQL MCP", s.sql_mcp_url),
        check_llm(s),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
