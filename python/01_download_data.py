"""
Downloads public EBA/ECB datasets to data/raw/.

Sources
-------
1. ECB Data Portal - EBA Risk Indicators (KRI) via SDMX REST API
   Country aggregates + EU/EEA aggregate, quarterly since 2014.
2. EBA Transparency Exercise 2024 - full database
   Bank-by-bank disclosures across four CSVs (cre/mkt/sov/oth) plus
   TR_Metadata.xlsx and SDD.xlsx.

Behaviour
---------
- Idempotent: existing files in data/raw/ are not re-downloaded.
- When a real endpoint is unreachable, a deterministic synthetic dataset
  is generated using the documented EBA/ECB schemas:
    * SDMX-CSV for the ECB KRI series
    * Long EAV layout (LEI_code, NSA, Period, Item, Label, Portfolio,
      Country, Country_rank, Exposure, Status, Perf_Status, NACE_codes,
      Amount, ...) for the EBA Transparency files
  A SYNTHETIC.flag marker is written next to the synthetic files.

Run
---
    python python/01_download_data.py
"""

from __future__ import annotations

import csv
import logging
import random
from pathlib import Path

import requests
from openpyxl import Workbook

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("eba.download")

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (EBA-Dashboard-Builder; research/portfolio)"

# ECB Data Portal SDMX-CSV endpoint for the EBA Key Risk Indicators dataflow.
ECB_KRI_URL = (
    "https://data-api.ecb.europa.eu/service/data/KRI"
    "?format=csvdata&startPeriod=2018-Q1&endPeriod=2024-Q4"
)

# EBA Transparency Exercise 2024 - full database direct file URLs.
# Subject to change between releases; failure triggers the synthetic fallback.
EBA_TE_BASE = "https://www.eba.europa.eu/assets/TE2024/Full_database/256109"
EBA_TE_FILES: dict[str, str] = {
    "tr_oth.csv":         f"{EBA_TE_BASE}/tr_oth.csv",
    "tr_cre.csv":         f"{EBA_TE_BASE}/tr_cre.csv",
    "tr_mkt.csv":         f"{EBA_TE_BASE}/tr_mkt.csv",
    "tr_sov.csv":         f"{EBA_TE_BASE}/tr_sov.csv",
    "TR_Metadata.xlsx":   f"{EBA_TE_BASE}/TR_Metadata.xlsx",
    "SDD.xlsx":           f"{EBA_TE_BASE}/SDD.xlsx",
}


def download_file(url: str, dest: Path, timeout: int = 120) -> bool:
    """Download url to dest. Skip if already cached. Return True on success."""
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
# Synthetic fallback - ECB KRI in SDMX-CSV shape
# ---------------------------------------------------------------------------

ECB_COUNTRIES: tuple[str, ...] = (
    "AT", "BE", "CY", "DE", "DK", "ES", "FI", "FR", "GR", "HR",
    "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "NO", "PL",
    "PT", "RO", "SE", "SI", "SK", "U2",   # U2 = EU/EEA aggregate
)

ECB_QUARTERS: tuple[str, ...] = tuple(
    f"{y}-Q{q}" for y in range(2018, 2025) for q in range(1, 5)
)

# (centre, jitter) in decimal units for the ECB KRI series we surface.
ECB_INDICATORS: dict[str, tuple[float, float]] = {
    "CET1_FL":      (0.155, 0.030),
    "TIER1_FL":     (0.175, 0.030),
    "TOTAL_CAP_FL": (0.198, 0.030),
    "LEV_RATIO":    (0.057, 0.012),
    "LCR":          (1.60,  0.30),
    "NSFR":         (1.27,  0.10),
    "NPL_RATIO":    (0.025, 0.020),
    "NPL_COVERAGE": (0.435, 0.040),
    "ROE":          (0.085, 0.030),
    "CTI":          (0.585, 0.060),
    "LTD_RATIO":    (1.05,  0.10),    # Loan-to-deposit ratio (ECB KRI specific)
}


