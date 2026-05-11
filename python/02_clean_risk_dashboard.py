"""
Cleans the EBA Risk Dashboard data (country aggregates, quarterly).

Input
-----
- data/raw/risk_dashboard_q4_2024.xlsx   (real EBA Excel, multi-sheet, wide)
- data/raw/risk_dashboard_synthetic.csv  (long-format fallback, identical schema)

Output
------
- data/processed/risk_dashboard_clean.csv
    Columns: COUNTRY_CODE, INDICATOR_CODE, REFERENCE_DATE, YEAR_QUARTER, VALUE

Transformations
---------------
- Reshape Excel sheets from wide (one column per quarter) to long.
- Map sheet names to indicator codes (CET1_FL, LCR, etc.).
- Normalise percent values: if the indicator is expressed in % and we observe
  values > 1, divide by 100 (EBA mixes 15.3 and 0.153 across vintages).
- Map EBA country labels to ISO 3166 alpha-2 codes.
- Keep only periods from Q1 2018 onwards.
- Drop rows missing essential keys.

Run
---
    python python/02_clean_risk_dashboard.py
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("eba.clean.rd")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_PATH = PROCESSED_DIR / "risk_dashboard_clean.csv"

# Sheet name -> indicator code. Match is case-insensitive and tolerates the
# small wording variations EBA introduces between vintages.
SHEET_TO_INDICATOR: dict[str, str] = {
    "cet1 ratio": "CET1_FL",
    "cet1 fully loaded": "CET1_FL",
    "tier 1 ratio": "TIER1_FL",
    "total capital ratio": "TOTAL_CAP_FL",
    "leverage ratio": "LEV_RATIO",
    "lcr": "LCR",
    "liquidity coverage ratio": "LCR",
    "nsfr": "NSFR",
    "npl ratio": "NPL_RATIO",
    "npl coverage": "NPL_COVERAGE",
    "coverage ratio": "NPL_COVERAGE",
    "roe": "ROE",
    "return on equity": "ROE",
    "roa": "ROA",
    "return on assets": "ROA",
    "cost-to-income": "CTI",
    "cost to income": "CTI",
}

# Indicators reported in percent (decimals expected downstream).
PERCENT_INDICATORS: set[str] = {
    "CET1_FL", "TIER1_FL", "TOTAL_CAP_FL", "LEV_RATIO",
    "LCR", "NSFR", "NPL_RATIO", "NPL_COVERAGE", "ROE", "ROA", "CTI",
}

EBA_COUNTRY_TO_ISO: dict[str, str] = {
    "Austria": "AT", "Belgium": "BE", "Bulgaria": "BG", "Croatia": "HR",
    "Cyprus": "CY", "Czech Republic": "CZ", "Czechia": "CZ",
    "Denmark": "DK", "Estonia": "EE", "Finland": "FI", "France": "FR",
    "Germany": "DE", "Greece": "GR", "Hungary": "HU", "Iceland": "IS",
    "Ireland": "IE", "Italy": "IT", "Latvia": "LV", "Liechtenstein": "LI",
    "Lithuania": "LT", "Luxembourg": "LU", "Malta": "MT", "Netherlands": "NL",
    "Norway": "NO", "Poland": "PL", "Portugal": "PT", "Romania": "RO",
    "Slovakia": "SK", "Slovenia": "SI", "Spain": "ES", "Sweden": "SE",
    "European Union": "EU", "EU": "EU", "EU/EEA": "EU", "EEA": "EU",
}

QUARTER_PATTERN = re.compile(
    r"^(?P<year>20\d{2})[\s\-_]*Q(?P<q>[1-4])$", re.IGNORECASE,
)


def _normalise_indicator(sheet_name: str) -> str | None:
    key = sheet_name.strip().lower()
    if key in SHEET_TO_INDICATOR:
        return SHEET_TO_INDICATOR[key]
    for fragment, indicator in SHEET_TO_INDICATOR.items():
        if fragment in key:
            return indicator
    return None


def _to_iso_country(label: object) -> str | None:
    if not isinstance(label, str):
        return None
    label = label.strip()
    if not label:
        return None
    if len(label) <= 3 and label.isupper():
        return "EU" if label in {"EU", "EEA"} else label
    return EBA_COUNTRY_TO_ISO.get(label)


def _to_year_quarter(label: object) -> str | None:
    if isinstance(label, pd.Timestamp):
        return f"{label.year}-Q{((label.month - 1) // 3) + 1}"
    if not isinstance(label, str):
        return None
    label = label.strip()
    match = QUARTER_PATTERN.match(label.replace(" ", ""))
    if match:
        return f"{match.group('year')}-Q{match.group('q')}"
    return None


def _yq_to_reference_date(year_quarter: str) -> str:
    year, quarter = year_quarter.split("-Q")
    month_day = {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}[quarter]
    return f"{year}-{month_day}"


def _scale_percent(values: pd.Series, indicator: str) -> pd.Series:
    """Return values in decimal form (0-1 for ratios, may exceed 1 for LCR)."""
    if indicator not in PERCENT_INDICATORS:
        return values
    abs_max = values.abs().max(skipna=True)
    if pd.isna(abs_max) or abs_max <= 5:
        return values
    return values / 100.0


def _clean_excel(path: Path) -> pd.DataFrame:
    logger.info("reading Excel: %s", path.name)
    sheets = pd.read_excel(path, sheet_name=None, header=0)
    long_frames: list[pd.DataFrame] = []
    for sheet_name, frame in sheets.items():
        indicator = _normalise_indicator(sheet_name)
        if indicator is None:
            logger.debug("skipping sheet (unmapped): %s", sheet_name)
            continue
        if frame.empty:
            continue
        country_col = frame.columns[0]
        melted = frame.melt(
            id_vars=[country_col],
            var_name="period_label",
            value_name="VALUE",
        )
        melted["COUNTRY_CODE"] = melted[country_col].map(_to_iso_country)
        melted["YEAR_QUARTER"] = melted["period_label"].map(_to_year_quarter)
        melted = melted.dropna(subset=["COUNTRY_CODE", "YEAR_QUARTER", "VALUE"])
        if melted.empty:
            continue
        melted["INDICATOR_CODE"] = indicator
        melted["VALUE"] = pd.to_numeric(melted["VALUE"], errors="coerce")
        melted = melted.dropna(subset=["VALUE"])
        melted["VALUE"] = _scale_percent(melted["VALUE"], indicator)
        long_frames.append(
            melted[["COUNTRY_CODE", "INDICATOR_CODE", "YEAR_QUARTER", "VALUE"]]
        )
        logger.info("sheet %r -> %s (%d rows)", sheet_name, indicator, len(melted))
    if not long_frames:
        raise ValueError("no usable sheets found in Risk Dashboard Excel")
    return pd.concat(long_frames, ignore_index=True)


def _clean_synthetic(path: Path) -> pd.DataFrame:
    logger.info("reading synthetic CSV: %s", path.name)
    frame = pd.read_csv(path)
    frame["VALUE"] = pd.to_numeric(frame["VALUE"], errors="coerce")
    frame = frame.dropna(subset=["VALUE"])
    return frame[["COUNTRY_CODE", "INDICATOR_CODE", "YEAR_QUARTER", "VALUE"]]


def load() -> pd.DataFrame:
    excel_path = RAW_DIR / "risk_dashboard_q4_2024.xlsx"
    synthetic_path = RAW_DIR / "risk_dashboard_synthetic.csv"
    if excel_path.exists() and excel_path.stat().st_size > 0:
        return _clean_excel(excel_path)
    if synthetic_path.exists():
        return _clean_synthetic(synthetic_path)
    raise FileNotFoundError(
        "No Risk Dashboard input found. Run python/01_download_data.py first."
    )


def main() -> None:
    frame = load()
    frame = frame[frame["YEAR_QUARTER"] >= "2018-Q1"].copy()
    frame["REFERENCE_DATE"] = frame["YEAR_QUARTER"].map(_yq_to_reference_date)
    frame = frame.drop_duplicates(
        subset=["COUNTRY_CODE", "INDICATOR_CODE", "YEAR_QUARTER"], keep="last"
    )
    frame = frame[[
        "COUNTRY_CODE", "INDICATOR_CODE", "REFERENCE_DATE", "YEAR_QUARTER", "VALUE",
    ]].sort_values(
        ["INDICATOR_CODE", "COUNTRY_CODE", "REFERENCE_DATE"]
    )
    frame.to_csv(OUTPUT_PATH, index=False)
    logger.info("wrote %s (%d rows, %d indicators, %d countries)",
                OUTPUT_PATH.relative_to(ROOT), len(frame),
                frame["INDICATOR_CODE"].nunique(),
                frame["COUNTRY_CODE"].nunique())


if __name__ == "__main__":
    main()
