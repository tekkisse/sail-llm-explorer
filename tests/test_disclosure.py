import pytest

from sailx.config import SdcSettings
from sailx.sql.disclosure import DisclosureError, apply_sdc

SDC = SdcSettings(threshold=10, rounding=5, require_count=True,
                  identifier_columns=["alf_pe", "spell_num_pe"])


def test_suppresses_small_counts_and_rounds_others():
    r = apply_sdc(["wimd_2019_quintile", "n"], [[1, 1234], [2, 7], [3, 0]], SDC)
    assert r.rows == [[1, 1235], [2, "<10"], [3, 0]]
    assert r.suppressed_cells == 1
    assert r.count_columns == ["n"]


def test_secondary_suppression_blanks_measures_but_keeps_keys():
    r = apply_sdc(["year", "n", "avg_hba1c"], [[2020, 5, 61.2], [2021, 40, 58.0]], SDC)
    assert r.rows[0] == [2020, "<10", None]
    assert r.rows[1] == [2021, 40, 58.0]


def test_refuses_identifiers():
    with pytest.raises(DisclosureError, match="identifier"):
        apply_sdc(["alf_pe", "n"], [[1, 20]], SDC)


def test_refuses_results_without_count_in_strict_mode():
    with pytest.raises(DisclosureError, match="No count column"):
        apply_sdc(["gndr_cd", "avg_age"], [[1, 50.1]], SDC)


def test_lenient_mode_warns():
    lenient = SdcSettings(threshold=10, rounding=5, require_count=False, identifier_columns=[])
    r = apply_sdc(["gndr_cd", "avg_age"], [[1, 50.1]], lenient)
    assert r.notes and "not checked" in r.notes[0]


def test_decimal_and_dates_become_json_values():
    from datetime import date
    from decimal import Decimal

    r = apply_sdc(["month", "n"], [[date(2024, 1, 1), Decimal("20")]], SDC)
    assert r.rows == [["2024-01-01", 20]]
