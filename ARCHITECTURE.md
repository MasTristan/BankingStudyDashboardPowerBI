# Architecture

Technical reference for the European Banking Regulatory Dashboard project.

## Stack

| Layer | Technology | Cost |
|---|---|---|
| Acquisition / cleaning | Python 3.11 + pandas + openpyxl + requests | Free |
| Modelling | CSV files on disk (star schema) | Free |
| Reporting | Power BI Desktop (Import mode) | Free |
| Versioning | Git + GitHub public repository | Free |

No SaaS service, no paid licence, no cloud egress. The Power BI report is
distributed as a `.pbix` file committed to the repository.

---

## Data flow

```
EBA Risk Dashboard (.xlsx)            EBA Transparency Exercise (.csv x 4)
        |                                            |
        v                                            v
 01_download_data.py  ---  data/raw/  --- 01_download_data.py
        |                                            |
        v                                            v
 02_clean_risk_dashboard.py               03_clean_transparency.py
        |                                            |
        +------------------> 04_build_model.py <-----+
                                     |
                                     v
                     data/processed/  (5 CSVs, star schema)
                                     |
                                     v
                       powerbi/eba_dashboard.pbix
```

Every step is idempotent: rerunning a script overwrites its outputs without
side effects on the prior stages.

---

## Star schema

### Fact: FACT_RATIOS

| Column | Type | Notes |
|---|---|---|
| ROW_ID | INT | Surrogate primary key |
| BANK_ID | INT | FK -> DIM_BANK |
| COUNTRY_CODE | VARCHAR(3) | FK -> DIM_COUNTRY |
| DATE_ID | INT | FK -> DIM_DATE |
| INDICATOR_ID | INT | FK -> DIM_INDICATOR |
| VALUE | DECIMAL(10,4) | Ratio in decimal form (0.155 = 15.5%) |
| SOURCE | VARCHAR(20) | `RISK_DASHBOARD` or `TRANSPARENCY` |
| REFERENCE_DATE | DATE | Exact EBA reference date |

Row counts after the bundled run:

| Source | Rows |
|---|---|
| RISK_DASHBOARD | 8 008 |
| TRANSPARENCY | 2 684 |
| **Total** | **10 692** |

### DIM_BANK

| Column | Notes |
|---|---|
| BANK_ID | Surrogate PK |
| BANK_CODE | EBA LEI/code (or `AGG_<country>` for aggregates) |
| BANK_NAME | Display name |
| COUNTRY_CODE | FK -> DIM_COUNTRY |
| BANK_SIZE | `LARGE` (>300 BEUR), `MEDIUM` (30-300), `SMALL` (<30), `AGGREGATE` |
| IS_GSIB | 1 if listed by the FSB 2024 G-SIB roster |
| TOTAL_ASSETS_BEUR | Total assets in billion EUR |

Country aggregates from the Risk Dashboard are stored as banks with
`BANK_SIZE = 'AGGREGATE'` so that all measures behave consistently.

### DIM_COUNTRY

| Column | Notes |
|---|---|
| COUNTRY_CODE | ISO 3166-1 alpha-2, plus `EU` for the EU/EEA aggregate |
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

Covers Q1 2018 to Q4 2024 (28 quarters).

### DIM_INDICATOR

| Column | Notes |
|---|---|
| INDICATOR_ID | Surrogate PK |
| INDICATOR_CODE | Short code used in FACT_RATIOS |
| INDICATOR_NAME | Full label |
| CATEGORY | `CAPITAL`, `LIQUIDITY`, `ASSET_QUALITY`, `PROFITABILITY`, `LEVERAGE` |
| UNIT | `PCT`, `RATIO`, `BEUR` |
| REGULATORY_MIN | Decimal threshold (e.g. 0.045 for CET1) |
| HIGHER_IS_BETTER | Drives conditional formatting direction |
| CRR2_REFERENCE | Article reference |

---

## Relations in Power BI

```
FACT_RATIOS[BANK_ID]      -> DIM_BANK[BANK_ID]            (many-to-one, single)
FACT_RATIOS[COUNTRY_CODE] -> DIM_COUNTRY[COUNTRY_CODE]    (many-to-one, single)
FACT_RATIOS[DATE_ID]      -> DIM_DATE[DATE_ID]            (many-to-one, single)
FACT_RATIOS[INDICATOR_ID] -> DIM_INDICATOR[INDICATOR_ID]  (many-to-one, single)
DIM_BANK[COUNTRY_CODE]    -> DIM_COUNTRY[COUNTRY_CODE]    (many-to-one, single)
```

All relations are single-direction; DIM_BANK[COUNTRY_CODE] -> DIM_COUNTRY
exists to keep country slicers working when filtering by bank.

---

## DAX measures

Organised under a `Measures` table in Power BI.

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

---

## Validation rules enforced by `04_build_model.py`

- No null foreign keys in FACT_RATIOS.
- CET1_FL in [5%, 50%].
- LCR in [80%, 500%].
- NPL_RATIO in [0%, 30%].
- At least 24 distinct quarters covered (warning if fewer banks than 100 -
  expected when using the synthetic fallback).

Any violation raises an `AssertionError`, so a green run of script 04 means the
star schema is publication-ready.

---

## Power BI configuration notes

- **Storage mode**: Import. No DirectQuery, no gateway.
- **Refresh**: manual, by reopening the `.pbix` or pressing Home -> Refresh
  after re-running the Python pipeline.
- **Theme**: JSON theme file applied via View -> Themes -> Browse.
- **Numeric formats**:
  - Percent ratios: `#,##0.0%`
  - bps deltas: `+#,##0;-#,##0`
  - BEUR amounts: `#,##0.0 "Bn EUR"`
  - Ranks: `0`

---

## Source-of-truth references

- CRR2 (Regulation (EU) 2019/876): consolidated text on EUR-Lex.
- EBA Risk Dashboard methodology notes (annexed to each quarterly release).
- FSB list of Global Systemically Important Banks, November 2024.
