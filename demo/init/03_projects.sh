#!/bin/bash
# Project views, roles and the gateway login used by the SQL MCP server.
# Runs from the Postgres image's docker-entrypoint-initdb.d, or by hand:
#   PGHOST=... PGUSER=postgres ./03_projects.sh
set -euo pipefail

GATEWAY_PASSWORD="${GATEWAY_PASSWORD:-gateway}"
PROFILER_PASSWORD="${PROFILER_PASSWORD:-profiler}"
DB="${POSTGRES_DB:-${PGDATABASE:-sail}}"

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-${PGUSER:-postgres}}" --dbname "$DB" \
     -v gw_pw="$GATEWAY_PASSWORD" -v prof_pw="$PROFILER_PASSWORD" <<'SQL'
-- No default access to the base SAIL schema
REVOKE ALL ON SCHEMA sail FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Project: demo (diabetes study) --------------------------------------------
CREATE SCHEMA proj_demo;
-- Views run with the owner's rights, so the project role never touches sail.* directly.
-- Data minimisation: LSOA is dropped; deprivation quintile is kept.
CREATE VIEW proj_demo.wdsd_ar_pers AS
    SELECT alf_pe, wob, gndr_cd, wimd_2019_quintile, ethn_cat FROM sail.wdsd_ar_pers;
CREATE VIEW proj_demo.wlgp_clean_gp_reg     AS SELECT * FROM sail.wlgp_clean_gp_reg;
CREATE VIEW proj_demo.wlgp_gp_event_cleansed AS SELECT * FROM sail.wlgp_gp_event_cleansed;
CREATE VIEW proj_demo.pedw_spell            AS SELECT * FROM sail.pedw_spell;
CREATE VIEW proj_demo.pedw_diag             AS SELECT * FROM sail.pedw_diag;
CREATE VIEW proj_demo.adde_deaths           AS SELECT * FROM sail.adde_deaths;
CREATE VIEW proj_demo.lkp_read_cd           AS SELECT * FROM sail.lkp_read_cd;

CREATE ROLE proj_demo_ro NOLOGIN;
GRANT USAGE ON SCHEMA proj_demo TO proj_demo_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA proj_demo TO proj_demo_ro;

-- Project: other (mortality only) - used to prove project isolation ---------
CREATE SCHEMA proj_other;
CREATE VIEW proj_other.adde_deaths AS SELECT * FROM sail.adde_deaths;
CREATE ROLE proj_other_ro NOLOGIN;
GRANT USAGE ON SCHEMA proj_other TO proj_other_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA proj_other TO proj_other_ro;

-- Gateway login: NOINHERIT, so it has no data rights until it SET ROLEs into a project
CREATE ROLE explorer_gateway LOGIN NOINHERIT PASSWORD :'gw_pw';
GRANT proj_demo_ro, proj_other_ro TO explorer_gateway;

-- Profiler login used by the demo catalog (stands in for the OpenMetadata profiler)
CREATE ROLE metadata_profiler LOGIN PASSWORD :'prof_pw';
GRANT USAGE ON SCHEMA sail TO metadata_profiler;
GRANT SELECT ON ALL TABLES IN SCHEMA sail TO metadata_profiler;
SQL
