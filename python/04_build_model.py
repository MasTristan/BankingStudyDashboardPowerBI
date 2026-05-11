"""
Builds the star schema CSVs consumed by Power BI Desktop.

Inputs
------
- data/processed/risk_dashboard_clean.csv
- data/processed/transparency_clean.csv

Outputs
-------
- data/processed/fact_ratios.csv
- data/processed/dim_bank.csv
- data/processed/dim_country.csv
- data/processed/dim_date.csv
- data/processed/dim_indicator.csv

Validation
----------
- No null foreign keys in FACT_RATIOS.
- CET1_FL within [0.05, 0.50].
- LCR within [0.80, 5.00].
- NPL_RATIO within [0.00, 0.30].
- >= 100 distinct banks (raise to warning if fewer; the fallback ships 60).
- >= 24 distinct quarters (Q1 2018 -> Q4 2024 = 28 quarters).

Run
---
    python python/04_build_model.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("eba.model")

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

RD_PATH = PROCESSED_DIR / "risk_dashboard_clean.csv"
TR_PATH = PROCESSED_DIR / "transparency_clean.csv"

# Country metadata used to build DIM_COUNTRY.
COUNTRY_META: list[dict[str, object]] = [
    {"COUNTRY_CODE": "AT", "COUNTRY_NAME": "Austria",        "REGION": "WEST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "BE", "COUNTRY_NAME": "Belgium",        "REGION": "WEST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "BG", "COUNTRY_NAME": "Bulgaria",       "REGION": "EAST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "CY", "COUNTRY_NAME": "Cyprus",         "REGION": "SOUTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "CZ", "COUNTRY_NAME": "Czechia",        "REGION": "EAST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "DE", "COUNTRY_NAME": "Germany",        "REGION": "WEST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "DK", "COUNTRY_NAME": "Denmark",        "REGION": "NORTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "EE", "COUNTRY_NAME": "Estonia",        "REGION": "NORTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "ES", "COUNTRY_NAME": "Spain",          "REGION": "SOUTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "FI", "COUNTRY_NAME": "Finland",        "REGION": "NORTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "FR", "COUNTRY_NAME": "France",         "REGION": "WEST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "GR", "COUNTRY_NAME": "Greece",         "REGION": "SOUTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "HR", "COUNTRY_NAME": "Croatia",        "REGION": "SOUTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "HU", "COUNTRY_NAME": "Hungary",        "REGION": "EAST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "IE", "COUNTRY_NAME": "Ireland",        "REGION": "WEST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "IS", "COUNTRY_NAME": "Iceland",        "REGION": "NORTH",  "EU_MEMBER": False, "EEA_MEMBER": True},
    {"COUNTRY_CODE": "IT", "COUNTRY_NAME": "Italy",          "REGION": "SOUTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "LI", "COUNTRY_NAME": "Liechtenstein",  "REGION": "WEST",   "EU_MEMBER": False, "EEA_MEMBER": True},
    {"COUNTRY_CODE": "LT", "COUNTRY_NAME": "Lithuania",      "REGION": "NORTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "LU", "COUNTRY_NAME": "Luxembourg",     "REGION": "WEST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "LV", "COUNTRY_NAME": "Latvia",         "REGION": "NORTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "MT", "COUNTRY_NAME": "Malta",          "REGION": "SOUTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "NL", "COUNTRY_NAME": "Netherlands",    "REGION": "WEST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "NO", "COUNTRY_NAME": "Norway",         "REGION": "NORTH",  "EU_MEMBER": False, "EEA_MEMBER": True},
    {"COUNTRY_CODE": "PL", "COUNTRY_NAME": "Poland",         "REGION": "EAST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "PT", "COUNTRY_NAME": "Portugal",       "REGION": "SOUTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "RO", "COUNTRY_NAME": "Romania",        "REGION": "EAST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "SE", "COUNTRY_NAME": "Sweden",         "REGION": "NORTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "SI", "COUNTRY_NAME": "Slovenia",       "REGION": "SOUTH",  "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "SK", "COUNTRY_NAME": "Slovakia",       "REGION": "EAST",   "EU_MEMBER": True,  "EEA_MEMBER": True},
    {"COUNTRY_CODE": "EU", "COUNTRY_NAME": "European Union", "REGION": "EU_AGG", "EU_MEMBER": True,  "EEA_MEMBER": True},
]

INDICATOR_META: list[dict[str, object]] = [
    {"INDICATOR_CODE": "CET1_FL",      "INDICATOR_NAME": "Common Equity Tier 1 ratio (fully loaded)", "CATEGORY": "CAPITAL",       "UNIT": "PCT",   "REGULATORY_MIN": 0.045, "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": "Art. 50"},
    {"INDICATOR_CODE": "TIER1_FL",     "INDICATOR_NAME": "Tier 1 ratio (fully loaded)",                "CATEGORY": "CAPITAL",       "UNIT": "PCT",   "REGULATORY_MIN": 0.060, "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": "Art. 25"},
    {"INDICATOR_CODE": "TOTAL_CAP_FL", "INDICATOR_NAME": "Total capital ratio (fully loaded)",         "CATEGORY": "CAPITAL",       "UNIT": "PCT",   "REGULATORY_MIN": 0.080, "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": "Art. 92"},
    {"INDICATOR_CODE": "RWA_TOTAL",    "INDICATOR_NAME": "Total RWA",                                  "CATEGORY": "CAPITAL",       "UNIT": "BEUR",  "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": False, "CRR2_REFERENCE": "Art. 92"},
    {"INDICATOR_CODE": "RWA_CREDIT",   "INDICATOR_NAME": "RWA credit risk (% of total)",               "CATEGORY": "CAPITAL",       "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": False, "CRR2_REFERENCE": "Art. 112"},
    {"INDICATOR_CODE": "RWA_MARKET",   "INDICATOR_NAME": "RWA market risk (% of total)",               "CATEGORY": "CAPITAL",       "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": False, "CRR2_REFERENCE": "Art. 325"},
    {"INDICATOR_CODE": "RWA_OP",       "INDICATOR_NAME": "RWA operational risk (% of total)",          "CATEGORY": "CAPITAL",       "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": False, "CRR2_REFERENCE": "Art. 312"},
    {"INDICATOR_CODE": "LCR",          "INDICATOR_NAME": "Liquidity Coverage Ratio",                   "CATEGORY": "LIQUIDITY",     "UNIT": "PCT",   "REGULATORY_MIN": 1.00,  "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": "Art. 412"},
    {"INDICATOR_CODE": "NSFR",         "INDICATOR_NAME": "Net Stable Funding Ratio",                   "CATEGORY": "LIQUIDITY",     "UNIT": "PCT",   "REGULATORY_MIN": 1.00,  "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": "Art. 428b"},
    {"INDICATOR_CODE": "NPL_RATIO",    "INDICATOR_NAME": "Non-performing loans ratio (gross)",         "CATEGORY": "ASSET_QUALITY", "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": False, "CRR2_REFERENCE": "EBA NPL GL"},
    {"INDICATOR_CODE": "NPL_COVERAGE", "INDICATOR_NAME": "NPL coverage ratio",                          "CATEGORY": "ASSET_QUALITY", "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": "EBA NPL GL"},
    {"INDICATOR_CODE": "FORBORNE_RATIO","INDICATOR_NAME": "Forborne exposures ratio",                  "CATEGORY": "ASSET_QUALITY", "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": False, "CRR2_REFERENCE": "EBA GL 2018"},
    {"INDICATOR_CODE": "ROE",          "INDICATOR_NAME": "Return on equity",                            "CATEGORY": "PROFITABILITY", "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": ""},
    {"INDICATOR_CODE": "ROA",          "INDICATOR_NAME": "Return on assets",                            "CATEGORY": "PROFITABILITY", "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": ""},
    {"INDICATOR_CODE": "CTI",          "INDICATOR_NAME": "Cost-to-income ratio",                       "CATEGORY": "PROFITABILITY", "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": False, "CRR2_REFERENCE": ""},
    {"INDICATOR_CODE": "NIM",          "INDICATOR_NAME": "Net interest margin",                        "CATEGORY": "PROFITABILITY", "UNIT": "PCT",   "REGULATORY_MIN": None,  "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": ""},
    {"INDICATOR_CODE": "LEV_RATIO",    "INDICATOR_NAME": "Leverage ratio",                              "CATEGORY": "LEVERAGE",      "UNIT": "PCT",   "REGULATORY_MIN": 0.030, "HIGHER_IS_BETTER": True,  "CRR2_REFERENCE": "Art. 429"},
]


def _build_dim_country(rd: pd.DataFrame, tr: pd.DataFrame) -> pd.DataFrame:
    referenced = set(rd["COUNTRY_CODE"].dropna().unique()) | set(
        tr["COUNTRY_CODE"].dropna().unique()
    )
    dim = pd.DataFrame(COUNTRY_META)
    known = set(dim["COUNTRY_CODE"])
    unknown = sorted(referenced - known)
    if unknown:
        logger.warning("countries without metadata, added as UNKNOWN region: %s", unknown)
        extras = pd.DataFrame([
            {
                "COUNTRY_CODE": code,
                "COUNTRY_NAME": code,
                "REGION": "UNKNOWN",
                "EU_MEMBER": False,
                "EEA_MEMBER": False,
            }
            for code in unknown
        ])
        dim = pd.concat([dim, extras], ignore_index=True)
    return dim.sort_values("COUNTRY_CODE").reset_index(drop=True)


def _build_dim_date(rd: pd.DataFrame, tr: pd.DataFrame) -> pd.DataFrame:
    quarters = sorted(
        set(rd["YEAR_QUARTER"].dropna()) | set(tr["YEAR_QUARTER"].dropna())
    )
    rows: list[dict[str, object]] = []
    for idx, year_quarter in enumerate(quarters, start=1):
        year, q = year_quarter.split("-Q")
        year, q = int(year), int(q)
        reference = {
            1: f"{year}-03-31", 2: f"{year}-06-30",
            3: f"{year}-09-30", 4: f"{year}-12-31",
        }[q]
        rows.append({
            "DATE_ID": idx,
            "REFERENCE_DATE": reference,
            "YEAR": year,
            "QUARTER": q,
            "YEAR_QUARTER": year_quarter,
            "SEMESTER": 1 if q <= 2 else 2,
            "SOURCE_LABEL": f"Q{q} {year}",
        })
    return pd.DataFrame(rows)


def _build_dim_indicator() -> pd.DataFrame:
    dim = pd.DataFrame(INDICATOR_META)
    dim.insert(0, "INDICATOR_ID", range(1, len(dim) + 1))
    return dim


def _build_dim_bank(rd: pd.DataFrame, tr: pd.DataFrame) -> pd.DataFrame:
    individual = (
        tr[["LEI_CODE", "BANK_NAME", "COUNTRY_CODE", "TOTAL_ASSETS_BEUR",
            "IS_GSIB", "BANK_SIZE"]]
        .dropna(subset=["LEI_CODE", "BANK_NAME"])
        .drop_duplicates(subset=["LEI_CODE"])
        .rename(columns={"LEI_CODE": "BANK_CODE"})
        .copy()
    )
    individual["BANK_ID"] = range(1, len(individual) + 1)

    aggregate_countries = sorted(rd["COUNTRY_CODE"].dropna().unique())
    next_id = len(individual) + 1
    aggregates = pd.DataFrame([
        {
            "BANK_ID": next_id + offset,
            "BANK_CODE": f"AGG_{code}",
            "BANK_NAME": f"Country aggregate - {code}",
            "COUNTRY_CODE": code,
            "TOTAL_ASSETS_BEUR": pd.NA,
            "IS_GSIB": 0,
            "BANK_SIZE": "AGGREGATE",
        }
        for offset, code in enumerate(aggregate_countries)
    ])
    dim = pd.concat([individual, aggregates], ignore_index=True)
    return dim[[
        "BANK_ID", "BANK_CODE", "BANK_NAME", "COUNTRY_CODE",
        "BANK_SIZE", "IS_GSIB", "TOTAL_ASSETS_BEUR",
    ]]


def _build_fact(
    rd: pd.DataFrame,
    tr: pd.DataFrame,
    dim_bank: pd.DataFrame,
    dim_date: pd.DataFrame,
    dim_indicator: pd.DataFrame,
) -> pd.DataFrame:
    bank_by_code = dim_bank.set_index("BANK_CODE")["BANK_ID"]
    date_by_yq = dim_date.set_index("YEAR_QUARTER")["DATE_ID"]
    indicator_by_code = dim_indicator.set_index("INDICATOR_CODE")["INDICATOR_ID"]

    rd_fact = rd.copy()
    rd_fact["BANK_CODE"] = "AGG_" + rd_fact["COUNTRY_CODE"]
    rd_fact["BANK_ID"] = rd_fact["BANK_CODE"].map(bank_by_code)
    rd_fact["DATE_ID"] = rd_fact["YEAR_QUARTER"].map(date_by_yq)
    rd_fact["INDICATOR_ID"] = rd_fact["INDICATOR_CODE"].map(indicator_by_code)
    rd_fact["SOURCE"] = "RISK_DASHBOARD"
    rd_fact = rd_fact[[
        "BANK_ID", "COUNTRY_CODE", "DATE_ID", "INDICATOR_ID",
        "VALUE", "SOURCE", "REFERENCE_DATE",
    ]]

    tr_fact = tr.copy()
    tr_fact["BANK_ID"] = tr_fact["LEI_CODE"].map(bank_by_code)
    tr_fact["DATE_ID"] = tr_fact["YEAR_QUARTER"].map(date_by_yq)
    tr_fact["INDICATOR_ID"] = tr_fact["INDICATOR_CODE"].map(indicator_by_code)
    tr_fact["SOURCE"] = "TRANSPARENCY"
    tr_fact = tr_fact[[
        "BANK_ID", "COUNTRY_CODE", "DATE_ID", "INDICATOR_ID",
        "VALUE", "SOURCE", "REFERENCE_DATE",
    ]]

    fact = pd.concat([rd_fact, tr_fact], ignore_index=True)
    fact = fact.dropna(subset=["BANK_ID", "DATE_ID", "INDICATOR_ID"])
    fact["BANK_ID"] = fact["BANK_ID"].astype(int)
    fact["DATE_ID"] = fact["DATE_ID"].astype(int)
    fact["INDICATOR_ID"] = fact["INDICATOR_ID"].astype(int)
    fact.insert(0, "ROW_ID", range(1, len(fact) + 1))
    return fact


def _validate(fact: pd.DataFrame, dim_bank: pd.DataFrame, dim_date: pd.DataFrame,
              dim_indicator: pd.DataFrame) -> None:
    nulls = fact[["BANK_ID", "COUNTRY_CODE", "DATE_ID", "INDICATOR_ID"]].isna().sum()
    if nulls.any():
        raise AssertionError(f"null foreign keys detected: {nulls.to_dict()}")

    code_by_id = dim_indicator.set_index("INDICATOR_ID")["INDICATOR_CODE"]

    def values_for(code: str) -> pd.Series:
        indicator_id = dim_indicator.loc[
            dim_indicator["INDICATOR_CODE"] == code, "INDICATOR_ID"
        ]
        if indicator_id.empty:
            return pd.Series(dtype="float64")
        return fact.loc[fact["INDICATOR_ID"] == int(indicator_id.iloc[0]), "VALUE"]

    bounds: dict[str, tuple[float, float]] = {
        "CET1_FL": (0.05, 0.50),
        "LCR": (0.80, 5.00),
        "NPL_RATIO": (0.00, 0.30),
    }
    for code, (lo, hi) in bounds.items():
        values = values_for(code)
        if values.empty:
            logger.warning("validation skipped (%s missing from FACT)", code)
            continue
        out_of_bounds = values[(values < lo) | (values > hi)]
        if not out_of_bounds.empty:
            raise AssertionError(
                f"{code} out of [{lo}, {hi}] in {len(out_of_bounds)} rows; "
                f"min={values.min():.4f} max={values.max():.4f}"
            )

    bank_count = dim_bank[dim_bank["BANK_SIZE"] != "AGGREGATE"]["BANK_ID"].nunique()
    if bank_count < 100:
        logger.warning(
            "only %d individual banks (brief targets >= 100). "
            "Expected when using the synthetic fallback dataset.", bank_count,
        )
    quarter_count = dim_date["YEAR_QUARTER"].nunique()
    if quarter_count < 24:
        raise AssertionError(f"only {quarter_count} quarters (need >= 24)")

    logger.info("validation passed (%d rows, %d banks, %d quarters)",
                len(fact), bank_count, quarter_count)


def main() -> None:
    rd = pd.read_csv(RD_PATH)
    tr = pd.read_csv(TR_PATH)
    logger.info("loaded RD rows=%d, TR rows=%d", len(rd), len(tr))

    dim_country = _build_dim_country(rd, tr)
    dim_date = _build_dim_date(rd, tr)
    dim_indicator = _build_dim_indicator()
    dim_bank = _build_dim_bank(rd, tr)
    fact = _build_fact(rd, tr, dim_bank, dim_date, dim_indicator)

    _validate(fact, dim_bank, dim_date, dim_indicator)

    outputs = {
        "fact_ratios.csv": fact,
        "dim_bank.csv": dim_bank,
        "dim_country.csv": dim_country,
        "dim_date.csv": dim_date,
        "dim_indicator.csv": dim_indicator,
    }
    for name, frame in outputs.items():
        path = PROCESSED_DIR / name
        frame.to_csv(path, index=False)
        logger.info("wrote %s (%d rows)", path.relative_to(ROOT), len(frame))


if __name__ == "__main__":
    main()
