# data/

## raw/

Source files downloaded from the European Banking Authority. Not committed
(see `.gitignore`). Populate it by running:

```
python python/01_download_data.py
```

Expected contents after a successful run against live EBA URLs:

| File | Source |
|---|---|
| `risk_dashboard_q4_2024.xlsx` | EBA Risk Dashboard Q4 2024 (data file) |
| `transparency_2024_capital.csv` | TE 2024 - Capital & RWA |
| `transparency_2024_asset_quality.csv` | TE 2024 - Asset quality |
| `transparency_2024_profitability.csv` | TE 2024 - Profitability |
| `transparency_2024_leverage.csv` | TE 2024 - Leverage & liquidity |

When the EBA endpoint is unreachable, the script writes a synthetic dataset
plus a `SYNTHETIC.flag` marker so it cannot be confused with real data.

## processed/

Cleaned CSVs consumed by Power BI. Committed to the repository.

| File | Description | Approx rows |
|---|---|---|
| `risk_dashboard_clean.csv` | Country aggregates, long format | ~8 000 |
| `transparency_clean.csv` | Bank-by-bank ratios, long format | ~2 700 |
| `fact_ratios.csv` | Combined fact table for Power BI | ~10 700 |
| `dim_bank.csv` | Bank dimension (individual + country aggregates) | ~90 |
| `dim_country.csv` | Country dimension | 31 |
| `dim_date.csv` | Date dimension, quarterly | 28 |
| `dim_indicator.csv` | Indicator metadata with CRR2 references | 17 |

Schemas are detailed in `ARCHITECTURE.md`.
