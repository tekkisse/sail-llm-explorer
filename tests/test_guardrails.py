import pytest

from sailx.sql.guardrails import GuardrailError, check_sql

KW = dict(dialect="postgres", allowed_schemas=["proj_demo"], default_schema="proj_demo", max_rows=100)


def test_qualifies_unqualified_tables_and_caps_rows():
    q = check_sql("select gndr_cd, count(distinct alf_pe) as n from wdsd_ar_pers group by gndr_cd;", **KW)
    assert q.tables == ["proj_demo.wdsd_ar_pers"]
    assert "proj_demo.wdsd_ar_pers" in q.sql
    assert q.wrapped_sql.endswith("LIMIT 101")


def test_cte_names_are_not_treated_as_tables():
    q = check_sql("with dm as (select alf_pe from wlgp_gp_event_cleansed where event_cd in ('C10F.')) "
                  "select count(distinct alf_pe) as n from dm", **KW)
    assert q.tables == ["proj_demo.wlgp_gp_event_cleansed"]


@pytest.mark.parametrize("sql", [
    "delete from wdsd_ar_pers",
    "update wdsd_ar_pers set gndr_cd = 1",
    "insert into wdsd_ar_pers values (1)",
    "drop table wdsd_ar_pers",
    "select count(*) as n from a; delete from b",
    "create table x as select 1",
    "select count(*) as n into newtab from wdsd_ar_pers",
    "vacuum",
])
def test_rejects_non_select(sql):
    with pytest.raises(GuardrailError):
        check_sql(sql, **KW)


def test_rejects_other_schemas():
    with pytest.raises(GuardrailError, match="outside this project"):
        check_sql("select count(*) as n from sail.wdsd_ar_pers", **KW)
    with pytest.raises(GuardrailError, match="outside this project"):
        check_sql("select count(*) as n from pg_catalog.pg_roles", **KW)


def test_rejects_dangerous_functions():
    with pytest.raises(GuardrailError, match="not allowed"):
        check_sql("select count(*) as n, pg_sleep(10) from wdsd_ar_pers", **KW)
    with pytest.raises(GuardrailError, match="not allowed"):
        check_sql("select count(*) as n from wdsd_ar_pers where current_setting('x') = 'y'", **KW)


def test_requires_aggregation():
    with pytest.raises(GuardrailError, match="aggregated"):
        check_sql("select alf_pe, wob from wdsd_ar_pers", **KW)
    with pytest.raises(GuardrailError, match="aggregated"):
        check_sql("select alf_pe, count(*) over () from wdsd_ar_pers", **KW)
    check_sql("select count(*) as n from wdsd_ar_pers union all select count(*) as n from adde_deaths", **KW)


def test_aggregation_can_be_disabled():
    check_sql("select gndr_cd from wdsd_ar_pers", **(KW | {"require_aggregation": False}))


def test_db2_uses_fetch_first():
    q = check_sql("select count(*) as n from wdsd_ar_pers", **(KW | {"dialect": "db2"}))
    assert q.wrapped_sql.endswith("FETCH FIRST 101 ROWS ONLY")
