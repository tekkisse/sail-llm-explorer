# OpenMetadata → HealthDCAT-AP: custom-property setup

This kit prepares an OpenMetadata catalogue so its datasets can be exported as
**HealthDCAT-AP** (the health profile of W3C DCAT used by the European Health Data Space).
Other catalogues can then harvest the export.

It contains:

| File | Purpose |
|---|---|
| `om_hdcat_setup.py` | Creates the `hdcat*` custom properties and the supporting tag classifications in OpenMetadata. Safe to re-run. |
| `hdcat_vocab_map.yaml` | Maps the short codes used in OpenMetadata pick-lists to the vocabulary URIs the exporter publishes. |
| `README.md` | This file. |

Targets **OpenMetadata 2.0.x** (checked against the 2.0.2-release schemas) and
**HealthDCAT-AP Release 7**.

---

## 1. How the model works

- **One Data Product = one published dataset.** Each OpenMetadata Data Product becomes a
  `dcat:Dataset`. Its tables and views become the dataset's distributions and variables.
- **Use existing OpenMetadata fields wherever possible.** Title, description, owners, tags,
  column metadata, row counts and lineage already live in OpenMetadata. Custom properties
  only cover what HealthDCAT-AP needs and OpenMetadata doesn't hold.
- **Pick-lists store short codes, not URIs.** Curators choose readable values such as
  `NON_PUBLIC`. The exporter turns them into official URIs using `hdcat_vocab_map.yaml`, so
  URIs can be corrected without touching OpenMetadata.
- **Nothing is published until it's approved.** The exporter only publishes datasets whose
  `hdcatExportStatus` is `APPROVED`.

---

## 2. Prerequisites

- Python 3.9+ with `requests` (`pip install requests`)
- An OpenMetadata JWT that can edit types and classifications. The `ingestion-bot` token
  works: Settings → Bots → ingestion-bot → copy token.
- **One or more Teams created in OpenMetadata** (Settings → Teams) to act as:
  - the **health data access body**, e.g. "SAIL Databank – Data Access", with a contact email set
  - the **publisher**, e.g. "SAIL Databank"

  The script doesn't create these Teams.

---

## 3. Running the setup

```bash
export OM_HOST=http://<your-openmetadata>:8585/api
export OM_TOKEN=<bot JWT>

# 1. See what would change (writes nothing)
python3 om_hdcat_setup.py --dry-run

# 2. Apply
python3 om_hdcat_setup.py
```

Options:

| Option | Meaning |
|---|---|
| `--host` / `OM_HOST` | API base URL, including `/api` |
| `--token` / `OM_TOKEN` | JWT bearer token |
| `--entity` | Entity type to extend. Default `dataProduct`; use `table` as a fallback. |
| `--ca-bundle` | CA bundle path for internal TLS certificates |
| `--skip-tags` | Don't create the classifications and tags |
| `--dry-run` | Show the plan only |

Output symbols: `+` create, `~` add pick-list values, `=` already present,
`!` problem (skipped). The exit code is `1` if there were problems, `2` on error.

### Re-running and changing the model

- **Adding pick-list values:** edit the `values` list in `PROPERTIES` in the script and
  re-run. New values are merged into the existing ones.
- **Removing pick-list values:** the script never does this. OpenMetadata removes the value
  from every asset that uses it, so do it by hand in Settings → Custom Properties if you
  really mean it.
- **Changing a property's type or name:** OpenMetadata can't do this in place. Delete the
  property in the UI (which deletes its values on every asset), then re-run.
- **Verify** in the UI under Settings → Custom Properties → Data Products, or with:
  `GET /api/v1/metadata/types/name/dataProduct?fields=customProperties`

---

## 4. What maps where

### 4.1 Existing OpenMetadata fields (no setup needed)