def _synthetic_ecb_kri(path: Path) -> None:
    """Write a SDMX-CSV-shaped CSV mimicking the ECB KRI dataflow."""
    rng = random.Random(20240630)
    columns = [
        "KEY", "FREQ", "REF_AREA", "KRI_IND",
        "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS", "UNIT_MEASURE",
    ]
    rows: list[list[str]] = [columns]
    for country in ECB_COUNTRIES:
        country_bias = rng.uniform(-0.02, 0.02)
        for indicator, (centre, jitter) in ECB_INDICATORS.items():
            for idx, period in enumerate(ECB_QUARTERS):
                trend = 0.0015 * idx if indicator.startswith("CET1") else 0.0
                value = max(0.0, centre + country_bias + trend
                            + rng.uniform(-jitter, jitter))
                key = f"KRI.Q.{country}.{indicator}"
                unit = "PC" if indicator != "LTD_RATIO" else "PCPA"
                rows.append([
                    key, "Q", country, indicator, period,
                    f"{value:.6f}", "A", unit,
                ])
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    logger.info("synthetic ECB KRI rows: %d", len(rows) - 1)


# ---------------------------------------------------------------------------
# Synthetic fallback - EBA Transparency in EAV shape
# ---------------------------------------------------------------------------

TE_PERIODS: tuple[str, ...] = ("202309", "202312", "202403", "202406")

# Item codes (synthetic but plausible) + labels matching the EBA template
# wording. The 03 cleaner does Label-based fuzzy matching as primary strategy,
# so production Item code drift between exercises is tolerated.
TR_OTH_ITEMS: tuple[tuple[str, str, str, float, float], ...] = (
    # (Item, Label, indicator_code, centre, jitter)
    ("KM01", "Common Equity Tier 1 ratio - fully loaded",  "CET1_FL",      0.155, 0.040),
    ("KM02", "Tier 1 ratio - fully loaded",                "TIER1_FL",     0.175, 0.040),
    ("KM03", "Total capital ratio - fully loaded",         "TOTAL_CAP_FL", 0.198, 0.040),
    ("LR01", "Leverage ratio - fully loaded",              "LEV_RATIO",    0.057, 0.015),
    ("LQ01", "Liquidity coverage ratio (%)",               "LCR",          1.60,  0.30),
    ("LQ02", "Net stable funding ratio (%)",               "NSFR",         1.27,  0.10),
    ("PR01", "Return on equity",                           "ROE",          0.085, 0.035),
    ("PR02", "Return on assets",                           "ROA",          0.007, 0.003),
    ("PR03", "Cost to income ratio",                       "CTI",          0.585, 0.060),
    ("CR01", "Total risk weighted assets (BEUR)",          "RWA_TOTAL",    150.0, 100.0),
)

TR_CRE_ITEMS: tuple[tuple[str, str, str, float, float], ...] = (
    ("CR10", "Non-performing loans ratio",                 "NPL_RATIO",     0.025, 0.020),
    ("CR20", "Coverage ratio of non-performing loans",     "NPL_COVERAGE",  0.435, 0.060),
    ("CR30", "Forborne exposures ratio",                   "FORBORNE_RATIO",0.020, 0.015),
)

