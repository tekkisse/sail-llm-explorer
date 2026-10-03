"""Push descriptions, known issues, sensitivity tags and glossary terms from a catalogue YAML
into OpenMetadata, so the real OpenMetadata MCP server can answer what the demo catalogue answers.

    python scripts/push_catalog_to_openmetadata.py demo/catalog.yaml \
        --host http://openmetadata-server:8585/api --token $OM_BOT_JWT [--dry-run]

Run the metadata ingestion first so the tables exist. Uses JSON Patch on /v1/tables.
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx
import yaml


def table_description(t: dict) -> str:
    parts = [t.get("description", "")]
    if t.get("grain"):
        parts.append(f"**Grain:** one row per {t['grain']}.")
    if t.get("coverage"):
        parts.append(f"**Coverage:** {t['coverage']}.")
    if t.get("topics"):
        parts.append(f"**Topics:** {', '.join(t['topics'])}.")
    if t.get("known_issues"):
        parts.append("**Known issues:**\n" + "\n".join(f"- {i}" for i in t["known_issues"]))
    return "\n\n".join(p for p in parts if p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog")
    ap.add_argument("--host", required=True, help="OpenMetadata API base, e.g. http://host:8585/api")
    ap.add_argument("--token", required=True)
    ap.add_argument("--glossary", default="SAIL")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cat = yaml.safe_load(open(args.catalog))
    base = f"{cat['service']}.{cat['database']}.{cat['schema']}"
    http = httpx.Client(base_url=args.host.rstrip("/"), timeout=30,
                        headers={"Authorization": f"Bearer {args.token}"})

    for t in cat["tables"]:
        fqn = f"{base}.{t['name']}"
        resp = http.get(f"/v1/tables/name/{fqn}", params={"fields": "columns,tags"})
        if resp.status_code == 404:
            print(f"skip {fqn}: not ingested yet", file=sys.stderr)
            continue
        resp.raise_for_status()
        table = resp.json()
        index = {c["name"].lower(): i for i, c in enumerate(table["columns"])}
        ops = [{"op": "add", "path": "/description", "value": table_description(t)}]
        for c in t.get("columns", []):
            i = index.get(c["name"].lower())
            if i is None:
                continue
            desc = c.get("description", "")
            if c.get("code_system"):
                desc += f" Coding system: {c['code_system']}."
            ops.append({"op": "add", "path": f"/columns/{i}/description", "value": desc})
            if c.get("sensitive"):
                existing = {tag["tagFQN"] for tag in table["columns"][i].get("tags", [])}
                if "PII.Sensitive" not in existing:
                    ops.append({"op": "add", "path": f"/columns/{i}/tags/-", "value": {
                        "tagFQN": "PII.Sensitive", "source": "Classification",
                        "labelType": "Manual", "state": "Confirmed"}})
        print(f"{fqn}: {len(ops)} change(s)")
        if not args.dry_run:
            http.patch(f"/v1/tables/{table['id']}", content=json.dumps(ops),
                       headers={"Content-Type": "application/json-patch+json"}).raise_for_status()

    if cat.get("glossary"):
        print(f"glossary {args.glossary}: {len(cat['glossary'])} term(s)")
        if not args.dry_run:
            r = http.put("/v1/glossaries", json={"name": args.glossary, "displayName": args.glossary,
                                                 "description": "SAIL Databank terms used by the explorer."})
            r.raise_for_status()
            for g in cat["glossary"]:
                http.put("/v1/glossaryTerms", json={"glossary": args.glossary, "name": g["term"].replace(".", ""),
                                                    "displayName": g["term"], "description": g["description"]}
                         ).raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