| HealthDCAT-AP | Obligation | OpenMetadata source |
|---|---|---|
| `dct:title` | Mandatory | Data Product display name |
| `dct:description` | Mandatory | Data Product description |
| `dcat:contactPoint` | Recommended | Data Product owners (the Team's email) |
| `dcat:keyword` | Optional | Tags and glossary terms on the Data Product |
| `healthdcatap:hasStructuredData` | Mandatory (Release 7) | Derived: `true` if the Data Product contains table assets |
| `healthdcatap:hasVariables` | Mandatory when structured | Table and column names, types and descriptions → `csvw:TableGroup` / `csvw:Table` / `csvw:Column` |
| `healthdcatap:numberOfRecords` | Optional | Profiler row count, unless `hdcatNumberOfRecords` is set |
| `dct:source` | Optional | Upstream lineage |

### 4.2 Custom properties: mandatory in HealthDCAT-AP

These must be filled in before a dataset can be set to `APPROVED`.

| Property | Type | Maps to | What it's for / how to fill it |
|---|---|---|---|
| `hdcatIdentifier` | String | `dct:identifier` | A stable, persistent ID for the dataset, e.g. the SAIL dataset code or a DOI. Never the OpenMetadata UUID, which changes if the asset is recreated. |
| `hdcatAccessRights` | Pick one: `PUBLIC`, `RESTRICTED`, `NON_PUBLIC` | `dct:accessRights` (EU access-right vocabulary) | How the data can be accessed. Person-level SAIL data is `NON_PUBLIC`. Use `RESTRICTED` for non-personal data with conditions, and `PUBLIC` for open data only. |
| `hdcatApplicableLegislation` | Pick several: `UK_GDPR`, `DPA_2018`, `DEA_2017`, `COPI_2002`, `EHDS` | `dcatap:applicableLegislation` (legislation.gov.uk / ELI URIs) | The laws that govern this data. Most SAIL datasets: `UK_GDPR` + `DPA_2018`. Add `DEA_2017` (Digital Economy Act) or `COPI_2002` (Control of Patient Information regulations) where relevant. |
| `hdcatHealthCategory` | Pick several (16 EHDS categories, e.g. `EHR`, `POPULATION_REGISTRY`, `ADMIN_CLAIMS`, `RESEARCH_COHORT_SURVEY`, `GENOMIC`) | Health category (HealthDCAT-AP Health Categories vocabulary) | The type of health data, following EHDS Article 51. E.g. GP records → `EHR`; hospital admissions → `ADMIN_CLAIMS`/`EHR`; cancer registry → `POPULATION_REGISTRY`; cohort studies → `RESEARCH_COHORT_SURVEY`. |
| `hdcatAccessBody` | Entity reference → Team | Health data access body (Agent with name and contact point) | The body that grants access. Select the Team you created for SAIL's data access route; its name and email are published. |

### 4.3 Custom properties: recommended or optional

| Property | Type | Maps to | What it's for / how to fill it |
|---|---|---|---|
| `hdcatHealthTheme` | Pick several (e.g. `DEMENTIA`, `CANCER`, `MENTAL_HEALTH`) | Health theme | Disease or topic areas the dataset supports. Local starter list; extend as needed. |
| `hdcatPublisher` | Entity reference → Team | `dct:publisher` | The organisation publishing the dataset. If empty, the exporter's default publisher is used. |
| `hdcatTemporalCoverage` | Time interval (start, end) | `dct:temporal` | The period the data covers, e.g. 1990-01-01 to latest refresh. |
| `hdcatSpatialCoverage` | Pick several: `WALES`, `ENGLAND`, `SCOTLAND`, `NORTHERN_IRELAND`, `UK` | `dct:spatial` | Geographic coverage of the population. |
| `hdcatPopulationCoverage` | Markdown | Population coverage | Plain-language description of who is included, e.g. "All people registered with a SAIL-contributing GP practice in Wales". |
| `hdcatNumberOfRecords` | Integer | `healthdcatap:numberOfRecords` | Overrides the profiler row count when a single figure is more meaningful (e.g. events, not rows across several tables). |
| `hdcatUniqueIndividuals` | Integer | Number of unique individuals | Count of distinct people. **This is published, so enter a rounded figure** (e.g. to the nearest 1,000). |
| `hdcatMinTypicalAge` / `hdcatMaxTypicalAge` | Integer | Minimum / maximum typical age | Typical age range of the population, in years. |
| `hdcatPurpose` | Pick several: `ACADEMIC_RESEARCH`, `RESEARCH_AND_DEVELOPMENT`, `PUBLIC_HEALTH`, `HEALTHCARE_PLANNING`, `OFFICIAL_STATISTICS` | `healthdcatap:purpose` (DPV purposes) | The purposes the data may be used for. |
| `hdcatLegalBasis` | Pick several: `GDPR_6_1_A/C/E/F`, `GDPR_9_2_A/H/I/J` | `healthdcatap:legalBasis` (DPV GDPR terms) | UK GDPR lawful basis (Article 6) and special-category condition (Article 9). Typical for research: `GDPR_6_1_E` + `GDPR_9_2_J`. |
| `hdcatPersonalData` | Pick several: `HEALTH`, `GENETIC`, `AGE`, `GENDER`, `LOCATION`, `SOCIOECONOMIC`, `ETHNICITY` | Personal data (DPV categories) | Categories of (pseudonymised) personal data present. |
| `hdcatRetentionPeriod` | Duration (ISO 8601) | Retention period | How long the data is kept, e.g. `P10Y` for 10 years. |
| `hdcatConformsTo` | Pick several: `OMOP_CDM_54`, `OMOP_CDM_53`, `FHIR_R4`, `HL7_V2`, `OPENEHR` | `dct:conformsTo` (Standard vocabulary) | The data model or standard the dataset follows, if any. |
| `hdcatProvenance` | Markdown | `dct:provenance` | How the data was collected, linked and processed, e.g. the split-file linkage process. |
| `hdcatVersionNotes` | Markdown | `adms:versionNotes` | What changed in the latest refresh or release. |
| `hdcatDocumentationUrl` | Hyperlink (or string) | `foaf:page` | Public documentation page for the dataset. |
| `hdcatAccessUrl` | Hyperlink (or string) | `dcat:accessURL` on the Distribution | Where researchers apply for access. HealthDCAT-AP expects a Distribution for non-public data, so fill this in for every `NON_PUBLIC` dataset. |

### 4.4 Custom property: export control (not part of HealthDCAT-AP)

| Property | Type | What it does |
|---|---|---|
| `hdcatExportStatus` | Pick one: `DRAFT`, `APPROVED`, `WITHDRAWN` | Publication gate. `DRAFT` is never exported; `APPROVED` is exported; `WITHDRAWN` is removed from the feed. Set to `APPROVED` only after the metadata has been reviewed, including a disclosure check. |

### 4.5 Column-level tags

| Classification | Tags | Used for |
|---|---|---|
| `CodingSystem` | `ICD10`, `ICD9`, `ReadV2`, `CTV3`, `SNOMEDCT`, `OPCS4`, `dmd`, `BNF`, `ATC`, `LOINC` | Tag coded columns with their coding system. The exporter adds it to each published column and rolls the set up to `healthdcatap:hasCodingSystem` on the dataset, so it never has to be entered twice. |
| `HdcatExport` | `Exclude` | Tag a column to leave it out of the published variable list (`hasVariables`), e.g. internal linkage keys or audit columns. |

---

## 5. Vocabulary map (`hdcat_vocab_map.yaml`)

The exporter reads this file to turn pick-list codes into URIs.

| Section | Status |
|---|---|
| `hdcatAccessRights` | Done: EU access-right URIs |
| `hdcatApplicableLegislation` | Done: legislation.gov.uk and EHDS ELI URIs |
| `hdcatLegalBasis` | Done: DPV GDPR terms (`eu-gdpr:A6-1-e` etc.) |
| `hdcatPurpose` | Partial: two DPV terms filled in, others TODO |
| `hdcatHealthCategory` | **TODO, and mandatory.** Map to the HealthDCAT-AP Health Categories vocabulary. The 16 local codes follow EHDS Article 51 but must be checked against the official list. |
| `namespaces.healthdcatap` | **TODO.** Release 7's prefix table (`http://healthdataportal.eu/ns/health#`) and its property URIs (`https://w3id.org/healthdcatap#`) disagree; confirm which one validators expect. |
| `hdcatHealthTheme`, `hdcatConformsTo`, `CodingSystem` | TODO: map to the Health Theme, Standard and Coding System vocabularies (the last two are new in Release 7) |
| `hdcatSpatialCoverage`, `hdcatPersonalData` | TODO: choose GeoNames/ONS URIs and DPV personal-data terms |

If a code has no URI, the exporter should fail validation for that dataset rather than
publish a broken link.

---

## 6. Curation workflow

1. Run the setup script (section 3).
2. Create a Data Product for each published dataset and add its tables or views as assets.
3. Make sure description, owners and column descriptions are complete. These are published.
4. Fill in the mandatory `hdcat*` properties (section 4.2), then as many optional ones as you can.
5. Tag coded columns with `CodingSystem.*`, and tag columns to hide with `HdcatExport.Exclude`.
6. Review the dataset, including a disclosure check (section 7), then set `hdcatExportStatus = APPROVED`.
7. The exporter validates each approved dataset against the HealthDCAT-AP SHACL shapes,
   leaves out any that fail, and publishes the rest.

Tip: in OpenMetadata, give a reviewer group edit rights on `hdcatExportStatus` only, so
approval is a separate step from curation.

---

## 7. Disclosure guidance

The export is designed for **open discovery**. Treat everything in it as public.

- **Never published:** profiler statistics (min/max, distinct values, value distributions,
  histograms), sample data, or categorical code frequencies.
- **Published:** dataset descriptions, column names, types and descriptions (minus
  `Exclude`-tagged columns), coding systems, and dataset-level counts.
- Enter `hdcatUniqueIndividuals` and `hdcatNumberOfRecords` as **rounded** figures.
- Check free-text fields (`hdcatProvenance`, `hdcatPopulationCoverage`, descriptions) for
  anything identifying or commercially sensitive.

---

## 8. Known limitations

- **UK and EHDS fit.** The access body and applicable legislation fields are designed for
  the EHDS. For UK datasets they hold UK law and your own access route. EU harvesters will
  accept this as valid RDF, but anything that checks EHDS rules specifically may flag it.
- **The script hasn't been run against a live OpenMetadata yet,** only a mock of the API.
  Start with `--dry-run` on a test instance.
- **Data Products.** If your OpenMetadata deployment doesn't support custom properties on
  Data Products, use `--entity table` and put the properties on each dataset's main table.
  The exporter then has to be configured to treat that table as the dataset.
- **Release changes.** HealthDCAT-AP is still evolving alongside the EHDS implementing acts.
  Check the [latest release](https://healthdataeu.pages.code.europa.eu/healthdcat-ap/releases/latest/)
  and its changelog before each major export change.

---

## References

- HealthDCAT-AP Release 7: <https://healthdataeu.pages.code.europa.eu/healthdcat-ap/releases/release-7/>
- HealthDCAT-AP Release 7 changelog: <https://healthdataeu.pages.code.europa.eu/healthdcat-ap/releases/release-7/changelog.html>
- OpenMetadata custom properties: <https://docs.open-metadata.org/v2.0.x/how-to-guides/guide-for-data-users/custom.md>
- OpenMetadata custom properties API: <https://docs.open-metadata.org/v2.0.x/developers/custom-properties.md>
- Example exporter for DataHub (AP-HP): <https://github.com/davidouagne/datahub-healthdcat-ap-exporter>