# 60 representative banks across 26 countries. Real EBA TE 2024 covers 123 banks;
# this fallback exercises every visual without bloating the repo.
SYNTHETIC_BANKS: tuple[tuple[str, str, str, float, bool], ...] = (
    ("FR_BNPP", "BNP Paribas", "FR", 2592.0, True),
    ("FR_GLE",  "Societe Generale", "FR", 1554.0, True),
    ("FR_ACA",  "Credit Agricole Group", "FR", 2351.0, True),
    ("FR_BPCE", "BPCE", "FR", 1535.0, True),
    ("DE_DBK",  "Deutsche Bank", "DE", 1337.0, True),
    ("DE_CBK",  "Commerzbank", "DE", 553.0, False),
    ("DE_DZB",  "DZ Bank", "DE", 627.0, False),
    ("DE_LBW",  "LBBW", "DE", 332.0, False),
    ("IT_UCG",  "UniCredit", "IT", 802.0, True),
    ("IT_ISP",  "Intesa Sanpaolo", "IT", 964.0, True),
    ("IT_BAMI", "Banco BPM", "IT", 192.0, False),
    ("IT_BMPS", "Monte dei Paschi", "IT", 121.0, False),
    ("ES_SAN",  "Banco Santander", "ES", 1797.0, True),
    ("ES_BBVA", "BBVA", "ES", 775.0, True),
    ("ES_CABK", "CaixaBank", "ES", 612.0, False),
    ("ES_SAB",  "Banco Sabadell", "ES", 240.0, False),
    ("NL_INGA", "ING Groep", "NL", 975.0, True),
    ("NL_ABN",  "ABN AMRO", "NL", 396.0, False),
    ("NL_RABO", "Rabobank", "NL", 631.0, True),
    ("BE_KBC",  "KBC Group", "BE", 354.0, False),
    ("BE_BEL",  "Belfius", "BE", 192.0, False),
    ("AT_ERST", "Erste Group", "AT", 343.0, False),
    ("AT_RBI",  "Raiffeisen Bank Intl", "AT", 211.0, False),
    ("SE_NDA",  "Nordea", "SE", 583.0, True),
    ("SE_SHB",  "Handelsbanken", "SE", 313.0, False),
    ("SE_SEB",  "SEB", "SE", 357.0, False),
    ("SE_SWED", "Swedbank", "SE", 281.0, False),
    ("DK_DAN",  "Danske Bank", "DK", 510.0, False),
    ("DK_JYS",  "Jyske Bank", "DK", 91.0, False),
    ("FI_OP",   "OP Group", "FI", 178.0, False),
    ("NO_DNB",  "DNB Bank", "NO", 332.0, False),
    ("IE_AIB",  "AIB Group", "IE", 134.0, False),
    ("IE_BIRG", "Bank of Ireland", "IE", 156.0, False),
    ("PT_BCP",  "Millennium BCP", "PT", 96.0, False),
    ("PT_CGD",  "Caixa Geral de Depositos", "PT", 100.0, False),
    ("GR_NBG",  "National Bank of Greece", "GR", 75.0, False),
    ("GR_ALPHA","Alpha Bank", "GR", 75.0, False),
    ("GR_PIR",  "Piraeus Bank", "GR", 80.0, False),
    ("GR_ETE",  "Eurobank Ergasias", "GR", 79.0, False),
    ("PL_PKO",  "PKO Bank Polski", "PL", 117.0, True),
    ("PL_PEKAO","Bank Pekao", "PL", 78.0, False),
    ("PL_MBK",  "mBank", "PL", 50.0, True),
    ("HU_OTP",  "OTP Bank", "HU", 95.0, False),
    ("CZ_KB",   "Komercni Banka", "CZ", 64.0, False),
    ("RO_BRD",  "BRD Groupe SG", "RO", 19.0, False),
    ("CY_BOC",  "Bank of Cyprus", "CY", 26.0, False),
    ("LU_BIL",  "Banque Internationale Lux", "LU", 28.0, False),
    ("MT_BOV",  "Bank of Valletta", "MT", 14.0, False),
    ("SI_NLB",  "NLB Group", "SI", 26.0, False),
    ("SK_VUB",  "VUB Banka", "SK", 23.0, False),
    ("HR_ZABA", "Zagrebacka Banka", "HR", 21.0, False),
    ("LV_SWLV", "Swedbank Latvia", "LV", 9.0, False),
    ("LT_SBLT", "Siauliu Bankas", "LT", 6.0, False),
    ("EE_LHV",  "LHV Group", "EE", 8.0, False),
    ("BG_DSK",  "DSK Bank", "BG", 18.0, False),
    ("IS_LAN",  "Landsbankinn", "IS", 13.0, False),
    ("LI_LLB",  "Liechtensteinische LB", "LI", 22.0, False),
    ("FR_HSBC", "HSBC Continental Europe", "FR", 240.0, False),
    ("DE_HCOB", "Hamburg Commercial Bank", "DE", 30.0, False),
    ("DE_NORD", "NORD/LB", "DE", 110.0, False),
    ("IT_MED",  "Mediobanca", "IT", 87.0, False),
)

EAV_COLUMNS: tuple[str, ...] = (
    "LEI_code", "NSA", "Period", "Item", "Label", "Portfolio",
    "Country", "Country_rank", "Exposure", "Status", "Perf_Status",
    "NACE_codes", "Amount", "Footnote", "Row", "Column", "Sheet",
)


def _eav_row(
    lei: str, nsa: str, period: str, item: str, label: str, value: float,
) -> list[str]:
    """Return one EAV record, aggregate flavour (no portfolio / country split)."""
    return [
        lei, nsa, period, item, label,
        "a0",     # Portfolio: a0 = total (aggregated)
        "Total",  # Country: Total = exposure across all counterparties
        "1",      # Country_rank
        "", "", "", "",         # Exposure / Status / Perf_Status / NACE
        f"{value:.6f}",
        "",                     # Footnote
        "r0010", "c0010", item, # Row / Column / Sheet hints
    ]


