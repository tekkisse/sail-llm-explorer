# SAIL Data Explorer

An LLM-driven explorer for SAIL datasets. Researchers ask in plain English about **what is in the data** (answered from metadata) or ask **data questions** (answered by governed text-to-SQL inside the TRE, with disclosure control and charts).

This is a reference implementation that runs end to end on synthetic data. Point it at OpenMetadata, the HDR UK Phenotype Library and SAIL DB2 to go further (see [Going to production](#going-to-production)).

## Architecture

```
 Slack ──► n8n ──► agent-api ─┐                 (discovery only: Slack is outside the TRE)
                              ├──► LLM (self-hosted, OpenAI-compatible)
 Browser ──► ui (Chainlit) ───┤
   (inside the TRE)           ├──► metadata MCP   OpenMetadata MCP, or catalog-mcp (demo)
                              ├──► phenotype-mcp  HDR UK Phenotype Library
                              └──► sql-mcp ──────► research DB (project views only)
                                     guardrails · project role · disclosure control · audit
```

| Component | What it does | Code |
| --- | --- | --- |
| `ui` | Chainlit chat app: shows each step, the SQL for approval, results, charts and phenotypes used | `ui/app.py` |
| `agent-api` | HTTP endpoint for n8n/Slack; discovery mode unless the project allows Slack queries | `sailx/agent/api.py` |
| Agent | Tool loop over the MCP servers; approval pause/resume; charts | `sailx/agent/` |
| `sql-mcp` | The only component that touches data. Verifies the caller, checks project membership, runs guardrails, `SET ROLE` into the project, applies disclosure control, writes audit | `sailx/sql/` |
| `phenotype-mcp` | Search phenotypes, get definitions and versioned code lists | `sailx/phenotype/` |
| `catalog-mcp` | Demo stand-in for OpenMetadata: descriptions, known issues, live column profiles | `sailx/catalog/` |

## Quick start (Docker Compose, synthetic data)

```bash
cp .env.example .env                      # change the secrets
docker compose --profile local-llm up -d --build
docker compose exec ollama ollama pull qwen2.5:14b-instruct
```

Open http://localhost:8000 and sign in as `alice@example.org` / `alice-demo`. Try:

- "What datasets hold information on smoking?"
- "How complete is ethnicity recording?"
- "How does type 2 diabetes vary by deprivation?" (you'll be asked to approve the SQL)

The demo database has about 20,000 synthetic people, with SAIL-style tables (`WDSD_AR_PERS`, `WLGP_GP_EVENT_CLEANSED`, `WLGP_CLEAN_GP_REG`, `PEDW_SPELL`, `PEDW_DIAG`, `ADDE_DEATHS`) and deliberate quality issues (placeholder birth dates, BMI of 999, missing ethnicity) so the metadata has something to say. `bob@example.org` belongs to a second project that can only see deaths, which demonstrates project isolation.

**No GPU?** `scripts/fake_llm.py` is a scripted OpenAI-compatible endpoint that answers the diabetes question by calling the real tools. It's for smoke-testing the UI, approvals, SQL and charts, not for real questions:

```bash
uvicorn scripts.fake_llm:app --host 0.0.0.0 --port 9000   # then LLM_BASE_URL=http://<host>:9000/v1
```

## Troubleshooting

Run the connectivity check from inside the stack. It tests the service secret, each MCP server, and the LLM endpoint and model:

```bash
docker compose exec ui python -m sailx.check
```

**`Request timed out` / `ConnectTimeout` calling the LLM.** The UI container can't open a connection to `LLM_BASE_URL`. Remember that `localhost` inside a container is the container itself.

- **Compose Ollama:** start it with `--profile local-llm`, use `LLM_BASE_URL=http://ollama:11434/v1`, and `ollama pull` the model.
- **Ollama on the Windows or Mac host:** use `LLM_BASE_URL=http://host.docker.internal:11434/v1`. Ollama listens on 127.0.0.1 by default, so set the `OLLAMA_HOST=0.0.0.0:11434` environment variable, restart Ollama, and allow port 11434 through the firewall.
- **vLLM elsewhere on the network:** check that the container can route to it and that no proxy is in the way.

**`SERVICE_JWT_SECRET must be at least 32 bytes`.** Generate one with `openssl rand -hex 32`.

## Running without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[ui,dev]"
createdb sail && psql -d sail -f demo/init/01_schema.sql -f demo/init/02_data.sql
PGDATABASE=sail ./demo/init/03_projects.sh
```

Then start each component (each in its own shell, with the variables from `.env.example` adjusted for localhost):

```bash
MCP_PORT=8101 CATALOG_FILE=demo/catalog.yaml PROFILE_DSN=postgresql://metadata_profiler:profiler@localhost/sail sailx-catalog-mcp
MCP_PORT=8102 PHENOTYPE_FIXTURES=demo/phenotypes.json sailx-phenotype-mcp
MCP_PORT=8103 SQL_DSN=postgresql://explorer_gateway:gateway@localhost/sail sailx-sql-mcp
chainlit run ui/app.py --port 8000
```

## Tests

```bash
TEST_PG_DSN=postgresql://postgres@localhost:5432/sail pytest
```

35 tests cover the guardrails, disclosure control, identity tokens, phenotype client, catalogue profiling, and end-to-end agent runs against the real MCP servers and database using a scripted LLM. Those end-to-end runs cover approval, declining a query, the model correcting a refused query, Slack discovery mode, project isolation enforced by the database itself, and token checks. Integration tests are skipped if the database isn't available.

## Security model

Controls are layered, so a failure in one is caught by the next:

1. **Identity.** The agent holds no database credentials. Each SQL call carries a short-lived token (HS256 shared secret, or RS256 via `SERVICE_JWKS_URL`) naming the user and project. `sql-mcp` verifies the token and checks membership against the project registry itself.
2. **Static guardrails** (`sailx/sql/guardrails.py`): one statement only, `SELECT` only, no DDL/DML/`SELECT INTO`/locking, no `pg_*`/`dblink`-type functions, tables restricted to the project's schemas (unqualified names are resolved to the project schema), results must be aggregated, and a row cap is applied. Refusals are written for the model so it can correct itself.
3. **Database enforcement.** The gateway login is `NOINHERIT` with no rights of its own. Each query runs in a `READ ONLY` transaction after `SET LOCAL ROLE <project role>`, with a statement timeout and an `EXPLAIN` cost limit. Project roles can only see their project's views. The tests bypass the guardrails to prove the database still refuses.
4. **Disclosure control** (`sailx/sql/disclosure.py`):
   - results containing identifier columns are refused;
   - counts below 10 are suppressed and other counts are rounded to 5;
   - other statistics in a suppressed row are blanked;
   - results with no count column are refused, so group sizes can always be checked.
5. **Human approval.** With `REQUIRE_SQL_APPROVAL=true` every query is shown and must be approved before it runs.
6. **Channel rules.** Slack gets no SQL tools unless `allow_slack_queries` is set for the project. Write tools on the metadata server are filtered out before the model sees them.
7. **Audit.** Every query, refusal and Slack question is logged as JSON lines (`AUDIT_LOG`, stdout by default) with user, project, purpose, SQL, tables, cost, rows and suppressed cells.
8. **Network policies** (Helm): only the UI and agent API can reach the MCP servers, and `sql-mcp` can reach only DNS and the database.

**Not covered:** differencing attacks across several queries, and output checking for anything leaving the TRE. Exports still go through Airlock/Egress review.

## Going to production

- **OpenMetadata.** Set `METADATA_MCP_URL` to your OpenMetadata MCP endpoint and `METADATA_MCP_TOKEN` to a bot or personal access token, then disable `catalog-mcp`. Tools are discovered at runtime; any tool whose name contains create/update/patch/delete/add/remove/put/set/write is hidden. You can pin an exact list with `METADATA_TOOL_ALLOWLIST`. To load the demo database into OpenMetadata, use `openmetadata/*.yaml` (sample data is off) and `scripts/push_catalog_to_openmetadata.py`, which pushes the descriptions, known issues, PII tags and glossary.
- **DB2.** Set `SQL_BACKEND=db2`, `SQL_DIALECT=db2`, `SQL_DIALECT_NAME="IBM DB2 11.5"`, and install `.[db2]`. Give each project read-only credentials mounted at `DB2_CREDENTIALS_DIR/<db_credentials_secret>/{username,password}`. Enforce cost and time limits with DB2 Workload Manager thresholds on the project users. **The DB2 backend is untested.** sqlglot has no DB2 dialect, so DB2 SQL is parsed as ANSI SQL; test the guardrails against real SAIL queries.
- **Phenotype Library.** Remove `PHENOTYPE_FIXTURES` and set `PHENOTYPE_API_BASE`. TREs usually have no internet access, so point it at an internal mirror. Endpoint paths are configurable (`PHENOTYPE_*_PATH`) and parsing tolerates different field names. Check them against the current API, and consider a dedicated MCP endpoint served by the library itself.
- **LLM.** Use any OpenAI-compatible endpoint inside the TRE, such as vLLM on the cluster. The model needs reliable tool calling, for example Qwen2.5 32B Instruct or larger, Llama 3.3 70B, or gpt-oss.
- **Web sign-in.** Run the UI behind your identity proxy with `UI_AUTH_MODE=header`, or use `UI_AUTH_MODE=oauth` with Chainlit's OAuth variables. Never use `password` outside a demo.
- **Project registry.** Generate `projects.yaml` from the SAIL project system: members, schemas, role or credentials, and Slack policy.
- **Helm.** Build and push the image, create the `sail-explorer` secret (see `values.yaml`), set `networkPolicy` CIDRs, then `helm install explorer deploy/helm/sail-explorer -f my-values.yaml`. The chart has not been linted here; run `helm lint` and `helm template` first.
- **Slack.** Import `n8n/slack-intake.json` into n8n. Create Slack bot credentials (scopes: `app_mentions:read`, `chat:write`, `users:read`, `users:read.email`) and an HTTP header credential (`X-API-Key`). Check each node after import, because node parameters vary between n8n versions.

## Configuration

| Variable | Default | Used by |
| --- | --- | --- |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | `http://localhost:8000/v1`, Qwen2.5-32B | agent |
| `METADATA_MCP_URL`, `METADATA_MCP_TOKEN` | demo catalogue | agent |
| `METADATA_TOOL_ALLOWLIST` / `_DENYLIST` | deny write words | agent |
| `PHENOTYPE_MCP_URL`, `SQL_MCP_URL` | localhost | agent |
| `REQUIRE_SQL_APPROVAL` | `true` | agent |
| `SERVICE_JWT_SECRET` or `SERVICE_JWKS_URL` | required | agent, sql-mcp |
| `SQL_BACKEND`, `SQL_DSN`, `SQL_DIALECT` | postgres | sql-mcp |
| `SQL_MAX_ROWS`, `SQL_TIMEOUT_MS`, `SQL_MAX_COST` | 1000, 30000, 5e6 | sql-mcp |
| `SQL_REQUIRE_AGGREGATION` | `true` | sql-mcp |
| `SDC_THRESHOLD`, `SDC_ROUNDING`, `SDC_REQUIRE_COUNT` | 10, 5, true | sql-mcp |
| `SDC_IDENTIFIER_COLUMNS` | ALF, spell, practice, LSOA… | sql-mcp |
| `PROJECTS_FILE` | `demo/projects.yaml` | all |
| `AUDIT_LOG` | `-` (stdout) | sql-mcp, agent-api |
| `PHENOTYPE_API_BASE`, `PHENOTYPE_FIXTURES`, `PHENOTYPE_API_TOKEN` | public API | phenotype-mcp |
| `CATALOG_FILE`, `PROFILE_DSN` | demo | catalog-mcp |
| `UI_AUTH_MODE`, `UI_AUTH_HEADER`, `UI_ALLOWED_EMAIL_DOMAINS` | password | ui |
| `AGENT_API_KEY`, `WEB_UI_URL` | — | agent-api |

## Repository layout

```
sailx/            Python package (agent, MCP servers, guardrails, disclosure control)
ui/app.py         Chainlit web app
demo/             synthetic database, catalogue, projects, phenotype fixtures
deploy/helm/      Kubernetes chart with network policies
n8n/              Slack intake workflow
openmetadata/     ingestion and profiler configs
scripts/          OpenMetadata push script, fake LLM
tests/            unit and end-to-end tests
```
