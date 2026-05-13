"""
Cleans the ECB Key Risk Indicators (KRI) SDMX-CSV download.

Input
-----
- data/raw/ecb_kri.csv

The ECB Data Portal publishes the EBA KRI dataflow in SDMX-CSV format. The
column set varies between vintages but always exposes:
    REF_AREA       country (ISO 2-letter, U2 for the EU/EEA aggregate)
    TIME_PERIOD    YYYY-Qn
    OBS_VALUE      numeric observation
    OBS_STATUS     A = approved
plus one or more series-key dimensions that identify the indicator. This
script auto-detects the indicator column and the numeric value column to
stay robust across ECB releases.

Output
------
- data/processed/risk_indicators_clean.csv
    Columns: COUNTRY_CODE, INDICATOR_CODE, REFERENCE_DATE, YEAR_QUARTER, VALUE

Transformations
---------------
- Map the indicator dimension (or the full KEY) to canonical INDICATOR_CODE
  values via fuzzy substring matching.
- Map ECB country codes to ISO 3166 (REF_AREA == "U2" -> "EU").
- Convert TIME_PERIOD strings into REFERENCE_DATE (last day of quarter) and
  keep YEAR_QUARTER for slicer convenience.
- Normalise percent values into decimal form when the published unit is %.
- Keep only periods >= 2018-Q1.

Run
---
    python python/02_clean_risk_indicators.py
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("eba.clean.kri")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

INPUT_PATH = RAW_DIR / "ecb_kri.csv"
OUTPUT_PATH = PROCESSED_DIR / "risk_indicators_clean.csv"

# Fuzzy matchers from ECB indicator labels / codes to canonical model codes.
# Order matters: the longest, most specific patterns must come first.
INDICATOR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("CET1_FL",      "CET1_FL"),
    ("CET1 RATIO",   "CET1_FL"),
    ("CET 1",        "CET1_FL"),
    ("TIER1_FL",     "TIER1_FL"),
    ("TIER 1 RATIO", "TIER1_FL"),
    ("TOTAL_CAP_FL", "TOTAL_CAP_FL"),
    ("TOTAL CAPITAL","TOTAL_CAP_FL"),
    ("LEV_RATIO",    "LEV_RATIO"),
    ("LEVERAGE",     "LEV_RATIO"),
    ("LCR",          "LCR"),
    ("LIQUIDITY COVERAGE", "LCR"),
    ("NSFR",         "NSFR"),
    ("NET STABLE",   "NSFR"),
    ("NPL_RATIO",    "NPL_RATIO"),
    ("NPL RATIO",    "NPL_RATIO"),
    ("NON-PERFORMING", "NPL_RATIO"),
    ("NPL_COVERAGE", "NPL_COVERAGE"),
    ("COVERAGE",     "NPL_COVERAGE"),
    ("ROE",          "ROE"),
    ("RETURN ON EQUITY", "ROE"),
    ("ROA",          "ROA"),
    ("RETURN ON ASSETS", "ROA"),
    ("CTI",          "CTI"),
    ("COST TO INCOME",  "CTI"),
    ("COST-TO-INCOME",  "CTI"),
    ("LTD_RATIO",    "LTD_RATIO"),
    ("LOAN-TO-DEPOSIT", "LTD_RATIO"),
)

PERCENT_INDICATORS: set[str] = {
    "CET1_FL", "TIER1_FL", "TOTAL_CAP_FL", "LEV_RATIO",
    "LCR", "NSFR", "NPL_RATIO", "NPL_COVERAGE", "ROE", "ROA", "CTI", "LTD_RATIO",
}

QUARTER_PATTERN = re.compile(r"^(?P<year>20\d{2})[\s\-_]*Q(?P<q>[1-4])$",
                             re.IGNORECASE)


def _canonical_indicator(label: object) -> str | None:
    if not isinstance(label, str):
        return None
    upper = label.upper()
    for pattern, code in INDICATOR_PATTERNS:
        if pattern in upper:
            return code
    return None


def _to_iso_country(label: object) -> str | None:
    if not isinstance(label, str):
        return None
    label = label.strip().upper()
    if not label:
        return None
    if label in {"U2", "EU", "EEA"}:
        return "EU"
    if len(label) == 2 and label.isalpha():
        return label
    return None


def _to_year_quarter(label: object) -> str | None:
    if isinstance(label, pd.Timestamp):
        return f"{label.year}-Q{((label.month - 1) // 3) + 1}"
    if not isinstance(label, str):
        return None
    match = QUARTER_PATTERN.match(label.strip().replace(" ", ""))
    if match:
        return f"{match.group('year')}-Q{match.group('q')}"
    return None


def _yq_to_reference_date(year_quarter: str) -> str:
    year, quarter = year_quarter.split("-Q")
    month_day = {"1": "03-31", "2": "06-30",
                 "3": "09-30", "4": "12-31"}[quarter]
    return f"{year}-{month_day}"


def _scale_percent(values: pd.Series, indicator: str) -> pd.Series:
    """Return values in decimal form when EBA / ECB published them as %."""
    if indicator not in PERCENT_INDICATORS:
        return values
    abs_max = values.abs().max(skipna=True)
    if pd.isna(abs_max) or abs_max <= 5:
        return values
    return values / 100.0


def _detect_value_column(frame: pd.DataFrame) -> str:
    """Locate the numeric observation column (SDMX uses OBS_VALUE)."""
    for candidate in ("OBS_VALUE", "VALUE", "Amount", "Value"):
        if candidate in frame.columns:
            return candidate
    raise ValueError(f"no observation column found in {frame.columns.tolist()}")


def _detect_indicator_column(frame: pd.DataFrame) -> str:
    """Locate the indicator column. Fall back to the full series KEY."""
    for candidate in (
        "KRI_IND", "KRI_INDICATOR", "INDICATOR", "INDICATOR_CODE",
        "KEY_FAMILY", "TITLE_COMPL", "KEY",
    ):
        if candidate in frame.columns:
            return candidate
    raise ValueError(f"no indicator column found in {frame.columns.tolist()}")


def _detect_time_column(frame: pd.DataFrame) -> str:
    for candidate in ("TIME_PERIOD", "PERIOD", "DATE"):
        if candidate in frame.columns:
            return candidate
    raise ValueError(f"no time column found in {frame.columns.tolist()}")


def _detect_country_column(frame: pd.DataFrame) -> str:
    for candidate in ("REF_AREA", "COUNTRY", "COUNTRY_CODE", "NSA"):
        if candidate in frame.columns:
            return candidate
    raise ValueError(f"no country column found in {frame.columns.tolist()}")


def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            "ECB KRI input not found. Run python/01_download_data.py first."
        )
    frame = pd.read_csv(INPUT_PATH, low_memory=False)
    logger.info("loaded %s shape=%s", INPUT_PATH.name, frame.shape)

    value_col = _detect_value_column(frame)
    indicator_col = _detect_indicator_column(frame)
    time_col = _detect_time_column(frame)
    country_col = _detect_country_column(frame)
    logger.info("columns mapped: indicator=%s country=%s time=%s value=%s",
                indicator_col, country_col, time_col, value_col)

    frame["COUNTRY_CODE"] = frame[country_col].map(_to_iso_country)
    frame["YEAR_QUARTER"] = frame[time_col].map(_to_year_quarter)
    frame["INDICATOR_CODE"] = frame[indicator_col].map(_canonical_indicator)
    frame["VALUE"] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.dropna(subset=[
        "COUNTRY_CODE", "YEAR_QUARTER", "INDICATOR_CODE", "VALUE",
    ])
    frame = frame[frame["YEAR_QUARTER"] >= "2018-Q1"].copy()

    pieces: list[pd.DataFrame] = []
    for indicator, slice_ in frame.groupby("INDICATOR_CODE"):
        scaled = slice_.assign(VALUE=_scale_percent(slice_["VALUE"], indicator))
        pieces.append(scaled)
    frame = pd.concat(pieces, ignore_index=True)

    frame["REFERENCE_DATE"] = frame["YEAR_QUARTER"].map(_yq_to_reference_date)
    output = frame[[
        "COUNTRY_CODE", "INDICATOR_CODE",
        "REFERENCE_DATE", "YEAR_QUARTER", "VALUE",
    ]].drop_duplicates(
        subset=["COUNTRY_CODE", "INDICATOR_CODE", "YEAR_QUARTER"], keep="last",
    ).sort_values(["INDICATOR_CODE", "COUNTRY_CODE", "REFERENCE_DATE"])

    output.to_csv(OUTPUT_PATH, index=False)
    logger.info(
        "wrote %s (%d rows, %d indicators, %d countries, %d quarters)",
        OUTPUT_PATH.relative_to(ROOT), len(output),
        output["INDICATOR_CODE"].nunique(),
        output["COUNTRY_CODE"].nunique(),
        output["YEAR_QUARTER"].nunique(),
    )


if __name__ == "__main__":
    main()
