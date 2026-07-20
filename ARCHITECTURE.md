# Architecture

Technical reference for the European Banking Regulatory Dashboard project.

## Stack

| Layer | Technology | Cost |
|---|---|---|
| Acquisition / cleaning | Python 3.11 + pandas + openpyxl + requests | Free |
| Modelling | CSV files on disk, star schema | Free |
| Reporting | Power BI Desktop (Import mode), PBIP project: PBIR report + TMDL semantic model | Free |
| Versioning | Git + GitHub public repository | Free |

No SaaS service, no paid licence, no cloud egress. The Power BI report is
distributed as a PBIP project (`powerbi/eba_dashboard.pbip` plus its
`.Report` and `.SemanticModel` folders) committed file by file, which makes
every measure and every visual reviewable in a pull request.

---

## Data flow

```
ECB Data Portal (SDMX-CSV)          EBA Transparency Exercise 2024 (EAV CSVs)
    KRI dataflow                          tr_oth.csv, tr_cre.csv, +metadata
        |                                            |
        v                                            v
                  01_download_data.py  --> data/raw/
        |                                            |
        v                                            v
 02_clean_risk_indicators.py             03_clean_transparency.py
        |                                            |
        +-------------> 04_build_model.py <----------+
                                |
                                v
                  data/processed/  (5 CSVs, star schema)
                                |
                                v
                    powerbi/eba_dashboard.pbip
              (TMDL semantic model + PBIR report)
                                ^
                                |
          specs/*.yaml -> scripts/compile_page.py -> pbir_lint.py
                     -> pbi_render.py (page captures)
```

Every step is idempotent: rerunning a script overwrites its outputs without
side effects on the prior stages.

---

## Semantic model (star schema)

### Fact: FACT_RATIOS

| Column | Type | Notes |
|---|---|---|
| ROW_ID | INT | Surrogate primary key |
| BANK_ID | INT | FK → DIM_BANK |
| COUNTRY_CODE | VARCHAR(3) | FK → DIM_COUNTRY |
| DATE_ID | INT | FK → DIM_DATE |
| INDICATOR_ID | INT | FK → DIM_INDICATOR |
| VALUE | DECIMAL(10,4) | Ratio in decimal form (0.155 = 15.5%) |
| SOURCE | VARCHAR(20) | `RISK_DASHBOARD` (ECB KRI) or `TRANSPARENCY` |
| REFERENCE_DATE | DATE | Exact reference date |

### DIM_BANK

| Column | Notes |
|---|---|
| BANK_ID | Surrogate PK |
| BANK_CODE | LEI (TE) or `AGG_<country>` (ECB KRI aggregates) |
| BANK_NAME | Display name |
| COUNTRY_CODE | FK → DIM_COUNTRY |
| BANK_SIZE | `LARGE` (>300 BEUR), `MEDIUM` (30-300), `SMALL` (<30), `AGGREGATE` |
| IS_GSIB | 1 if listed by the FSB 2024 G-SIB roster |
| TOTAL_ASSETS_BEUR | Total assets in billion EUR |

Country aggregates from the ECB KRI dataflow are stored as banks with
`BANK_SIZE = 'AGGREGATE'`, so every visual behaves consistently regardless of
the underlying grain.

### DIM_COUNTRY

| Column | Notes |
|---|---|
| COUNTRY_CODE | ISO 3166-1 alpha-2, plus `EU` for the U2/EU aggregate |
| COUNTRY_NAME | English label |
| REGION | `NORTH`, `SOUTH`, `EAST`, `WEST`, `EU_AGG` |
| EU_MEMBER | Boolean |
| EEA_MEMBER | Boolean |

### DIM_DATE

| Column | Notes |
|---|---|
| DATE_ID | Surrogate PK (1 = oldest quarter present) |
| REFERENCE_DATE | Last day of the quarter |
| YEAR / QUARTER / SEMESTER | Integer parts |
| YEAR_QUARTER | `YYYY-Qn` |
| SOURCE_LABEL | `Qn YYYY` |

Covers Q1 2018 → Q4 2024 (28 quarters).

### DIM_INDICATOR

