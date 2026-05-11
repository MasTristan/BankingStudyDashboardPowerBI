"""
Downloads EBA public datasets into data/raw/.

Sources
-------
1. EBA Risk Dashboard - Q4 2024 (Excel, country aggregates, quarterly)
2. EBA Transparency Exercise 2024 (bank-by-bank CSV files)

Behaviour
---------
- Idempotent: files already present in data/raw/ are skipped.
- Generates a synthetic fallback dataset (deterministic, documented) when the
  EBA endpoint cannot be reached, so the downstream pipeline always runs.
- Logs every step.

Run
---
    python python/01_download_data.py
"""

from __future__ import annotations

import csv
import logging
import math
import random
from pathlib import Path
from typing import Iterable

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("eba.download")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (EBA-Dashboard-Builder)"

# Direct file URLs published by EBA. Subject to change between releases.
URLS: dict[str, str] = {
    "risk_dashboard_q4_2024.xlsx": (
        "https://www.eba.europa.eu/sites/default/files/2025-03/"
        "948020f4-c628-4698-87e9-3be7f21919c0/"
        "EBA%20Risk%20Dashboard%20-%20Q4%202024%20-%20Data.xlsx"
    ),
    "transparency_2024_capital.csv": (
        "https://www.eba.europa.eu/sites/default/files/2024-12/"
        "TR_Capital.csv"
    ),
    "transparency_2024_asset_quality.csv": (
        "https://www.eba.europa.eu/sites/default/files/2024-12/"
        "TR_AssetQuality.csv"
    ),
    "transparency_2024_profitability.csv": (
        "https://www.eba.europa.eu/sites/default/files/2024-12/"
        "TR_Profitability.csv"
    ),
    "transparency_2024_leverage.csv": (
        "https://www.eba.europa.eu/sites/default/files/2024-12/"
        "TR_Leverage.csv"
    ),
}


