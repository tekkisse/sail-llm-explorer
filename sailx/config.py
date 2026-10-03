"""Settings, read from environment variables. See README "Configuration"."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = _env(name)
    return default if value is None else int(value)


def _list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in (_env(name, default) or "").split(",") if v.strip()]


@dataclass
class SqlSettings:
    backend: str = field(default_factory=lambda: _env("SQL_BACKEND", "postgres"))
    dsn: str = field(default_factory=lambda: _env("SQL_DSN", "postgresql://explorer_gateway:gateway@localhost:5432/sail"))
    dialect: str = field(default_factory=lambda: _env("SQL_DIALECT", "postgres"))
    max_rows: int = field(default_factory=lambda: _int("SQL_MAX_ROWS", 1000))
    timeout_ms: int = field(default_factory=lambda: _int("SQL_TIMEOUT_MS", 30000))
    max_cost: float = field(default_factory=lambda: float(_env("SQL_MAX_COST", "5000000")))
    require_aggregation: bool = field(default_factory=lambda: _bool("SQL_REQUIRE_AGGREGATION", True))
    audit_log: str = field(default_factory=lambda: _env("AUDIT_LOG", "-"))
    projects_file: str = field(default_factory=lambda: _env("PROJECTS_FILE", "demo/projects.yaml"))
    db2_credentials_dir: str = field(default_factory=lambda: _env("DB2_CREDENTIALS_DIR", "/var/run/secrets/sailx-db2"))


@dataclass
class SdcSettings:
    threshold: int = field(default_factory=lambda: _int("SDC_THRESHOLD", 10))
    rounding: int = field(default_factory=lambda: _int("SDC_ROUNDING", 5))
    require_count: bool = field(default_factory=lambda: _bool("SDC_REQUIRE_COUNT", True))
    identifier_columns: list[str] = field(default_factory=lambda: _list(
        "SDC_IDENTIFIER_COLUMNS", "alf_pe,alf_e,ralf_pe,spell_num_pe,prac_cd_pe,lsoa2011_cd,nhs_number"))


@dataclass
class IdentitySettings:
    # HS256 shared secret between agent and SQL MCP server (demo / simple deployments).
    shared_secret: str | None = field(default_factory=lambda: _env("SERVICE_JWT_SECRET"))
    # Or validate RS256 tokens from your identity provider via JWKS.
    jwks_url: str | None = field(default_factory=lambda: _env("SERVICE_JWKS_URL"))
    issuer: str = field(default_factory=lambda: _env("SERVICE_JWT_ISSUER", "sailx-agent"))
    audience: str = field(default_factory=lambda: _env("SERVICE_JWT_AUDIENCE", "sailx-sql"))
    ttl_seconds: int = field(default_factory=lambda: _int("SERVICE_JWT_TTL", 300))


@dataclass
class AgentSettings:
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "http://localhost:8000/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", "not-needed"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "Qwen/Qwen2.5-32B-Instruct"))
    llm_temperature: float = field(default_factory=lambda: float(_env("LLM_TEMPERATURE", "0.1")))
    llm_timeout_seconds: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_SECONDS", "180")))
    llm_connect_timeout_seconds: float = field(default_factory=lambda: float(_env("LLM_CONNECT_TIMEOUT_SECONDS", "10")))
    llm_max_retries: int = field(default_factory=lambda: _int("LLM_MAX_RETRIES", 1))
    max_steps: int = field(default_factory=lambda: _int("AGENT_MAX_STEPS", 16))
    require_sql_approval: bool = field(default_factory=lambda: _bool("REQUIRE_SQL_APPROVAL", True))
    metadata_mcp_url: str = field(default_factory=lambda: _env("METADATA_MCP_URL", "http://localhost:8101/mcp"))
    metadata_mcp_token: str | None = field(default_factory=lambda: _env("METADATA_MCP_TOKEN"))
    # Write tools on the OpenMetadata MCP server are never exposed to the model.
    metadata_tool_allowlist: list[str] = field(default_factory=lambda: _list("METADATA_TOOL_ALLOWLIST"))
    metadata_tool_denylist: list[str] = field(default_factory=lambda: _list(
        "METADATA_TOOL_DENYLIST", "create,update,patch,delete,add,remove,put,set,write"))
    phenotype_mcp_url: str = field(default_factory=lambda: _env("PHENOTYPE_MCP_URL", "http://localhost:8102/mcp"))
    sql_mcp_url: str = field(default_factory=lambda: _env("SQL_MCP_URL", "http://localhost:8103/mcp"))
    sql_dialect_name: str = field(default_factory=lambda: _env("SQL_DIALECT_NAME", "PostgreSQL"))
    web_ui_url: str = field(default_factory=lambda: _env("WEB_UI_URL", "http://localhost:8000"))
    api_key: str | None = field(default_factory=lambda: _env("AGENT_API_KEY"))
    projects_file: str = field(default_factory=lambda: _env("PROJECTS_FILE", "demo/projects.yaml"))
