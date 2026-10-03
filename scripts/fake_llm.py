"""A scripted, OpenAI-compatible fake LLM for smoke-testing the stack without a GPU.

It always answers one canned question shape (a condition by deprivation) by calling the real
tools in order, so you can check the UI, approvals, SQL, disclosure control and charts work.

    uvicorn scripts.fake_llm:app --port 9000
    LLM_BASE_URL=http://localhost:9000/v1 LLM_MODEL=fake ...
"""
from __future__ import annotations

import json
import time
import uuid

from fastapi import FastAPI, Request

app = FastAPI()

SQL = (
    "SELECT p.wimd_2019_quintile, COUNT(DISTINCT p.alf_pe) AS n\n"
    "FROM wdsd_ar_pers p\nJOIN wlgp_gp_event_cleansed e ON e.alf_pe = p.alf_pe\n"
    "WHERE e.event_cd IN ({codes})\nGROUP BY p.wimd_2019_quintile\nORDER BY p.wimd_2019_quintile"
)


def _reply(content=None, tool=None, args=None):
    msg = {"role": "assistant", "content": content}
    if tool:
        msg["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                              "function": {"name": tool, "arguments": json.dumps(args or {})}}]
    return {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion", "created": int(time.time()),
            "model": "fake", "choices": [{"index": 0, "message": msg,
                                          "finish_reason": "tool_calls" if tool else "stop"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body["messages"]
    tools = {t["function"]["name"] for t in body.get("tools", [])}
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    turn = messages[last_user + 1:]
    called = [tc["function"]["name"] for m in turn if m["role"] == "assistant" for tc in m.get("tool_calls") or []]
    results = [json.loads(m["content"]) if m["content"].startswith("{") else m["content"]
               for m in turn if m["role"] == "tool"]

    if "meta__search_metadata" not in called:
        return _reply(tool="meta__search_metadata", args={"query": "diabetes deprivation"})
    if "sql__run_query" not in tools:
        return _reply("GP data (WLGP) records type 2 diabetes with Read codes, and WDSD holds deprivation "
                      "quintiles. Data queries need the web explorer inside the TRE.")
    if "pheno__search_phenotypes" not in called:
        return _reply(tool="pheno__search_phenotypes", args={"query": "type 2 diabetes"})
    if "pheno__get_phenotype_codes" not in called:
        pid = results[-1]["results"][0]["phenotype_id"]
        return _reply(tool="pheno__get_phenotype_codes", args={"phenotype_id": pid, "coding_system": "Read"})
    if "sql__run_query" not in called:
        codes = [c["code"] for cs in results[-1]["codes_by_system"].values() for c in cs]
        return _reply(tool="sql__run_query", args={
            "sql": SQL.format(codes=", ".join(f"'{c}'" for c in codes)),
            "purpose": "People with type 2 diabetes by WIMD quintile"})
    last = results[-1]
    if "create_chart" not in called and isinstance(last, dict) and last.get("ok"):
        return _reply(tool="create_chart", args={"query_id": last["query_id"], "chart_type": "bar",
                                                 "x": "wimd_2019_quintile", "y": "n",
                                                 "title": "Type 2 diabetes by deprivation quintile"})
    query = next((r for r in results if isinstance(r, dict) and "rows" in r), None)
    if not query:
        return _reply("The query did not run, so I can't give figures.")
    rows = "; ".join(f"quintile {r[0]}: {r[1]}" for r in query["rows"])
    return _reply(f"Type 2 diabetes is recorded more often in more deprived areas ({rows} people; "
                  "quintile 1 is most deprived).\n\nHow this was answered: WLGP GP events joined to WDSD, "
                  "Read codes from the Phenotype Library, counts rounded to 5.")