def _synthetic_eav(
    path: Path,
    items: tuple[tuple[str, str, str, float, float], ...],
    seed: int,
) -> None:
    """Write a Transparency EAV CSV for the given Item catalogue."""
    rng = random.Random(seed)
    rows: list[list[str]] = [list(EAV_COLUMNS)]
    for lei, _name, country, total_assets, is_gsib in SYNTHETIC_BANKS:
        size_adj = 0.0 if total_assets > 300 else (
            0.005 if total_assets > 30 else 0.015
        )
        for period_idx, period in enumerate(TE_PERIODS):
            for item, label, _code, centre, jitter in items:
                base = centre + (size_adj if "CET1" in label else 0.0)
                if "Non-performing" in label or "Cost to income" in label:
                    base = max(0.0, centre - size_adj)
                if is_gsib and "ratio" in label.lower() and "Non-performing" not in label:
                    base += 0.005
                value = max(0.0, base + 0.001 * period_idx
                            + rng.uniform(-jitter, jitter))
                if item == "CR01":     # RWA in BEUR, not a ratio
                    value = max(5.0, total_assets * 0.35
                                + rng.uniform(-jitter, jitter))
                rows.append(_eav_row(lei, country, period, item, label, value))
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    logger.info("synthetic %s rows: %d", path.name, len(rows) - 1)


def _synthetic_metadata(path: Path) -> None:
    """Write a minimal TR_Metadata.xlsx with an Items sheet (Item, Label)."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Items"
    sheet.append(["Item", "Label", "File"])
    for item, label, _code, *_ in TR_OTH_ITEMS:
        sheet.append([item, label, "tr_oth"])
    for item, label, _code, *_ in TR_CRE_ITEMS:
        sheet.append([item, label, "tr_cre"])
    workbook.save(path)
    logger.info("synthetic %s rows: %d", path.name,
                len(TR_OTH_ITEMS) + len(TR_CRE_ITEMS))


def write_synthetic_fallback(missing: list[Path]) -> None:
    """Write synthetic data only for paths in ``missing`` (never overwrites real files)."""
    missing_set = {p.name for p in missing}
    if not missing_set:
        return
    logger.warning(
        "generating synthetic fallback for %d missing file(s): %s",
        len(missing_set), ", ".join(sorted(missing_set)),
    )
    if "ecb_kri.csv" in missing_set:
        _synthetic_ecb_kri(RAW_DIR / "ecb_kri.csv")
    if "tr_oth.csv" in missing_set:
        _synthetic_eav(RAW_DIR / "tr_oth.csv", TR_OTH_ITEMS, seed=20240928)
    if "tr_cre.csv" in missing_set:
        _synthetic_eav(RAW_DIR / "tr_cre.csv", TR_CRE_ITEMS, seed=20240929)
    if "TR_Metadata.xlsx" in missing_set:
        _synthetic_metadata(RAW_DIR / "TR_Metadata.xlsx")
    (RAW_DIR / "SYNTHETIC.flag").write_text(
        "Synthetic fallback dataset.\n"
        "Generated when live ECB / EBA endpoints were unreachable.\n"
        "Shape matches the published schemas:\n"
        "  - ECB KRI : SDMX-CSV (KEY, FREQ, REF_AREA, KRI_IND, TIME_PERIOD, ...)\n"
        "  - EBA TE  : EAV  (LEI_code, NSA, Period YYYYMM, Item, Label, ...)\n"
        "Values are deterministic but NOT real regulatory data.\n",
        encoding="utf-8",
    )


def main() -> None:
    targets: list[tuple[str, Path]] = [
        (ECB_KRI_URL, RAW_DIR / "ecb_kri.csv"),
    ] + [(url, RAW_DIR / name) for name, url in EBA_TE_FILES.items()]

    successes = sum(download_file(url, dest) for url, dest in targets)
    missing = [dest for _, dest in targets if not dest.exists()]
    if missing:
        write_synthetic_fallback(missing)
    if successes:
        logger.info("%d/%d real files cached locally", successes, len(targets))


if __name__ == "__main__":
    main()
