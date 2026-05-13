# Architecture

Technical reference for the European Banking Regulatory Dashboard project.

## Stack

| Layer | Technology | Cost |
|---|---|---|
| Acquisition / cleaning | Python 3.11 + pandas + openpyxl + requests | Free |
| Modelling | CSV files on disk, star schema | Free |
| Reporting | Power BI Desktop (Import mode), semantic model | Free |
| Versioning | Git + GitHub public repository | Free |

No SaaS service, no paid licence, no cloud egress. The Power BI report is
distributed as a `.pbix` file committed to the repository.

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
                    powerbi/eba_dashboard.pbix
                       (semantic model)
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

Measures live under a dedicated `Measures` table in the semantic model.

### Base

```dax
Avg Ratio        = AVERAGE(FACT_RATIOS[VALUE])
Latest Value     = CALCULATE([Avg Ratio], LASTDATE(DIM_DATE[REFERENCE_DATE]))
Nb Banks         = DISTINCTCOUNT(FACT_RATIOS[BANK_ID])
Nb Countries     = DISTINCTCOUNT(FACT_RATIOS[COUNTRY_CODE])
```

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

Avg Regulatory Buffer =
[Latest Value] - MAX(DIM_INDICATOR[REGULATORY_MIN])

Compliance Status =
VAR Threshold = MAX(DIM_INDICATOR[REGULATORY_MIN])
RETURN IF(
    ISBLANK(Threshold), "N/A",
    IF([Latest Value] >= Threshold, "COMPLIANT", "BELOW MINIMUM")
)
```

### DAX User-Defined Function (preview, Sep 2025)

```dax
FUNCTION RegBuffer(RatioValue AS DOUBLE, MinThreshold AS DOUBLE)
    RETURN RatioValue - MinThreshold

CET1 Buffer = RegBuffer([Latest Value], 0.045)
LCR Buffer  = RegBuffer([Latest Value], 1.00)
```

When the installed Power BI Desktop build does not yet support `FUNCTION`,
the equivalent inline arithmetic is used and the intention is documented in
the report description.

---

## Power BI 2025-2026 features integrated

### New Card Visual (GA Nov 2025)
Page 1 KPI cards use the new card visual exclusively. Each card shows:
- Callout value (e.g. CET1 ratio current quarter)
- Reference value (regulatory minimum from DIM_INDICATOR)
- Status badge driven by `Compliance Status`
- YoY arrow (driven by `YoY Change bps`)

### Visual Calculations (GA late 2024)
Page 4 line chart uses three Visual Calculations defined directly on the
visual via *Add visual calculation*, replacing model-level moving-average
mesures:

```dax
Running CET1            = RUNNINGSUM([Avg Ratio])
Rolling 4Q              = MOVINGAVERAGE([Avg Ratio], 4)
vs Previous Quarter     = [Avg Ratio] - PREVIOUS([Avg Ratio])
```

### DAX Query View

Used for offline validation. Example query stored in the report metadata:

```dax
EVALUATE
CALCULATETABLE(
    SUMMARIZECOLUMNS(
        DIM_COUNTRY[COUNTRY_NAME],
        DIM_INDICATOR[INDICATOR_CODE],
        "Latest Value",        [Latest Value],
        "YoY Change bps",      [YoY Change bps],
        "Regulatory Buffer",   [Avg Regulatory Buffer]
    ),
    DIM_INDICATOR[INDICATOR_CODE] = "CET1_FL",
    DIM_DATE[YEAR_QUARTER]        = "2024-Q4"
)
ORDER BY [Latest Value] DESC
```

### Sparklines
Page 3 bank table embeds line-type sparklines on the `CET1_FL` and
`NPL_RATIO` columns, four points (Q3 2023 → Q2 2024), auto-axis.

### Button Slicer
Page 5 uses Button Slicers for period selection (style: rectangular,
active colour `#0070C0`).

### Annotations
Page 4 line chart carries point annotations:
- Q1 2020 – "COVID-19 impact"
- Q2 2022 – "ECB rate hike cycle begins"
- Q4 2023 – "Basel III final rules (CRR3)"

### On-Object formatting
All visual-level styling is performed via On-Object editing
(direct click on the element) rather than the legacy Format pane.

---

## TMDL Compatibility

This semantic model follows naming conventions and structural patterns
compatible with **TMDL** (Tabular Model Definition Language), the format used
by Microsoft Fabric Git integration for version control of Power BI semantic
models.

Specifically:

- `UPPER_SNAKE_CASE` table names and DAX-friendly column identifiers (no
  spaces inside the model objects).
- Measures grouped under a dedicated `Measures` table - matches the
  `measures/` folder pattern produced by Fabric's TMDL export.
- Relationships defined with explicit cardinality and direction so that
  the TMDL `.relationships` files round-trip cleanly.
- Calculated columns kept minimal in favour of upstream cleanup in Python,
  reducing TMDL diff noise when iterating on the visual layer.

In a Fabric-enabled environment, the model would be exported as a
folder-based TMDL structure (`tables/`, `measures/`, `relationships/`),
enabling PR-based review workflows and conflict resolution on individual
measure files rather than a monolithic JSON blob.

The project does not deploy to Fabric (zero-paid-licence constraint) and
remains entirely on Power BI Desktop. Migration to Fabric is the intended
"next level".

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
- **Refresh**: manual, by reopening the `.pbix` or pressing Home → Refresh
  after re-running the Python pipeline.
- **Theme**: `powerbi/theme.json` applied via View → Themes → Browse.
- **Numeric formats**:
  - Percent ratios: `#,##0.0%`
  - bps deltas: `+#,##0;-#,##0`
  - BEUR amounts: `#,##0.0 "Bn EUR"`
  - Ranks: `0`

---

## Source-of-truth references

- CRR2 (Regulation (EU) 2019/876): consolidated text on EUR-Lex.
- EBA Risk Dashboard methodology notes (annexed to each quarterly release).
- ECB Data Portal KRI dataflow specification (SDMX-CSV).
- FSB list of Global Systemically Important Banks, November 2024.
