"""Builds Plotly figures from disclosure-controlled query results."""
from __future__ import annotations

import json
from typing import Any

import plotly.graph_objects as go

CHART_TOOL = {
    "type": "function",
    "function": {
        "name": "create_chart",
        "description": (
            "Draw a chart from a query result you already ran. Use a bar chart to compare groups, "
            "a line chart for trends over time, a scatter chart for two numeric measures. "
            "Suppressed cells are left as gaps."),
        "parameters": {
            "type": "object",
            "properties": {
                "query_id": {"type": "string", "description": "query_id returned by run_query"},
                "chart_type": {"type": "string", "enum": ["bar", "line", "scatter"]},
                "x": {"type": "string", "description": "column for the x axis"},
                "y": {"type": "string", "description": "column for the y axis (a measure)"},
                "series": {"type": "string", "description": "optional column that splits the data into series"},
                "title": {"type": "string", "description": "the finding, as a short sentence"},
            },
            "required": ["query_id", "chart_type", "x", "y", "title"],
        },
    },
}


class ChartError(ValueError):
    pass


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None   # e.g. "<10" suppressed


def build_chart(result: dict[str, Any], chart_type: str, x: str, y: str, title: str,
                series: str | None = None) -> dict[str, Any]:
    columns = [c.lower() for c in result["columns"]]
    for col in filter(None, (x, y, series)):
        if col.lower() not in columns:
            raise ChartError(f"Column {col!r} is not in the result. Available: {', '.join(result['columns'])}")
    xi, yi = columns.index(x.lower()), columns.index(y.lower())
    si = columns.index(series.lower()) if series else None

    groups: dict[str, tuple[list[Any], list[Any]]] = {}
    for row in result["rows"]:
        key = str(row[si]) if si is not None else y
        xs, ys = groups.setdefault(key, ([], []))
        xs.append(row[xi])
        ys.append(_num(row[yi]))

    fig = go.Figure()
    for name, (xs, ys) in groups.items():
        if chart_type == "bar":
            fig.add_trace(go.Bar(x=xs, y=ys, name=name))
        elif chart_type == "line":
            fig.add_trace(go.Scatter(x=xs, y=ys, name=name, mode="lines+markers", connectgaps=False))
        elif chart_type == "scatter":
            fig.add_trace(go.Scatter(x=xs, y=ys, name=name, mode="markers"))
        else:
            raise ChartError(f"Unsupported chart type {chart_type!r}")
    suppressed = any(v is None for _, ys in groups.values() for v in ys)
    fig.update_layout(
        title=title, xaxis_title=x, yaxis_title=y, template="plotly_white",
        barmode="group", showlegend=si is not None, margin=dict(t=60, l=60, r=20, b=60),
    )
    if suppressed:
        fig.add_annotation(text="Gaps are suppressed small counts", xref="paper", yref="paper",
                           x=0, y=-0.18, showarrow=False, font=dict(size=11))
    return json.loads(fig.to_json()) | {"_meta": {"query_id": result.get("query_id"), "title": title}}
