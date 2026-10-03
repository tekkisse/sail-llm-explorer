"""Statistical disclosure control applied to every result before the model or user sees it.

Rules (configurable):
  1. Results containing person-level identifier columns are refused outright.
  2. Count-like columns: values 1..threshold-1 are suppressed ("<10"); others rounded.
  3. Secondary: when a count in a row is suppressed, other numeric values in that row
     (means, medians, sums over the same small group) are suppressed too.
  4. If no count column can be found, group sizes are unknown and the result is refused
     (strict mode), so the model is told to add COUNT(DISTINCT alf_pe) AS n.

This is a practical first line, not a replacement for output checking at egress.
Complementary (differencing) disclosure across queries is NOT handled here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..config import SdcSettings

COUNT_COLUMN = re.compile(
    r"(^n$|^n_|_n$|count|^num_|_num$|patients|people|persons|individuals|cases|events|admissions|"
    r"spells|deaths|registrations|frequency|^freq|denominator|numerator)",
    re.IGNORECASE,
)


# Numeric columns that are usually grouping keys, not statistics about the group.
KEY_COLUMN = re.compile(
    r"^(year|yr|month|quarter|week|.*_year|.*_month|.*quintile|.*decile|.*_band|.*_group|.*_cd|.*code|.*_id|id|sex)$",
    re.IGNORECASE,
)
MEASURE_COLUMN = re.compile(r"(avg|mean|median|sum|total|min|max|pct|percent|rate|prop|ratio|sd|std)", re.IGNORECASE)


def _is_key(name: str) -> bool:
    return bool(KEY_COLUMN.search(name)) and not MEASURE_COLUMN.search(name)


class DisclosureError(ValueError):
    """Raised with a message written for the model."""


@dataclass
class SdcResult:
    columns: list[str]
    rows: list[list[Any]]
    suppressed_cells: int = 0
    count_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _round(value: float, base: int) -> int | float:
    if base <= 1:
        return value
    return int(base * round(float(value) / base))


def apply_sdc(columns: list[str], rows: list[list[Any]], settings: SdcSettings) -> SdcResult:
    lowered = [c.lower() for c in columns]
    identifiers = {c.lower() for c in settings.identifier_columns}
    leaked = [c for c in lowered if c in identifiers]
    if leaked:
        raise DisclosureError(
            f"The result contains identifier column(s) {', '.join(leaked)}. Aggregate the data instead; "
            "identifiers are never returned.")

    rows = [[_jsonable(v) for v in row] for row in rows]
    count_idx = [i for i, c in enumerate(lowered) if COUNT_COLUMN.search(c)]
    numeric_idx = [i for i in range(len(columns))
                   if not _is_key(lowered[i])
                   and any(isinstance(r[i], (int, float)) and not isinstance(r[i], bool) for r in rows)]

    if not count_idx and rows:
        if settings.require_count:
            raise DisclosureError(
                "No count column was found, so group sizes cannot be checked. Add a person count, "
                "for example COUNT(DISTINCT alf_pe) AS n, to every group.")
        return SdcResult(columns, rows, notes=["Warning: no count column; group sizes were not checked."])

    label = f"<{settings.threshold}"
    suppressed = 0
    out_rows = []
    for row in rows:
        row = list(row)
        small = False
        for i in count_idx:
            v = row[i]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if 0 < v < settings.threshold:
                    row[i] = label
                    small = True
                    suppressed += 1
                else:
                    row[i] = _round(v, settings.rounding)
        if small:
            for i in numeric_idx:
                if i not in count_idx and isinstance(row[i], (int, float)):
                    row[i] = None
                    suppressed += 1
        out_rows.append(row)

    notes = []
    if suppressed:
        notes.append(f"{suppressed} cell(s) suppressed: counts below {settings.threshold}, and other values in those rows.")
    if settings.rounding > 1:
        notes.append(f"Counts rounded to the nearest {settings.rounding}.")
    return SdcResult(columns, out_rows, suppressed, [columns[i] for i in count_idx], notes)
