"""
Cleans the four EBA Transparency Exercise 2024 CSV files (bank-by-bank).

Inputs (any of these schemas is accepted)
-----------------------------------------
A) Long format published by EBA:
       LEI_Code, Bank_Name, Country, Period, Template_Row, Value, ...
B) Long format used by the synthetic fallback:
       LEI_Code, Bank_Name, Country, Period, Indicator, Value,
       Total_Assets_BEUR, Is_GSIB

Output
------
- data/processed/transparency_clean.csv
    Columns: LEI_Code, Bank_Name, Country, REFERENCE_DATE, YEAR_QUARTER,
             INDICATOR_CODE, VALUE, TOTAL_ASSETS_BEUR, IS_GSIB, BANK_SIZE

Transformations
---------------
- Concatenate the four theme files.
- Map indicator labels / template rows to canonical INDICATOR_CODE values.
- Normalise percent values into decimals where appropriate.
- Compute BANK_SIZE buckets:
    LARGE  : total_assets > 300 BEUR
    MEDIUM : 30 <= total_assets <= 300 BEUR
    SMALL  : total_assets < 30 BEUR
- Flag G-SIBs (FSB 2024 list) when not already provided.

Run
---
    python python/03_clean_transparency.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("eba.clean.te")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_PATH = PROCESSED_DIR / "transparency_clean.csv"

THEME_FILES: tuple[str, ...] = (
    "transparency_2024_capital.csv",
    "transparency_2024_asset_quality.csv",
    "transparency_2024_profitability.csv",
    "transparency_2024_leverage.csv",
)

INDICATOR_ALIASES: dict[str, str] = {
    # Direct canonical codes
    "CET1_FL": "CET1_FL",
    "TIER1_FL": "TIER1_FL",
    "TOTAL_CAP_FL": "TOTAL_CAP_FL",
    "LEV_RATIO": "LEV_RATIO",
    "LCR": "LCR",
    "NSFR": "NSFR",
    "NPL_RATIO": "NPL_RATIO",
    "NPL_COVERAGE": "NPL_COVERAGE",
    "ROE": "ROE",
    "ROA": "ROA",
    "CTI": "CTI",
    # Common EBA template wordings
    "cet1 ratio - fully loaded": "CET1_FL",
    "common equity tier 1 ratio (fully loaded)": "CET1_FL",
    "tier 1 ratio - fully loaded": "TIER1_FL",
    "total capital ratio - fully loaded": "TOTAL_CAP_FL",
    "leverage ratio - fully loaded": "LEV_RATIO",
    "liquidity coverage ratio (%)": "LCR",
    "net stable funding ratio (%)": "NSFR",
    "non-performing loans ratio": "NPL_RATIO",
    "npl coverage ratio": "NPL_COVERAGE",
    "return on equity": "ROE",
    "return on assets": "ROA",
    "cost to income ratio": "CTI",
    "cost-to-income ratio": "CTI",
}

PERCENT_INDICATORS: set[str] = {
    "CET1_FL", "TIER1_FL", "TOTAL_CAP_FL", "LEV_RATIO",
    "LCR", "NSFR", "NPL_RATIO", "NPL_COVERAGE", "ROE", "ROA", "CTI",
}

# FSB 2024 list of EU-headquartered banks classified as G-SIBs plus the EU
# G-SIB subsidiaries called out in the brief.
G_SIB_LEI_OR_NAME: set[str] = {
    "BNP PARIBAS", "SOCIETE GENERALE", "CREDIT AGRICOLE", "BPCE",
    "DEUTSCHE BANK", "UNICREDIT", "INTESA SANPAOLO",
    "BANCO SANTANDER", "BBVA", "ING", "RABOBANK", "NORDEA",
    "STANDARD CHARTERED", "HSBC",
    # Brief additions
    "ABN AMRO", "COMMERZBANK", "SWEDBANK", "SEB",
    "HANDELSBANKEN", "DANSKE BANK", "ERSTE GROUP", "KBC",
    "MBANK", "PKO BANK POLSKI",
}


def _canonical_indicator(label: object) -> str | None:
    if not isinstance(label, str):
        return None
    key = label.strip()
    if not key:
        return None
    if key in INDICATOR_ALIASES:
        return INDICATOR_ALIASES[key]
    low = key.lower()
    if low in INDICATOR_ALIASES:
        return INDICATOR_ALIASES[low]
    for fragment, indicator in INDICATOR_ALIASES.items():
        if fragment in low:
            return indicator
    return None


def _parse_period(value: object) -> str | None:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return pd.to_datetime(text, dayfirst=False).date().isoformat()
    except (ValueError, TypeError):
        return None


def _reference_to_year_quarter(reference: str) -> str:
    ts = pd.to_datetime(reference)
    return f"{ts.year}-Q{((ts.month - 1) // 3) + 1}"


def _bank_size(total_assets: float) -> str:
    if pd.isna(total_assets):
        return "UNKNOWN"
    if total_assets > 300:
        return "LARGE"
    if total_assets >= 30:
        return "MEDIUM"
    return "SMALL"


def _is_gsib(bank_name: object, flag: object) -> int:
    if isinstance(flag, (int, float)) and not pd.isna(flag):
        return int(bool(flag))
    if isinstance(flag, str) and flag.strip() in {"1", "true", "True", "Y"}:
        return 1
    if isinstance(bank_name, str):
        upper = bank_name.upper()
        for needle in G_SIB_LEI_OR_NAME:
            if needle in upper:
                return 1
    return 0


def _scale_percent(value: float, indicator: str) -> float:
    if indicator not in PERCENT_INDICATORS or pd.isna(value):
        return value
    if abs(value) > 5:
        return value / 100.0
    return value


def _load_theme(path: Path) -> pd.DataFrame:
    if not path.exists():
        logger.warning("missing theme file: %s", path.name)
        return pd.DataFrame()
    logger.info("reading %s", path.name)
    frame = pd.read_csv(path)
    column_map = {c.lower(): c for c in frame.columns}
    indicator_col = (
        column_map.get("indicator")
        or column_map.get("template_row")
        or column_map.get("label")
    )
    if indicator_col is None:
        logger.warning("no indicator column in %s", path.name)
        return pd.DataFrame()
    value_col = column_map.get("value") or column_map.get("amount")
    if value_col is None:
        logger.warning("no value column in %s", path.name)
        return pd.DataFrame()
    rename = {
        column_map.get("lei_code", "LEI_Code"): "LEI_Code",
        column_map.get("bank_name", "Bank_Name"): "Bank_Name",
        column_map.get("country", "Country"): "Country",
        column_map.get("period", "Period"): "Period",
        indicator_col: "Indicator",
        value_col: "Value",
    }
    if "total_assets_beur" in column_map:
        rename[column_map["total_assets_beur"]] = "Total_Assets_BEUR"
    if "is_gsib" in column_map:
        rename[column_map["is_gsib"]] = "Is_GSIB"
    frame = frame.rename(columns=rename)
    keep = [c for c in (
        "LEI_Code", "Bank_Name", "Country", "Period", "Indicator", "Value",
        "Total_Assets_BEUR", "Is_GSIB",
    ) if c in frame.columns]
    return frame[keep].copy()


def load() -> pd.DataFrame:
    frames = [_load_theme(RAW_DIR / name) for name in THEME_FILES]
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise FileNotFoundError(
            "No Transparency Exercise files found in data/raw/. "
            "Run python/01_download_data.py first."
        )
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    frame = load()
    frame["INDICATOR_CODE"] = frame["Indicator"].map(_canonical_indicator)
    frame["REFERENCE_DATE"] = frame["Period"].map(_parse_period)
    frame = frame.dropna(subset=["INDICATOR_CODE", "REFERENCE_DATE"])
    frame["Value"] = pd.to_numeric(frame["Value"], errors="coerce")
    frame = frame.dropna(subset=["Value"])
    frame["VALUE"] = frame.apply(
        lambda row: _scale_percent(row["Value"], row["INDICATOR_CODE"]),
        axis=1,
    )
    frame["YEAR_QUARTER"] = frame["REFERENCE_DATE"].map(_reference_to_year_quarter)
    if "Total_Assets_BEUR" not in frame.columns:
        frame["Total_Assets_BEUR"] = pd.NA
    frame["Total_Assets_BEUR"] = pd.to_numeric(
        frame["Total_Assets_BEUR"], errors="coerce"
    )
    frame["BANK_SIZE"] = frame["Total_Assets_BEUR"].map(_bank_size)
    if "Is_GSIB" not in frame.columns:
        frame["Is_GSIB"] = pd.NA
    frame["IS_GSIB"] = frame.apply(
        lambda row: _is_gsib(row.get("Bank_Name"), row.get("Is_GSIB")),
        axis=1,
    )

    output = frame[[
        "LEI_Code", "Bank_Name", "Country", "REFERENCE_DATE", "YEAR_QUARTER",
        "INDICATOR_CODE", "VALUE", "Total_Assets_BEUR", "IS_GSIB", "BANK_SIZE",
    ]].rename(columns={
        "LEI_Code": "LEI_CODE", "Bank_Name": "BANK_NAME", "Country": "COUNTRY_CODE",
        "Total_Assets_BEUR": "TOTAL_ASSETS_BEUR",
    })
    output = output.drop_duplicates(
        subset=["LEI_CODE", "INDICATOR_CODE", "REFERENCE_DATE"], keep="last"
    ).sort_values(["INDICATOR_CODE", "COUNTRY_CODE", "BANK_NAME", "REFERENCE_DATE"])

    output.to_csv(OUTPUT_PATH, index=False)
    logger.info(
        "wrote %s (%d rows, %d banks, %d indicators, %d periods)",
        OUTPUT_PATH.relative_to(ROOT), len(output),
        output["LEI_CODE"].nunique(),
        output["INDICATOR_CODE"].nunique(),
        output["REFERENCE_DATE"].nunique(),
    )


if __name__ == "__main__":
    main()