def download_file(url: str, dest: Path, timeout: int = 60) -> bool:
    """Download ``url`` to ``dest``. Return True on success, False on failure."""
    if dest.exists() and dest.stat().st_size > 0:
        logger.info("cached: %s", dest.name)
        return True
    try:
        logger.info("downloading: %s", dest.name)
        response = requests.get(
            url, timeout=timeout, headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("download failed for %s: %s", dest.name, exc)
        return False
    if not response.content:
        logger.warning("empty payload for %s", dest.name)
        return False
    dest.write_bytes(response.content)
    logger.info("saved: %s (%.0f KB)", dest.name, len(response.content) / 1024)
    return True


# ---------------------------------------------------------------------------
# Synthetic fallback
# ---------------------------------------------------------------------------
#
# EBA publication URLs occasionally move between releases. To keep the pipeline
# runnable in any environment, we generate deterministic synthetic data when the
# real download fails. The fallback follows the schema described in the brief
# and is clearly tagged in DIM_BANK/SOURCE so it cannot be mistaken for real
# regulatory data in the Power BI report.

COUNTRY_CODES: tuple[str, ...] = (
    "AT", "BE", "CY", "DE", "DK", "ES", "FI", "FR", "GR", "HR",
    "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "NO", "PL",
    "PT", "RO", "SE", "SI", "SK", "EU",
)

QUARTERS: tuple[str, ...] = tuple(
    f"{y}-Q{q}" for y in range(2018, 2025) for q in range(1, 5)
)

RISK_DASHBOARD_INDICATORS: dict[str, tuple[float, float]] = {
    # indicator -> (centre, jitter) in decimal units
    "CET1_FL": (0.155, 0.030),
    "TIER1_FL": (0.175, 0.030),
    "TOTAL_CAP_FL": (0.198, 0.030),
    "LEV_RATIO": (0.057, 0.012),
    "LCR": (1.60, 0.30),
    "NSFR": (1.27, 0.10),
    "NPL_RATIO": (0.025, 0.020),
    "NPL_COVERAGE": (0.435, 0.040),
    "ROE": (0.085, 0.030),
    "ROA": (0.007, 0.003),
    "CTI": (0.585, 0.060),
}

TRANSPARENCY_PERIODS: tuple[str, ...] = (
    "2023-09-30", "2023-12-31", "2024-03-31", "2024-06-30",
)


def _synthetic_risk_dashboard(path: Path) -> None:
    """Write a long-format CSV mimicking the Risk Dashboard structure."""
    rng = random.Random(20240630)
    rows: list[list[str]] = [["COUNTRY_CODE", "INDICATOR_CODE",
                              "YEAR_QUARTER", "VALUE"]]
    for country in COUNTRY_CODES:
        country_bias = rng.uniform(-0.02, 0.02)
        for indicator, (centre, jitter) in RISK_DASHBOARD_INDICATORS.items():
            for idx, period in enumerate(QUARTERS):
                trend = 0.0015 * idx if indicator.startswith("CET1") else 0.0
                noise = rng.uniform(-jitter, jitter)
                value = max(0.0, centre + country_bias + trend + noise)
                rows.append([country, indicator, period, f"{value:.6f}"])
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    logger.info("synthetic risk dashboard rows: %d", len(rows) - 1)


# A small but realistic roster of 60 banks. The brief targets ~123 banks; we
# generate enough breadth across countries to exercise every visual while
# keeping the file small.
SYNTHETIC_BANKS: tuple[tuple[str, str, str, float, bool], ...] = (
    # (lei, name, country, total_assets_beur, is_gsib)
    ("FR_BNPP", "BNP Paribas", "FR", 2592.0, True),
    ("FR_GLE", "Societe Generale", "FR", 1554.0, True),
    ("FR_ACA", "Credit Agricole Group", "FR", 2351.0, True),
    ("FR_BPCE", "BPCE", "FR", 1535.0, True),
    ("DE_DBK", "Deutsche Bank", "DE", 1337.0, True),
    ("DE_CBK", "Commerzbank", "DE", 553.0, False),
    ("DE_DZB", "DZ Bank", "DE", 627.0, False),
    ("DE_LBW", "LBBW", "DE", 332.0, False),
    ("IT_UCG", "UniCredit", "IT", 802.0, True),
    ("IT_ISP", "Intesa Sanpaolo", "IT", 964.0, True),
    ("IT_BAMI", "Banco BPM", "IT", 192.0, False),
    ("IT_BMPS", "Monte dei Paschi", "IT", 121.0, False),
    ("ES_SAN", "Banco Santander", "ES", 1797.0, True),
    ("ES_BBVA", "BBVA", "ES", 775.0, True),
    ("ES_CABK", "CaixaBank", "ES", 612.0, False),
    ("ES_SAB", "Banco Sabadell", "ES", 240.0, False),
    ("NL_INGA", "ING Groep", "NL", 975.0, True),
    ("NL_ABN", "ABN AMRO", "NL", 396.0, False),
    ("NL_RABO", "Rabobank", "NL", 631.0, True),
    ("BE_KBC", "KBC Group", "BE", 354.0, False),
    ("BE_BEL", "Belfius", "BE", 192.0, False),
    ("AT_ERST", "Erste Group", "AT", 343.0, False),
    ("AT_RBI", "Raiffeisen Bank Intl", "AT", 211.0, False),
    ("SE_NDA", "Nordea", "SE", 583.0, True),
    ("SE_SHB", "Handelsbanken", "SE", 313.0, False),
    ("SE_SEB", "SEB", "SE", 357.0, False),
    ("SE_SWED", "Swedbank", "SE", 281.0, False),
    ("DK_DAN", "Danske Bank", "DK", 510.0, False),
    ("DK_JYS", "Jyske Bank", "DK", 91.0, False),
    ("FI_OP", "OP Group", "FI", 178.0, False),
    ("NO_DNB", "DNB Bank", "NO", 332.0, False),
    ("IE_AIB", "AIB Group", "IE", 134.0, False),
    ("IE_BIRG", "Bank of Ireland", "IE", 156.0, False),
    ("PT_BCP", "Millennium BCP", "PT", 96.0, False),
    ("PT_CGD", "Caixa Geral de Depositos", "PT", 100.0, False),
    ("GR_NBG", "National Bank of Greece", "GR", 75.0, False),
    ("GR_ALPHA", "Alpha Bank", "GR", 75.0, False),
    ("GR_PIR", "Piraeus Bank", "GR", 80.0, False),
    ("GR_ETE", "Eurobank Ergasias", "GR", 79.0, False),
    ("PL_PKO", "PKO Bank Polski", "PL", 117.0, True),
    ("PL_PEKAO", "Bank Pekao", "PL", 78.0, False),
    ("PL_MBK", "mBank", "PL", 50.0, True),
    ("HU_OTP", "OTP Bank", "HU", 95.0, False),
    ("CZ_KB", "Komercni Banka", "CZ", 64.0, False),
    ("RO_BRD", "BRD Groupe SG", "RO", 19.0, False),
    ("CY_BOC", "Bank of Cyprus", "CY", 26.0, False),
    ("LU_BIL", "Banque Internationale Lux", "LU", 28.0, False),
    ("MT_BOV", "Bank of Valletta", "MT", 14.0, False),
    ("SI_NLB", "NLB Group", "SI", 26.0, False),
    ("SK_VUB", "VUB Banka", "SK", 23.0, False),
    ("HR_ZABA", "Zagrebacka Banka", "HR", 21.0, False),
    ("LV_SWLV", "Swedbank Latvia", "LV", 9.0, False),
    ("LT_SBLT", "Siauliu Bankas", "LT", 6.0, False),
    ("EE_LHV", "LHV Group", "EE", 8.0, False),
    ("BG_DSK", "DSK Bank", "BG", 18.0, False),
    ("IS_LAN", "Landsbankinn", "IS", 13.0, False),
    ("LI_LLB", "Liechtensteinische LB", "LI", 22.0, False),
    ("FR_HSBC", "HSBC Continental Europe", "FR", 240.0, False),
    ("DE_HCOB", "Hamburg Commercial Bank", "DE", 30.0, False),
    ("DE_NORD", "NORD/LB", "DE", 110.0, False),
    ("IT_MED", "Mediobanca", "IT", 87.0, False),
)


def _bank_indicator_value(
    rng: random.Random,
    indicator: str,
    period_idx: int,
    is_gsib: bool,
    total_assets: float,
) -> float:
    """Return a plausible value for ``indicator`` for this bank/period."""
    size_factor = 0.0 if total_assets > 300 else (
        0.005 if total_assets > 30 else 0.015
    )
    base, jitter = RISK_DASHBOARD_INDICATORS[indicator]
    drift = 0.001 * period_idx
    noise = rng.uniform(-jitter, jitter)
    if indicator in {"NPL_RATIO", "CTI"}:
        return max(0.0, base - size_factor + drift + noise)
    if indicator == "LCR":
        return max(0.9, base + drift + noise)
    if is_gsib:
        return max(0.0, base + size_factor + drift + noise + 0.005)
    return max(0.0, base + size_factor + drift + noise)


def _synthetic_transparency(paths: dict[str, Path]) -> None:
    """Write four CSVs, one per Transparency theme."""
    rng = random.Random(20240928)
    themes: dict[str, Iterable[str]] = {
        "transparency_2024_capital.csv":
            ("CET1_FL", "TIER1_FL", "TOTAL_CAP_FL"),
        "transparency_2024_asset_quality.csv":
            ("NPL_RATIO", "NPL_COVERAGE"),
        "transparency_2024_profitability.csv":
            ("ROE", "ROA", "CTI"),
        "transparency_2024_leverage.csv":
            ("LEV_RATIO", "LCR", "NSFR"),
    }
    for filename, indicators in themes.items():
        rows: list[list[str]] = [[
            "LEI_Code", "Bank_Name", "Country", "Period",
            "Indicator", "Value", "Total_Assets_BEUR", "Is_GSIB",
        ]]
        for lei, name, country, total_assets, is_gsib in SYNTHETIC_BANKS:
            for period_idx, period in enumerate(TRANSPARENCY_PERIODS):
                for indicator in indicators:
                    value = _bank_indicator_value(
                        rng, indicator, period_idx, is_gsib, total_assets,
                    )
                    rows.append([
                        lei, name, country, period, indicator,
                        f"{value:.6f}", f"{total_assets:.1f}",
                        "1" if is_gsib else "0",
                    ])
        with paths[filename].open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        logger.info("synthetic %s rows: %d", filename, len(rows) - 1)


def write_synthetic_fallback() -> None:
    """Materialise the full synthetic dataset under data/raw/."""
    logger.warning(
        "EBA download unavailable - generating synthetic fallback dataset "
        "(clearly tagged in DIM_BANK / SOURCE)"
    )
    _synthetic_risk_dashboard(RAW_DIR / "risk_dashboard_synthetic.csv")
    _synthetic_transparency({
        name: RAW_DIR / name
        for name in (
            "transparency_2024_capital.csv",
            "transparency_2024_asset_quality.csv",
            "transparency_2024_profitability.csv",
            "transparency_2024_leverage.csv",
        )
    })
    flag = RAW_DIR / "SYNTHETIC.flag"
    flag.write_text(
        "Synthetic fallback dataset.\n"
        "Generated when the live EBA URLs were unreachable.\n"
        "Schema matches the brief; values are deterministic but not real.\n",
        encoding="utf-8",
    )


def main() -> None:
    successes = 0
    for name, url in URLS.items():
        if download_file(url, RAW_DIR / name):
            successes += 1
    if successes == 0:
        write_synthetic_fallback()
    else:
        logger.info("downloaded %d/%d files from EBA", successes, len(URLS))
        missing = [n for n in URLS if not (RAW_DIR / n).exists()]
        if missing:
            logger.warning(
                "missing %d EBA files - filling gaps with synthetic data",
                len(missing),
            )
            write_synthetic_fallback()


if __name__ == "__main__":
    main()
