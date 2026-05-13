# INSPECTION_NOTES.md

Mandatory inspection step before coding the cleaners, per the project brief.

## Why this file exists

The two upstream sources (ECB Data Portal SDMX-CSV, EBA Transparency Exercise
CSV) drift between vintages. Coding transformations against assumed column
names is a frequent cause of pipeline rot. This file documents what each
cleaner expects to find and provides a re-inspection checklist for whoever
runs the pipeline against fresh files.

---

## Source 1 - ECB KRI (data-api.ecb.europa.eu)

**Endpoint**
```
https://data-api.ecb.europa.eu/service/data/KRI?format=csvdata&startPeriod=2018-Q1&endPeriod=2024-Q4
```

**Expected SDMX-CSV columns** (varies between releases)

| Column | Role | Notes |
|---|---|---|
| `KEY` | Full series key | dot-separated dimensions |
| `FREQ` | Frequency | `Q` for quarterly |
| `REF_AREA` | Country | ISO-2 letters; `U2` for EU/EEA aggregate |
| `KRI_IND` / `INDICATOR` / `KEY_FAMILY` | Indicator code or family | varies |
| `TIME_PERIOD` | Period | `YYYY-Qn` |
| `OBS_VALUE` | Observation value | numeric |
| `OBS_STATUS` | Quality flag | `A` = approved |
| `UNIT_MEASURE` | Unit | `PC` = percent, `PCPA` = percent per annum |

The 02 cleaner auto-detects the indicator and value columns (`_detect_*`
helpers in `02_clean_risk_indicators.py`). Indicator labels are mapped to
canonical codes (`CET1_FL`, `LCR`, etc.) via case-insensitive substring
matching — see `INDICATOR_PATTERNS`.

**Re-inspection checklist when refreshing the file**

1. Open `data/raw/ecb_kri.csv` and confirm the column set above.
2. List the unique values of the indicator column:
   `python -c "import pandas as pd; print(pd.read_csv('data/raw/ecb_kri.csv')['KRI_IND'].unique())"`
3. If any new indicator labels appear, add a `(pattern, code)` tuple to
   `INDICATOR_PATTERNS` in `02_clean_risk_indicators.py`.

---

## Source 2 - EBA Transparency Exercise 2024 (eba.europa.eu)

**Files**

| File | Content |
|---|---|
| `tr_oth.csv` | Capital, leverage, RWA, P&L, key metrics (CET1, LCR, NSFR) |
| `tr_cre.csv` | Credit risk: NPL ratio, forborne, coverage, NACE breakdowns |
| `tr_mkt.csv` | Market risk |
| `tr_sov.csv` | Sovereign exposures |
| `TR_Metadata.xlsx` | Item → Label catalogue |
| `SDD.xlsx` | Data dictionary |

For prudential ratios only `tr_oth.csv` and `tr_cre.csv` are used.

**EAV column set** (confirmed against the brief)

| Column | Role |
|---|---|
| `LEI_code` | Bank LEI (20 chars in production, alias codes in the synthetic fallback) |
| `NSA` | Country of the bank (ISO-2) |
| `Period` | `YYYYMM`, e.g. `202406` |
| `Item` | Indicator code from the EBA template |
| `Label` | Human-readable indicator label |
| `Portfolio` | Risk approach (`a0` = total, STA, IRB, ...) |
| `Country` | Country of the counterparty (Total = aggregate exposure) |
| `Country_rank` | 1 = top exposure, blank for aggregates |
| `Exposure` | Sector/segment |
| `Status` | Default status |
| `Perf_Status` | Performing / non-performing |
| `NACE_codes` | Activity code |
| `Amount` | Numeric value |
| `Footnote` | Optional |
| `Row`/`Column`/`Sheet` | Source cell in the template |

**Encoding**: UTF-8 (CP65001). Reading with `encoding='utf-8'` works
for both the live EBA files and the synthetic fallback.

**Aggregate-row filter** used by 03_clean_transparency.py

We keep only rows that represent the bank-wide aggregate for that indicator:

| Dimension | Accepted values |
|---|---|
| Portfolio | `""`, `a0`, `Total`, `TOT`, `T` |
| Country | `""`, `Total`, `TOT`, `T` |
| Country_rank | `""`, `1` |

If EBA introduces new aggregate tokens, extend `AGG_PORTFOLIOS`,
`AGG_COUNTRIES`, `AGG_COUNTRY_RANK` in `03_clean_transparency.py`.

**Item code mapping**

Item codes drift between exercises (e.g. `KM01` for CET1 in 2024 may move to
`KM010` next exercise). The cleaner therefore uses **label-based matching**
as the primary strategy: substrings such as `"common equity tier 1 ratio"`
are matched against `Label` regardless of the Item code, with metadata from
`TR_Metadata.xlsx` consulted first when available. See `INDICATOR_LABELS`
in `03_clean_transparency.py`.

**Re-inspection checklist when refreshing the files**

1. Open `data/raw/tr_oth.csv` and verify the column order matches the table
   above. Adjust `EAV_COLUMNS` only if EBA reorders.
2. Inspect the unique values of `Portfolio`, `Country`, `Country_rank` to
   catch new aggregate tokens.
3. Sample 20 rows: `python -c "import pandas as pd; print(pd.read_csv('data/raw/tr_oth.csv', nrows=20).to_string())"`.
4. Open `TR_Metadata.xlsx` and confirm there is a sheet with `Item` and
   `Label` columns. Cross-check that all canonical indicators in
   `INDICATOR_LABELS` find at least one Item match — the cleaner logs
   `resolved N Item -> INDICATOR_CODE entries`.

---

## Inspection outcome for this repository

| Source | Status | Notes |
|---|---|---|
| ECB KRI live download | Blocked by sandbox (HTTP 403 host_not_allowed) | Synthetic SDMX-CSV generated |
| EBA TE live download | Blocked by sandbox (HTTP 403 host_not_allowed) | Synthetic EAV CSVs + TR_Metadata.xlsx generated |

The synthetic dataset matches the documented schemas above so that the
cleaners exercise the same code paths. A `SYNTHETIC.flag` file is placed
beside the raw files for unambiguous tagging. Real data refresh: drop the
official files into `data/raw/` and re-run `python python/01_download_data.py`
followed by 02, 03, 04 — `download_file()` is idempotent and will leave any
real file already present in place.

---

## Validation gates enforced downstream

`python/04_build_model.py` raises `AssertionError` on any of:

- Null foreign key in `FACT_RATIOS`.
- `CET1_FL` value outside `[0.05, 0.50]`.
- `LCR` value outside `[0.80, 5.00]`.
- `NPL_RATIO` value outside `[0.00, 0.30]`.
- Fewer than 24 distinct quarters in `DIM_DATE`.

A green run of 04 is the implicit signal that all inspection assumptions
above still hold.