| Column | Notes |
|---|---|
| INDICATOR_ID | Surrogate PK |
| INDICATOR_CODE | Short code used in FACT_RATIOS |
| INDICATOR_NAME | Full label |
| CATEGORY | `CAPITAL`, `LIQUIDITY`, `ASSET_QUALITY`, `PROFITABILITY`, `LEVERAGE` |
| UNIT | `PCT`, `RATIO`, `BEUR` |
| REGULATORY_MIN | Decimal threshold (e.g. 0.045 for CET1) |
| HIGHER_IS_BETTER | Drives conditional-formatting direction |
| CRR2_REFERENCE | Article reference |

---

## Relations

```
FACT_RATIOS[BANK_ID]      -> DIM_BANK[BANK_ID]            (many-to-one, single)
FACT_RATIOS[COUNTRY_CODE] -> DIM_COUNTRY[COUNTRY_CODE]    (many-to-one, single)
FACT_RATIOS[DATE_ID]      -> DIM_DATE[DATE_ID]            (many-to-one, single)
FACT_RATIOS[INDICATOR_ID] -> DIM_INDICATOR[INDICATOR_ID]  (many-to-one, single)
DIM_BANK[COUNTRY_CODE]    -> DIM_COUNTRY[COUNTRY_CODE]    (many-to-one, single)
```

All relations are single-direction; `DIM_BANK[COUNTRY_CODE] → DIM_COUNTRY`
keeps country slicers working when filtering by bank.

---

## DAX measure catalogue

Measures live under a dedicated `_Measures` table in the semantic model
(`definition/tables/_Measures.tmdl`). Every measure carries its format string
in the model; visuals never override formats.

### Base

```dax
Avg Ratio        = AVERAGEX(fact_ratios, fact_ratios[VALUE])
Latest Value     = CALCULATE(AVERAGE(fact_ratios[VALUE]),
                             LASTDATE(dim_date[REFERENCE_DATE]))
Nb Banks         = DISTINCTCOUNT(fact_ratios[BANK_ID])
Nb Countries     = DISTINCTCOUNT(fact_ratios[COUNTRY_CODE])

-- Last quarter WITH data in context: robust to the Transparency Exercise
-- stopping at 2024-Q2 while the KRI dataflow runs to 2024-Q4.
Latest NB Value  =
VAR LastDataDate = CALCULATE(MAX(fact_ratios[REFERENCE_DATE]))
RETURN CALCULATE(AVERAGE(fact_ratios[VALUE]),
                 fact_ratios[REFERENCE_DATE] = LastDataDate)
```

### Indicator-pinned families

Generic measures cannot mix indicators in one visual, so the model exposes
pinned families usable at any grain (bank, country, EU): `CET1 Latest`,
`NPL Latest`, `LCR Latest`, ... (via `Latest NB Value`), the smoothed
`* Rolling 4Q` series used by the Trend Analysis page, and the `EU *` KPI
measures pinned to the `AGG_EU` aggregate for the Executive Overview cards.

### Time intelligence

```dax
YoY Change =
VAR Current = [Latest Value]
VAR Prior = CALCULATE([Avg Ratio], DATEADD(DIM_DATE[REFERENCE_DATE], -1, YEAR))
RETURN DIVIDE(Current - Prior, Prior)

YoY Change bps = ([Latest Value] - CALCULATE([Avg Ratio],
    DATEADD(DIM_DATE[REFERENCE_DATE], -1, YEAR))) * 10000

Rolling 4Q Avg =
AVERAGEX(
    DATESINPERIOD(DIM_DATE[REFERENCE_DATE],
                  LASTDATE(DIM_DATE[REFERENCE_DATE]), -4, QUARTER),
    [Avg Ratio]
)

Bank Rank Latest =
RANKX(ALLSELECTED(DIM_BANK[BANK_NAME]), [Latest Value], , DESC, Dense)
```

### Regulatory

