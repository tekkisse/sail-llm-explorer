#!/usr/bin/env python3
"""
om_hdcat_setup.py - create the HealthDCAT-AP custom-property model in OpenMetadata.

Creates (idempotently):
  * hdcat* custom properties on an entity type (default: dataProduct)
  * the CodingSystem and HdcatExport classifications and their tags

Re-running is safe:
  * missing properties are created
  * existing enum properties get any NEW values merged in (values are never
    removed - OpenMetadata runs a clean-up job that strips removed enum values
    from every asset, so removal is left as a deliberate manual action)
  * a property that exists with a DIFFERENT type is reported and skipped
    (OpenMetadata properties cannot be retyped or renamed in place)

Target: OpenMetadata 2.0.x  (written against the 2.0.2-release schemas)

Usage:
  export OM_HOST=http://localhost:8585/api
  export OM_TOKEN=<bot JWT, e.g. ingestion-bot>
  python3 om_hdcat_setup.py --dry-run           # show the plan
  python3 om_hdcat_setup.py                     # apply
  python3 om_hdcat_setup.py --entity table      # fallback if dataProduct is unsuitable

Requires: python 3.9+, requests
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import requests

# --------------------------------------------------------------------------------------
# Model definition
#   Enum values are short local codes. The exporter maps code -> vocabulary URI using
#   hdcat_vocab_map.yaml, so the codes here never need to be URIs.
#   Values can be ADDED later by editing these lists and re-running.
# --------------------------------------------------------------------------------------

P = "hdcat"  # property name prefix (OpenMetadata requires camelCase, no '_' or '.')

PROPERTIES: list[dict[str, Any]] = [
    # ---------------- Mandatory in HealthDCAT-AP ----------------
    {
        "name": f"{P}Identifier",
        "displayName": "HDCAT: Identifier",
        "type": "string",
        "description": "**dct:identifier (mandatory).** Stable persistent identifier for the "
        "dataset, e.g. SAIL dataset code or DOI. Do not use the OpenMetadata UUID.",
    },
    {
        "name": f"{P}AccessRights",
        "displayName": "HDCAT: Access rights",
        "type": "enum",
        "multiSelect": False,
        "values": ["PUBLIC", "RESTRICTED", "NON_PUBLIC"],
        "description": "**dct:accessRights (mandatory).** EU access-right vocabulary. "
        "Person-level SAIL data is NON_PUBLIC.",
    },
    {
        "name": f"{P}ApplicableLegislation",
        "displayName": "HDCAT: Applicable legislation",
        "type": "enum",
        "multiSelect": True,
        "values": ["UK_GDPR", "DPA_2018", "DEA_2017", "COPI_2002", "EHDS"],
        "description": "**dcatap:applicableLegislation (mandatory).** Legal instruments that "
        "govern this dataset. Mapped to legislation.gov.uk / ELI URIs by the exporter.",
    },
    {
        "name": f"{P}HealthCategory",
        "displayName": "HDCAT: Health category",
        "type": "enum",
        "multiSelect": True,
        # Local codes for the EHDS Art. 51 categories. Confirm/align with the
        # HealthDCAT-AP 'Health Categories' vocabulary before publishing.
        "values": [
            "EHR",
            "HEALTH_DETERMINANTS",
            "HEALTHCARE_RESOURCES",
            "PATHOGEN",
            "ADMIN_CLAIMS",
            "GENOMIC",
            "OTHER_MOLECULAR",
            "DEVICE_GENERATED",
            "WELLNESS_APPS",
            "HEALTH_PROFESSIONALS",
            "POPULATION_REGISTRY",
            "MEDICAL_MORTALITY_REGISTRY",
            "CLINICAL_TRIALS",
            "PRODUCT_DEVICE_REGISTRY",
            "RESEARCH_COHORT_SURVEY",
            "BIOBANK",
        ],
        "description": "**Health category (mandatory).** EHDS data category. Codes are mapped "
        "to the HealthDCAT-AP Health Categories vocabulary by the exporter.",
    },
    {
        "name": f"{P}AccessBody",
        "displayName": "HDCAT: Health data access body",
        "type": "entityReference",
        "entityTypes": ["team"],
        "description": "**Health data access body (mandatory).** Team representing the body "
        "that grants access (name + email become the Agent and contact point).",
    },
    # ---------------- Recommended / optional ----------------
    {
        "name": f"{P}HealthTheme",
        "displayName": "HDCAT: Health theme",
        "type": "enum",
        "multiSelect": True,
        # Local starter list - map to the HealthDCAT-AP Health Theme vocabulary.
        "values": [
            "CANCER",
            "CARDIOVASCULAR",
            "COVID19",
            "DEMENTIA",
            "DIABETES",
            "INFECTIOUS_DISEASE",
            "MATERNITY_CHILD",
            "MENTAL_HEALTH",
            "MUSCULOSKELETAL",
            "RESPIRATORY",
        ],
        "description": "Health theme (optional). Mapped to the HealthDCAT-AP Health Theme vocabulary.",
    },
    {
        "name": f"{P}Publisher",
        "displayName": "HDCAT: Publisher",
        "type": "entityReference",
        "entityTypes": ["team"],
        "description": "dct:publisher. Team that publishes the dataset (falls back to the "
        "exporter's default publisher if empty).",
    },
    {
        "name": f"{P}TemporalCoverage",
        "displayName": "HDCAT: Temporal coverage",
        "type": "timeInterval",
        "description": "dct:temporal. Start/end of the period the data covers.",
    },
    {
        "name": f"{P}SpatialCoverage",
        "displayName": "HDCAT: Geographical coverage",
        "type": "enum",
        "multiSelect": True,
        "values": ["WALES", "ENGLAND", "SCOTLAND", "NORTHERN_IRELAND", "UK"],
        "description": "dct:spatial. Mapped to geographic URIs by the exporter.",
    },
    {
        "name": f"{P}PopulationCoverage",
        "displayName": "HDCAT: Population coverage",
        "type": "markdown",
        "description": "Population coverage - free-text description of who is in the data.",
    },
    {
        "name": f"{P}NumberOfRecords",
        "displayName": "HDCAT: Number of records",
        "type": "integer",
        "description": "healthdcatap:numberOfRecords. Overrides the profiler row count if set.",
    },
    {
        "name": f"{P}UniqueIndividuals",
        "displayName": "HDCAT: Number of unique individuals",
        "type": "integer",
        "description": "Number of unique individuals. **Enter a rounded figure** - this is published.",
    },
    {
        "name": f"{P}MinTypicalAge",
        "displayName": "HDCAT: Minimum typical age",
        "type": "integer",
        "description": "Minimum typical age of individuals in the dataset.",
    },
    {
        "name": f"{P}MaxTypicalAge",
        "displayName": "HDCAT: Maximum typical age",
        "type": "integer",
        "description": "Maximum typical age of individuals in the dataset.",
    },
    {
        "name": f"{P}Purpose",
        "displayName": "HDCAT: Purpose",
        "type": "enum",
        "multiSelect": True,
        "values": ["ACADEMIC_RESEARCH", "RESEARCH_AND_DEVELOPMENT", "PUBLIC_HEALTH",
                   "HEALTHCARE_PLANNING", "OFFICIAL_STATISTICS"],
        "description": "Purpose (healthdcatap:purpose). Mapped to DPV purpose URIs.",
    },
    {
        "name": f"{P}LegalBasis",
        "displayName": "HDCAT: Legal basis",
        "type": "enum",
        "multiSelect": True,
        "values": ["GDPR_6_1_A", "GDPR_6_1_C", "GDPR_6_1_E", "GDPR_6_1_F",
                   "GDPR_9_2_A", "GDPR_9_2_H", "GDPR_9_2_I", "GDPR_9_2_J"],
        "description": "Legal basis (healthdcatap:legalBasis). Mapped to DPV GDPR terms.",
    },
    {
        "name": f"{P}PersonalData",
        "displayName": "HDCAT: Personal data categories",
        "type": "enum",
        "multiSelect": True,
        "values": ["HEALTH", "GENETIC", "AGE", "GENDER", "LOCATION", "SOCIOECONOMIC", "ETHNICITY"],
        "description": "Categories of personal data present (pseudonymised). Mapped to DPV.",
    },
    {
        "name": f"{P}RetentionPeriod",
        "displayName": "HDCAT: Retention period",
        "type": "duration",
        "description": "Retention period as ISO 8601 duration, e.g. P10Y.",
    },
    {
        "name": f"{P}ConformsTo",
        "displayName": "HDCAT: Conforms to standard",
        "type": "enum",
        "multiSelect": True,
        "values": ["OMOP_CDM_54", "OMOP_CDM_53", "FHIR_R4", "HL7_V2", "OPENEHR"],
        "description": "dct:conformsTo. Data model / standard the dataset follows.",
    },
    {
        "name": f"{P}Provenance",
        "displayName": "HDCAT: Provenance",
        "type": "markdown",
        "description": "dct:provenance. How the data was collected, linked and processed.",
    },
    {
        "name": f"{P}VersionNotes",
        "displayName": "HDCAT: Version notes",
        "type": "markdown",
        "description": "adms:versionNotes. What changed in the current release of the dataset.",
    },
    {
        "name": f"{P}DocumentationUrl",
        "displayName": "HDCAT: Documentation URL",
        "type": "hyperlink-cp|string",  # hyperlink-cp if the server has it, else string
        "description": "foaf:page. Public documentation page for the dataset.",
    },
    {
        "name": f"{P}AccessUrl",
        "displayName": "HDCAT: Access URL",
        "type": "hyperlink-cp|string",
        "description": "dcat:accessURL of the Distribution - the page where researchers apply "
        "for access. HealthDCAT-AP expects a Distribution for non-public data.",
    },
    # ---------------- Export control (not part of HealthDCAT-AP) ----------------
    {
        "name": f"{P}ExportStatus",
        "displayName": "HDCAT: Export status",
        "type": "enum",
        "multiSelect": False,
        "values": ["DRAFT", "APPROVED", "WITHDRAWN"],
        "description": "Publication gate. The exporter only publishes APPROVED datasets; "
        "WITHDRAWN datasets are emitted as withdrawn/removed from the feed.",
    },
]

CLASSIFICATIONS: list[dict[str, Any]] = [
    {
        "name": "CodingSystem",
        "description": "Coding system used by a column. Rolled up by the HealthDCAT-AP exporter "
        "to healthdcatap:hasCodingSystem on the dataset.",
        "mutuallyExclusive": False,
        "tags": {
            "ICD10": "ICD-10 (WHO / UK edition)",
            "ICD9": "ICD-9",
            "ReadV2": "Read codes version 2",
            "CTV3": "Read codes CTV3",
            "SNOMEDCT": "SNOMED CT",
            "OPCS4": "OPCS-4 procedure codes",
            "dmd": "NHS Dictionary of Medicines and Devices",
            "BNF": "British National Formulary",
            "ATC": "Anatomical Therapeutic Chemical classification",
            "LOINC": "LOINC",
        },
    },
    {
        "name": "HdcatExport",
        "description": "Controls what the HealthDCAT-AP exporter publishes at column level.",
        "mutuallyExclusive": True,
        "tags": {
            "Exclude": "Do not publish this column in healthdcatap:hasVariables.",
        },
    },
]


# --------------------------------------------------------------------------------------
# OpenMetadata client
# --------------------------------------------------------------------------------------
class OM:
    def __init__(self, host: str, token: str | None, verify: bool | str = True):
        self.base = host.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Content-Type"] = "application/json"
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"
        self.s.verify = verify

    def _r(self, method: str, path: str, **kw) -> requests.Response:
        r = self.s.request(method, f"{self.base}{path}", timeout=60, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r

    def get(self, path: str, **kw) -> Any:
        return self._r("GET", path, **kw).json()

    def put(self, path: str, body: dict) -> Any:
        return self._r("PUT", path, json=body).json()


def build_property_body(p: dict, field_types: dict[str, dict]) -> dict:
    """Turn a model entry into an OpenMetadata CustomProperty body."""
    type_name = p["type"]
    if "|" in type_name:  # first available alternative
        type_name = next((t for t in type_name.split("|") if t in field_types), None)
        if type_name is None:
            raise RuntimeError(f"none of {p['type']} available on this server")
    if type_name not in field_types:
        raise RuntimeError(f"field type '{type_name}' not available on this server")

    body: dict[str, Any] = {
        "name": p["name"],
        "displayName": p.get("displayName", p["name"]),
        "description": p["description"],
        "propertyType": {"id": field_types[type_name]["id"], "type": "type"},
    }
    if type_name == "enum":
        body["customPropertyConfig"] = {
            "config": {"values": list(p["values"]), "multiSelect": bool(p.get("multiSelect"))}
        }
    elif type_name in ("entityReference", "entityReferenceList"):
        body["customPropertyConfig"] = {"config": list(p["entityTypes"])}
    return body


def setup_properties(om: OM, entity: str, dry: bool) -> int:
    problems = 0
    etype = om.get(f"/v1/metadata/types/name/{entity}", params={"fields": "customProperties"})
    existing = {cp["name"]: cp for cp in (etype.get("customProperties") or [])}

    ft = om.get("/v1/metadata/types", params={"category": "field", "limit": 1000})
    field_types = {t["name"]: t for t in ft.get("data", [])}

    print(f"\n== Custom properties on '{entity}' (type id {etype['id']}) ==")
    for p in PROPERTIES:
        try:
            body = build_property_body(p, field_types)
        except RuntimeError as e:
            print(f"  ! {p['name']}: {e}")
            problems += 1
            continue
        want_type = next(n for n, t in field_types.items() if t["id"] == body["propertyType"]["id"])
        cur = existing.get(p["name"])

        if cur is None:
            print(f"  + create {p['name']} ({want_type})")
            if not dry:
                om.put(f"/v1/metadata/types/{etype['id']}", body)
            continue

        cur_type = (cur.get("propertyType") or {}).get("name")
        if cur_type != want_type:
            print(f"  ! {p['name']} exists as '{cur_type}', wanted '{want_type}' - skipped "
                  f"(delete it in Settings > Custom Properties to recreate)")
            problems += 1
            continue

        if want_type == "enum":
            cur_cfg = (cur.get("customPropertyConfig") or {}).get("config") or {}
            cur_vals = list(cur_cfg.get("values") or [])
            new_vals = [v for v in p["values"] if v not in cur_vals]
            if new_vals:
                merged = cur_vals + new_vals  # never drop existing values
                body["customPropertyConfig"]["config"]["values"] = merged
                body["customPropertyConfig"]["config"]["multiSelect"] = bool(
                    cur_cfg.get("multiSelect", p.get("multiSelect")))
                print(f"  ~ {p['name']}: add enum values {new_vals}")
                if not dry:
                    om.put(f"/v1/metadata/types/{etype['id']}", body)
                continue
            extra = [v for v in cur_vals if v not in p["values"]]
            if extra:
                print(f"  = {p['name']} (server also has values not in script: {extra} - kept)")
                continue
        print(f"  = {p['name']} already present")
    return problems


def setup_classifications(om: OM, dry: bool) -> None:
    print("\n== Classifications and tags ==")
    for c in CLASSIFICATIONS:
        print(f"  * classification {c['name']} ({len(c['tags'])} tags)")
        if not dry:
            om.put("/v1/classifications", {
                "name": c["name"],
                "description": c["description"],
                "mutuallyExclusive": c["mutuallyExclusive"],
            })
            for tname, tdesc in c["tags"].items():
                om.put("/v1/tags", {"classification": c["name"], "name": tname, "description": tdesc})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("OM_HOST", "http://localhost:8585/api"),
                    help="OpenMetadata API base, e.g. https://om.example.org/api (env OM_HOST)")
    ap.add_argument("--token", default=os.environ.get("OM_TOKEN"), help="JWT (env OM_TOKEN)")
    ap.add_argument("--entity", default="dataProduct",
                    help="Entity type to extend (default dataProduct; fallback: table)")
    ap.add_argument("--ca-bundle", default=None, help="Path to CA bundle for TLS verification")
    ap.add_argument("--skip-tags", action="store_true", help="Do not create classifications/tags")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change, write nothing")
    a = ap.parse_args()

    om = OM(a.host, a.token, verify=a.ca_bundle or True)
    try:
        ver = om.get("/v1/system/version")
        print(f"Connected to OpenMetadata {ver.get('version', '?')} at {a.host}"
              f"{'  [DRY RUN]' if a.dry_run else ''}")
        problems = setup_properties(om, a.entity, a.dry_run)
        if not a.skip_tags:
            setup_classifications(om, a.dry_run)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"\nDone{' (dry run)' if a.dry_run else ''}. {problems} problem(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())