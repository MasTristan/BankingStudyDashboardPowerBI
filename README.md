# European Banking Regulatory Dashboard

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Data](https://img.shields.io/badge/Data-EBA%20public-orange)](https://www.eba.europa.eu/)

Power BI dashboard analysing prudential ratios of European banks using EBA
public data: the quarterly **EBA Risk Dashboard** (country aggregates) combined
with the **EBA Transparency Exercise 2024** (bank-by-bank disclosures).

> Built with a strict zero-paid-licence stack: Python 3, Power BI Desktop
> (free), GitHub (public).

---

## Key features

- **Two EBA sources combined** in a single star schema model.
- **15 regulatory indicators** covering capital, liquidity, asset quality,
  profitability and leverage, mapped to their CRR2 references.
- **28 quarters** of trend data (Q1 2018 -> Q4 2024).
- **Bank-by-bank granularity** with G-SIB flag and size buckets.
- **Five-page report**: executive overview, country comparison, bank
  rankings, trend analysis, regulatory heatmap.
- **Reproducible pipeline**: four Python scripts produce all CSVs consumed by
  Power BI.

---

## Repository structure

```
.
+-- python/                       Python pipeline
|   +-- 01_download_data.py       Download EBA datasets (with synthetic fallback)
|   +-- 02_clean_risk_dashboard.py
|   +-- 03_clean_transparency.py
|   +-- 04_build_model.py         Build star schema CSVs
|   +-- requirements.txt
+-- data/
|   +-- raw/                      EBA source files (gitignored)
|   +-- processed/                Cleaned CSVs consumed by Power BI
|   |   +-- fact_ratios.csv
|   |   +-- dim_bank.csv
|   |   +-- dim_country.csv
|   |   +-- dim_date.csv
|   |   +-- dim_indicator.csv
|   +-- README_data.md
+-- powerbi/
|   +-- eba_dashboard.pbix        Power BI report (to be built locally)
|   +-- screenshots/              Page screenshots embedded in README
+-- ARCHITECTURE.md
+-- README.md
+-- requirements.txt
+-- LICENSE
```

---

## Data sources

| Source | Granularity | Coverage | URL |
|---|---|---|---|
| EBA Risk Dashboard Q4 2024 | Country aggregate, quarterly | Q1 2014 -> Q4 2024 | [eba.europa.eu](https://www.eba.europa.eu/risk-and-data-analysis/risk-analysis/risk-monitoring/risk-dashboard) |
| EBA Transparency Exercise 2024 | Bank-by-bank, 4 reference dates | Sep 2023 -> Jun 2024, 123 banks, 26 countries | [eba.europa.eu](https://www.eba.europa.eu/risk-and-data-analysis/risk-analysis/eu-wide-transparency-exercise/2024-eu-wide-transparency-exercise) |

The download script keeps a copy under `data/raw/` and is idempotent.

### Synthetic fallback

EBA serves its files behind a CDN that occasionally rejects automated requests.
When `01_download_data.py` cannot reach the live URLs, it materialises a
deterministic synthetic dataset that follows the same schema and ratio ranges.
The fallback is clearly tagged via a `SYNTHETIC.flag` file in `data/raw/` so it
cannot be confused with regulatory data. Production users should drop the
official EBA files in `data/raw/` and re-run the pipeline.

---

## Indicators covered

| Code | Name | Category | Min | CRR2 reference |
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

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python python/01_download_data.py
python python/02_clean_risk_dashboard.py
python python/03_clean_transparency.py
python python/04_build_model.py
```

After the four scripts complete, `data/processed/` contains the five CSVs
consumed by Power BI.

### 2. Power BI report

Open `powerbi/eba_dashboard.pbix` in Power BI Desktop (free). The model
relations are:

```
FACT_RATIOS[BANK_ID]      -> DIM_BANK[BANK_ID]            (many-to-one)
FACT_RATIOS[COUNTRY_CODE] -> DIM_COUNTRY[COUNTRY_CODE]    (many-to-one)
FACT_RATIOS[DATE_ID]      -> DIM_DATE[DATE_ID]            (many-to-one)
FACT_RATIOS[INDICATOR_ID] -> DIM_INDICATOR[INDICATOR_ID]  (many-to-one)
DIM_BANK[COUNTRY_CODE]    -> DIM_COUNTRY[COUNTRY_CODE]    (many-to-one)
```

If you replace the CSVs, click **Home -> Refresh** to reload the model.

---

## Report pages

| # | Page | Purpose |
|---|---|---|
| 1 | Executive Overview | EU-wide KPI cards, CET1 trend, country bar chart |
| 2 | Country Comparison | Choropleth map, small multiples, scatter CET1 vs NPL |
| 3 | Bank Rankings | Top 20 by CET1, scatter CET1 vs LCR, full bank table |
| 4 | Trend Analysis | Multi-indicator time series, RWA breakdown, regional NPL |
| 5 | Regulatory Heatmap | Compliance heatmap country x indicator |

Screenshots live in `powerbi/screenshots/` and are referenced inline once the
report is exported.

---

## Star schema

See [ARCHITECTURE.md](ARCHITECTURE.md) for full table definitions, relations,
and the DAX measure catalogue.

---

## Author

**Tristan Mas** - Business Analyst, Risk & Finance IT.

Available for consulting engagements in regulated finance.
