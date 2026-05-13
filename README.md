# European Banking Regulatory Dashboard

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Data](https://img.shields.io/badge/Data-ECB%20%2F%20EBA%20public-orange)](https://www.eba.europa.eu/)

Power BI **semantic model** and report analysing prudential ratios of European
banks. Two public sources are combined into a single star schema:

- **ECB Data Portal - EBA Key Risk Indicators (KRI)** for quarterly country
  aggregates (Q1 2018 to Q4 2024).
- **EBA Transparency Exercise 2024** for bank-by-bank disclosures across
  123 institutions and 4 reference dates.

> Strict zero-paid-licence stack: Python 3, Power BI Desktop (free),
> GitHub (public). No Fabric, no Power BI Pro, no cloud egress.

---

## Key features

- **Two regulatory sources combined** in one star schema.
- **15 indicators** mapped to their CRR2 references (CET1, Tier 1, Total
  Capital, Leverage, LCR, NSFR, NPL, NPL Coverage, Forborne, ROE, ROA, CTI,
  NIM, RWA breakdown).
- **28 quarters** of trend data.
- **Bank-by-bank granularity** with G-SIB flag and size buckets.
- **Five-page report** built around the EBA visual language.
- **Reproducible pipeline**: four idempotent Python scripts produce all CSVs
  consumed by Power BI Desktop.

## Power BI 2025-2026 features used

This project doubles as a "Power BI after a year away" portfolio piece. The
following recently-released features are integrated visibly:

| Feature | Where it shows up |
|---|---|
| **Semantic Model** terminology | README, ARCHITECTURE, in-app model name |
| **New Card Visual** (GA Nov 2025) | Page 1 KPI cards with reference value + status |
| **Visual Calculations** (RUNNINGSUM, MOVINGAVERAGE, PREVIOUS) | Page 4 trend line |
| **DAX User-Defined Functions** (preview) | `RegBuffer(ratio, threshold)` |
| **DAX Query View** | Validation queries documented in ARCHITECTURE |
| **On-Object formatting** | Workflow used throughout the report |
| **Sparklines** | Page 3 bank table on CET1 and NPL columns |
| **Button Slicer** | Page 5 period selector |
| **Annotations** | Page 4 line chart (COVID 2020, ECB hikes 2022, Basel III 2023) |
| **TMDL-compatible layout** | Documented in ARCHITECTURE |

---

## Repository structure

```
.
+-- python/
|   +-- 01_download_data.py             ECB KRI + EBA TE download (synthetic fallback)
|   +-- 02_clean_risk_indicators.py     SDMX-CSV cleaner (ECB)
|   +-- 03_clean_transparency.py        EAV cleaner (EBA TE 2024)
|   +-- 04_build_model.py               Star schema builder
|   +-- requirements.txt
+-- data/
|   +-- raw/                            Source files (gitignored)
|   |   +-- INSPECTION_NOTES.md         Mandatory inspection notes (committed)
|   +-- processed/                      Cleaned CSVs (committed)
|   |   +-- fact_ratios.csv
|   |   +-- dim_bank.csv
|   |   +-- dim_country.csv
|   |   +-- dim_date.csv
|   |   +-- dim_indicator.csv
|   +-- README_data.md
+-- powerbi/
|   +-- eba_dashboard.pbix              Report file (built locally)
|   +-- theme.json                      EBA Banking colour theme
|   +-- screenshots/                    Page screenshots for README / LinkedIn
+-- ARCHITECTURE.md
+-- README.md
+-- requirements.txt
+-- LICENSE
```

---

## Data sources

| Source | Granularity | Coverage | URL |
|---|---|---|---|
| ECB Data Portal - EBA KRI dataflow | Country aggregate + EU/EEA aggregate, quarterly | 2018-Q1 to 2024-Q4 | [data.ecb.europa.eu](https://data.ecb.europa.eu/data/datasets/KRI) |
| EBA Transparency Exercise 2024 (full database) | Bank-by-bank, 4 reference dates | Sep 2023 to Jun 2024, 123 banks, 26 countries | [eba.europa.eu](https://www.eba.europa.eu/risk-and-data-analysis/risk-analysis/eu-wide-transparency-exercise/2024-eu-wide-transparency-exercise) |

The Risk Dashboard itself (the EBA quarterly PDF) is illustrative only —
the structured data behind it is published on the ECB Data Portal as the
**Key Risk Indicators (KRI)** SDMX dataflow, which is what the pipeline
ingests.

### Synthetic fallback

When the ECB / EBA endpoints cannot be reached, `01_download_data.py`
materialises a deterministic synthetic dataset that follows the exact same
shapes (SDMX-CSV for KRI, EAV for TE 2024) and is clearly tagged via a
`SYNTHETIC.flag` file. See `data/raw/INSPECTION_NOTES.md` for the column
contracts and refresh checklist.

---

## Indicators covered

| Code | Name | Category | Min | CRR2 |
|---|---|---|---|---|
| CET1_FL | Common Equity Tier 1 ratio (fully loaded) | Capital | 4.5% | Art. 50 |
| TIER1_FL | Tier 1 ratio (fully loaded) | Capital | 6.0% | Art. 25 |
| TOTAL_CAP_FL | Total capital ratio | Capital | 8.0% | Art. 92 |
| RWA_TOTAL | Total RWA (BEUR) | Capital | - | Art. 92 |
| RWA_CREDIT | RWA credit risk (% of total) | Capital | - | Art. 112 |
| RWA_MARKET | RWA market risk (% of total) | Capital | - | Art. 325 |
| RWA_OP | RWA operational risk (% of total) | Capital | - | Art. 312 |
| LCR | Liquidity Coverage Ratio | Liquidity | 100% | Art. 412 |
| NSFR | Net Stable Funding Ratio | Liquidity | 100% | Art. 428b |
| NPL_RATIO | Non-performing loans ratio (gross) | Asset quality | - | EBA NPL GL |
| NPL_COVERAGE | NPL coverage ratio | Asset quality | - | EBA NPL GL |
| FORBORNE_RATIO | Forborne exposures ratio | Asset quality | - | EBA GL 2018 |
| ROE | Return on equity | Profitability | - | - |
| ROA | Return on assets | Profitability | - | - |
| CTI | Cost-to-income ratio | Profitability | - | - |
| NIM | Net interest margin | Profitability | - | - |
| LEV_RATIO | Leverage ratio | Leverage | 3% | Art. 429 |

---

## Setup

### 1. Python pipeline

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python python/01_download_data.py         # ECB KRI + EBA TE 2024 (or synthetic)
python python/02_clean_risk_indicators.py
python python/03_clean_transparency.py
python python/04_build_model.py           # star schema + validation gates
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python python/01_download_data.py
python python/02_clean_risk_indicators.py
python python/03_clean_transparency.py
python python/04_build_model.py
```

After the four scripts complete, `data/processed/` contains the five CSVs
consumed by Power BI.

### 2. Power BI Desktop

1. Open `powerbi/eba_dashboard.pbix`.
2. Apply the colour theme: **View → Themes → Browse for themes** →
   `powerbi/theme.json`.
3. Refresh: **Home → Refresh** (after rerunning the Python pipeline).

Semantic model relations:

```
FACT_RATIOS[BANK_ID]      -> DIM_BANK[BANK_ID]            (many-to-one)
FACT_RATIOS[COUNTRY_CODE] -> DIM_COUNTRY[COUNTRY_CODE]    (many-to-one)
FACT_RATIOS[DATE_ID]      -> DIM_DATE[DATE_ID]            (many-to-one)
FACT_RATIOS[INDICATOR_ID] -> DIM_INDICATOR[INDICATOR_ID]  (many-to-one)
DIM_BANK[COUNTRY_CODE]    -> DIM_COUNTRY[COUNTRY_CODE]    (many-to-one)
```

---

## Report pages

| # | Page | Highlights |
|---|---|---|
| 1 | Executive Overview | New Card Visual KPIs, CET1 trend with reg-min line, country bar |
| 2 | Country Comparison | Filled map, small multiples, scatter CET1 vs NPL |
| 3 | Bank Rankings | Top 20 horizontal bar, scatter CET1 vs LCR, table with sparklines |
| 4 | Trend Analysis | Visual Calculations (running sum, moving avg, previous), annotations |
| 5 | Regulatory Heatmap | Country x indicator matrix, Button Slicer for period |

---

## Author

**Tristan Mas** - Business Analyst, Risk & Finance IT.

Available for consulting engagements in regulated finance.
