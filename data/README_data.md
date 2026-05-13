# data/

## raw/

Source files downloaded from the ECB Data Portal and the EBA. Not committed
(see `.gitignore`), except for `INSPECTION_NOTES.md` which documents the
expected file shapes and refresh checklist.

Populate the folder with:

```
python python/01_download_data.py
```

Expected contents after a successful run against the live endpoints:

| File | Source |
|---|---|
| `ecb_kri.csv` | ECB Data Portal SDMX-CSV - EBA KRI dataflow |
| `tr_oth.csv` | EBA TE 2024 - capital, leverage, RWA, P&L, key metrics |
| `tr_cre.csv` | EBA TE 2024 - credit risk |
| `tr_mkt.csv` | EBA TE 2024 - market risk |
| `tr_sov.csv` | EBA TE 2024 - sovereign exposures |
| `TR_Metadata.xlsx` | EBA TE 2024 - Item / Label catalogue |
| `SDD.xlsx` | EBA TE 2024 - data dictionary |

When the endpoints are unreachable, the script writes a synthetic dataset
in the same shapes (SDMX-CSV + EAV) plus a `SYNTHETIC.flag` marker so it
cannot be confused with real data. The synthetic dataset covers 60 banks
across 26 countries to exercise every visual.

## processed/

Cleaned CSVs consumed by the Power BI semantic model. Committed to the repo.

| File | Description | Approx rows |
|---|---|---|
| `risk_indicators_clean.csv` | Country aggregates, long format | ~7 000 |
| `transparency_clean.csv` | Bank-by-bank ratios, long format | ~3 100 |
| `fact_ratios.csv` | Combined fact table for Power BI | ~10 000 |
| `dim_bank.csv` | Bank dimension (individual + country aggregates) | ~90 |
| `dim_country.csv` | Country dimension | 31 |
| `dim_date.csv` | Date dimension, quarterly | 28 |
| `dim_indicator.csv` | Indicator metadata with CRR2 references | 17 |

Schemas are detailed in `ARCHITECTURE.md`.
