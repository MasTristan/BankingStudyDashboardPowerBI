"""
Cleans the EBA Transparency Exercise 2024 CSVs (EAV layout).

Inputs
------
- data/raw/tr_oth.csv  Capital, Leverage, RWA, P&L, key metrics (LCR, NSFR, CET1)
- data/raw/tr_cre.csv  Credit risk: NPL ratio, forborne, coverage
- data/raw/TR_Metadata.xlsx  Item -> Label mapping (optional)

Real-world column set:
    LEI_code, NSA, Period, Item, Label, Portfolio, Country, Country_rank,
    Exposure, Status, Perf_Status, NACE_codes, Amount, Footnote,
    Row, Column, Sheet

Period is YYYYMM (e.g. 202406 = 30-Jun-2024).

Output
------
- data/processed/transparency_clean.csv
    Columns: LEI_CODE, BANK_NAME, COUNTRY_CODE, REFERENCE_DATE, YEAR_QUARTER,
             INDICATOR_CODE, VALUE, TOTAL_ASSETS_BEUR, IS_GSIB, BANK_SIZE

Strategy
--------
1. Read TR_Metadata.xlsx when present; otherwise fall back to label-based
   matching directly against the CSV `Label` column.
2. For each target indicator (CET1, Tier1, Total Capital, Leverage, LCR,
   NSFR, ROE, ROA, CTI, NPL, NPL coverage, Forborne), match against the
   metadata catalogue with case-insensitive substring search.
3. Filter to aggregate rows: Portfolio in {"", "a0", "Total"},
   Country in {"", "Total", "TOT"} and Country_rank in {"", "1"}. This
   isolates the all-counterparties / all-approaches aggregate that EBA
   stamps on every disclosing bank.
4. Pivot Item -> wide indicator columns per (LEI_code, NSA, Period).
5. Convert Period YYYYMM into REFERENCE_DATE + YEAR_QUARTER.
6. Compute TOTAL_ASSETS_BEUR (from the RWA_TOTAL proxy when available),
   BANK_SIZE buckets and IS_GSIB flag from the FSB 2024 roster.

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

OTH_PATH = RAW_DIR / "tr_oth.csv"
CRE_PATH = RAW_DIR / "tr_cre.csv"
METADATA_PATH = RAW_DIR / "TR_Metadata.xlsx"
OUTPUT_PATH = PROCESSED_DIR / "transparency_clean.csv"

# Canonical indicator code -> ordered list of substrings to look for in the
# `Label` column (case-insensitive). The first matching label wins, so the
# most specific patterns come first.
INDICATOR_LABELS: dict[str, tuple[str, ...]] = {
    "CET1_FL":       ("common equity tier 1 ratio", "cet1 ratio", "cet 1"),
    "TIER1_FL":      ("tier 1 ratio",),
    "TOTAL_CAP_FL":  ("total capital ratio",),
    "LEV_RATIO":     ("leverage ratio",),
    "LCR":           ("liquidity coverage ratio",),
    "NSFR":          ("net stable funding ratio",),
    "ROE":           ("return on equity",),
    "ROA":           ("return on assets",),
    "CTI":           ("cost to income", "cost-to-income"),
    "RWA_TOTAL":     ("total risk weighted assets", "total rwa"),
    "NPL_RATIO":     ("non-performing loans ratio", "non performing loans ratio"),
    "NPL_COVERAGE":  ("coverage ratio of non-performing", "coverage ratio of non performing"),
    "FORBORNE_RATIO":("forborne exposures ratio",),
}

PERCENT_INDICATORS: set[str] = {
    "CET1_FL", "TIER1_FL", "TOTAL_CAP_FL", "LEV_RATIO",
    "LCR", "NSFR", "ROE", "ROA", "CTI",
    "NPL_RATIO", "NPL_COVERAGE", "FORBORNE_RATIO",
}

# Aggregate row filters. EBA uses several conventions across files; we accept
# any of these tokens (and empty strings) on the dimensional columns.
AGG_PORTFOLIOS = {"", "a0", "A0", "Total", "TOT", "T"}
AGG_COUNTRIES  = {"", "Total", "TOT", "T"}
AGG_COUNTRY_RANK = {"", "1"}

G_SIB_FRAGMENTS = (
    "BNP PARIBAS", "SOCIETE GENERALE", "CREDIT AGRICOLE", "BPCE",
    "DEUTSCHE BANK", "UNICREDIT", "INTESA SANPAOLO",
    "BANCO SANTANDER", "BBVA", "ING", "RABOBANK", "NORDEA",
    "ABN AMRO", "COMMERZBANK", "SWEDBANK", "SEB", "HANDELSBANKEN",
    "DANSKE BANK", "ERSTE GROUP", "KBC", "MBANK", "PKO BANK POLSKI",
)

# Bank display names. Real EBA TE 2024 ships the names in TR_Metadata.xlsx;
# the synthetic dataset embeds them in the LEI/Label combo so we recover
# names via this lookup. Production runs against real data will replace this
# with a metadata-driven join.
SYNTHETIC_BANK_NAMES: dict[str, str] = {
    "FR_BNPP": "BNP Paribas", "FR_GLE": "Societe Generale",
    "FR_ACA": "Credit Agricole Group", "FR_BPCE": "BPCE",
    "DE_DBK": "Deutsche Bank", "DE_CBK": "Commerzbank",
    "DE_DZB": "DZ Bank", "DE_LBW": "LBBW",
    "IT_UCG": "UniCredit", "IT_ISP": "Intesa Sanpaolo",
    "IT_BAMI": "Banco BPM", "IT_BMPS": "Monte dei Paschi",
    "ES_SAN": "Banco Santander", "ES_BBVA": "BBVA",
    "ES_CABK": "CaixaBank", "ES_SAB": "Banco Sabadell",
    "NL_INGA": "ING Groep", "NL_ABN": "ABN AMRO", "NL_RABO": "Rabobank",
    "BE_KBC": "KBC Group", "BE_BEL": "Belfius",
    "AT_ERST": "Erste Group", "AT_RBI": "Raiffeisen Bank Intl",
    "SE_NDA": "Nordea", "SE_SHB": "Handelsbanken",
    "SE_SEB": "SEB", "SE_SWED": "Swedbank",
    "DK_DAN": "Danske Bank", "DK_JYS": "Jyske Bank",
    "FI_OP": "OP Group", "NO_DNB": "DNB Bank",
    "IE_AIB": "AIB Group", "IE_BIRG": "Bank of Ireland",
    "PT_BCP": "Millennium BCP", "PT_CGD": "Caixa Geral de Depositos",
    "GR_NBG": "National Bank of Greece", "GR_ALPHA": "Alpha Bank",
    "GR_PIR": "Piraeus Bank", "GR_ETE": "Eurobank Ergasias",
    "PL_PKO": "PKO Bank Polski", "PL_PEKAO": "Bank Pekao", "PL_MBK": "mBank",
    "HU_OTP": "OTP Bank", "CZ_KB": "Komercni Banka",
    "RO_BRD": "BRD Groupe SG", "CY_BOC": "Bank of Cyprus",
    "LU_BIL": "Banque Internationale Lux", "MT_BOV": "Bank of Valletta",
    "SI_NLB": "NLB Group", "SK_VUB": "VUB Banka",
    "HR_ZABA": "Zagrebacka Banka", "LV_SWLV": "Swedbank Latvia",
    "LT_SBLT": "Siauliu Bankas", "EE_LHV": "LHV Group",
    "BG_DSK": "DSK Bank", "IS_LAN": "Landsbankinn",
    "LI_LLB": "Liechtensteinische LB", "FR_HSBC": "HSBC Continental Europe",
    "DE_HCOB": "Hamburg Commercial Bank", "DE_NORD": "NORD/LB",
    "IT_MED": "Mediobanca",
}


def load_metadata(path: Path) -> dict[str, str]:
    """Return {Item: Label} from TR_Metadata.xlsx if available, else {}."""
    if not path.exists():
        logger.warning("metadata not found: %s", path)
        return {}
    try:
        xl = pd.ExcelFile(path)
    except Exception as exc:
        logger.warning("cannot open %s: %s", path.name, exc)
        return {}
    for sheet_name in xl.sheet_names:
        frame = xl.parse(sheet_name)
        cols = {c.lower(): c for c in frame.columns}
        if "item" in cols and "label" in cols:
            mapping = dict(zip(frame[cols["item"]], frame[cols["label"]]))
            logger.info("metadata loaded from sheet %r (%d items)",
                        sheet_name, len(mapping))
            return mapping
    logger.warning("no (Item, Label) sheet in %s", path.name)
    return {}


def resolve_item_codes(
    metadata: dict[str, str], frame: pd.DataFrame,
) -> dict[str, str]:
    """Return {Item: canonical INDICATOR_CODE} for the indicators we care about."""
    label_source: dict[str, str] = dict(metadata)
    if not label_source and "Label" in frame.columns and "Item" in frame.columns:
        label_source = dict(zip(frame["Item"], frame["Label"]))

    resolved: dict[str, str] = {}
    for indicator, fragments in INDICATOR_LABELS.items():
        for item, label in label_source.items():
            if not isinstance(label, str):
                continue
            low = label.lower()
            if any(fragment in low for fragment in fragments):
                resolved.setdefault(item, indicator)
    logger.info("resolved %d Item -> INDICATOR_CODE entries", len(resolved))
    return resolved


def _agg_mask(frame: pd.DataFrame) -> pd.Series:
    """Boolean mask isolating aggregate (total) rows for ratio calculations."""
    def normalise(value: object) -> str:
        return "" if pd.isna(value) else str(value).strip()

    portfolio = frame.get("Portfolio", pd.Series([""] * len(frame))).map(normalise)
    country = frame.get("Country",   pd.Series([""] * len(frame))).map(normalise)
    rank = frame.get("Country_rank", pd.Series([""] * len(frame))).map(normalise)
    return (
        portfolio.isin(AGG_PORTFOLIOS)
        & country.isin(AGG_COUNTRIES)
        & rank.isin(AGG_COUNTRY_RANK)
    )


def _period_to_date(period: object) -> str | None:
    if pd.isna(period):
        return None
    text = str(int(period)) if isinstance(period, float) else str(period)
    text = text.strip()
    if len(text) != 6 or not text.isdigit():
        return None
    year, month = int(text[:4]), int(text[4:6])
    last_day = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}[month]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


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


def _is_gsib(bank_name: object) -> int:
    if not isinstance(bank_name, str):
        return 0
    upper = bank_name.upper()
    return int(any(fragment in upper for fragment in G_SIB_FRAGMENTS))


def _scale_percent(value: float, indicator: str) -> float:
    if indicator not in PERCENT_INDICATORS or pd.isna(value):
        return value
    return value / 100.0 if abs(value) > 5 else value


def _load_eav(path: Path) -> pd.DataFrame:
    if not path.exists():
        logger.warning("missing input: %s", path.name)
        return pd.DataFrame()
    logger.info("reading %s", path.name)
    frame = pd.read_csv(path, encoding="utf-8", low_memory=False)
    logger.info("  shape=%s columns=%s", frame.shape, list(frame.columns))
    return frame


def main() -> None:
    metadata = load_metadata(METADATA_PATH)
    frames = [_load_eav(OTH_PATH), _load_eav(CRE_PATH)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise FileNotFoundError(
            "No Transparency Exercise files in data/raw/. "
            "Run python/01_download_data.py first."
        )
    eav = pd.concat(frames, ignore_index=True)

    item_to_indicator = resolve_item_codes(metadata, eav)
    if not item_to_indicator:
        raise ValueError(
            "Could not resolve any Item -> indicator mapping. "
            "Inspect TR_Metadata.xlsx and the `Label` column of tr_oth/tr_cre."
        )

    eav["INDICATOR_CODE"] = eav["Item"].map(item_to_indicator)
    eav = eav.dropna(subset=["INDICATOR_CODE"])
    eav = eav.loc[_agg_mask(eav)].copy()
    eav["Amount"] = pd.to_numeric(eav["Amount"], errors="coerce")
    eav = eav.dropna(subset=["Amount"])
    eav["VALUE"] = eav.apply(
        lambda row: _scale_percent(row["Amount"], row["INDICATOR_CODE"]),
        axis=1,
    )
    eav["REFERENCE_DATE"] = eav["Period"].map(_period_to_date)
    eav = eav.dropna(subset=["REFERENCE_DATE"])
    eav["YEAR_QUARTER"] = eav["REFERENCE_DATE"].map(_reference_to_year_quarter)

    # Pivot: one row per (LEI, period, indicator). Drop duplicates (some EAV
    # files repeat the aggregate at multiple Row/Column positions).
    eav = eav.drop_duplicates(
        subset=["LEI_code", "NSA", "REFERENCE_DATE", "INDICATOR_CODE"],
        keep="last",
    )

    bank_names = eav.get("Bank_Name")
    if bank_names is None:
        eav["BANK_NAME"] = eav["LEI_code"].map(SYNTHETIC_BANK_NAMES).fillna(
            eav["LEI_code"]
        )
    else:
        eav["BANK_NAME"] = bank_names

    rwa_lookup = (
        eav[eav["INDICATOR_CODE"] == "RWA_TOTAL"]
        .groupby("LEI_code")["VALUE"]
        .max()
        .rename("RWA_MAX")
    )
    eav = eav.join(rwa_lookup, on="LEI_code")
    eav["TOTAL_ASSETS_BEUR"] = (eav["RWA_MAX"] / 0.35).round(1)
    eav["BANK_SIZE"] = eav["TOTAL_ASSETS_BEUR"].map(_bank_size)
    eav["IS_GSIB"] = eav["BANK_NAME"].map(_is_gsib)

    output = eav.rename(columns={
        "LEI_code": "LEI_CODE", "NSA": "COUNTRY_CODE",
    })[[
        "LEI_CODE", "BANK_NAME", "COUNTRY_CODE", "REFERENCE_DATE", "YEAR_QUARTER",
        "INDICATOR_CODE", "VALUE", "TOTAL_ASSETS_BEUR", "IS_GSIB", "BANK_SIZE",
    ]].sort_values(["INDICATOR_CODE", "COUNTRY_CODE", "BANK_NAME", "REFERENCE_DATE"])

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