```dax
Pct Banks Below Min =
VAR Threshold = MAX(DIM_INDICATOR[REGULATORY_MIN])
VAR Below = CALCULATE(DISTINCTCOUNT(FACT_RATIOS[BANK_ID]),
                      FACT_RATIOS[VALUE] < Threshold)
RETURN DIVIDE(Below, [Nb Banks])

-- Blank-safe: entities with no data in context must return BLANK, not
-- BLANK minus the threshold (which fabricates a fake breach).
Avg Regulatory Buffer =
VAR MinThreshold = MAX(dim_indicator[REGULATORY_MIN])
VAR BaseValue = [Latest NB Value]
RETURN IF(NOT ISBLANK(MinThreshold) && NOT ISBLANK(BaseValue),
          BaseValue - MinThreshold, BLANK())

Compliance Status =
VAR Threshold = MAX(dim_indicator[REGULATORY_MIN])
RETURN IF(
    ISBLANK(Threshold), "N/A",
    IF([Latest NB Value] >= Threshold, "COMPLIANT", "BELOW MINIMUM")
)
```

---

## Report build pipeline

The report layer is never edited by hand. Each page is a YAML spec under
`powerbi/specs/`, compiled to PBIR JSON by a deterministic toolchain:

```
1. PROFILE   scripts/pbi_profile.py    DAX against Desktop's local AS instance
2. DESIGN    specs/<page>.yaml         visual choice driven by data shape
3. COMPILE   scripts/compile_page.py   spec -> PBIR JSON, idempotent page ids
4. LINT      scripts/pbir_lint.py      bindings vs TMDL, layout, placeholders
5. RENDER    scripts/pbi_render.py     drives Desktop, captures pages to PNG
6. CRITIQUE  review the captures, patch the spec, recompile
```

Properties of the pipeline:

- **Idempotent recompiles**: a fixed 20-char `page_id` per spec means the
  same page folder is rebuilt in place; visual ids are content-addressed,
  so git diffs stay minimal.
- **Lint before render**: field bindings are checked against the TMDL
  (existence and column/measure kind), layout is checked for overlap and
  canvas overflow, tooltip references are resolved.
- **Design system**: 12-column grid, KPI band on top, one dominant visual
  per page, message titles ("Every market holds a double-digit CET1
  buffer"), no decorative colour. Red/green appears only on the compliance
  page, driven by rule-based conditional formatting on the buffer measure.

## TMDL semantic model

The model is stored as TMDL on disk (`eba_dashboard.SemanticModel/
definition/`): one file per table, explicit relationships, measures in
`_Measures.tmdl`. This is what Fabric Git integration produces, obtained
here with Desktop alone (zero-paid-licence constraint holds); measures are
reviewable line by line in a pull request.

---

## Validation rules enforced by `04_build_model.py`

- No null foreign keys in FACT_RATIOS.
- `CET1_FL` in [5%, 50%].
- `LCR` in [80%, 500%].
- `NPL_RATIO` in [0%, 30%].
- At least 24 distinct quarters covered (warning if fewer banks than 100 -
  expected when using the synthetic fallback).

Any violation raises an `AssertionError`, so a green run of script 04 means
the semantic model is publication-ready.

---

## Power BI configuration notes

- **Storage mode**: Import. No DirectQuery, no gateway, no Fabric.
- **Refresh**: manual, open `eba_dashboard.pbip` and press Home → Refresh
  after re-running the Python pipeline.
- **Theme**: registered in `eba_dashboard.Report/StaticResources` and wired
  into `report.json`; nothing to apply manually.
- **Auto recovery**: disabled in the `.pbip` on purpose; the render script
  force-kills Desktop and recovery would silently restore stale PBIR files.
- **Numeric formats** (defined per measure in the model):
  - Percent ratios: `0.0%` / `0.00%`; LCR and NSFR: `#,##0%`
  - bps deltas: `+#,##0;-#,##0;0`
  - Buffers: `+0.0%;-0.0%;0.0%`
  - BEUR amounts and ranks: `#,##0` / `0`

---

## Source-of-truth references

- CRR2 (Regulation (EU) 2019/876): consolidated text on EUR-Lex.
- EBA Risk Dashboard methodology notes (annexed to each quarterly release).
- ECB Data Portal KRI dataflow specification (SDMX-CSV).
- FSB list of Global Systemically Important Banks, November 2024.
