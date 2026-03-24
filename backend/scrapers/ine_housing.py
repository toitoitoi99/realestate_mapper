"""
ine_housing.py — Fetch INE aggregate housing transaction price data for the
Lisbon Metropolitan Area (Área Metropolitana de Lisboa).

Data source: INE "Estatísticas de Preços da Habitação ao Nível Local"
             Indicator 0012234: Median €/m² of transacted dwellings
             (quarterly, rolling 12-month window, NUTS-2024 geocodes)

Run:
    python3 scrapers/ine_housing.py          # fetch all AML municipalities
    python3 scrapers/ine_housing.py --show   # print latest stats and exit
    python3 scrapers/ine_housing.py --geocod 1A01106  # single municipality
"""

import sys
import json
import re
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).parent.parent))
import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

INE_API   = "https://www.ine.pt/ine/json_indicador/pindica.jsp"
INDICATOR = "0012234"   # Median €/m² transacted dwellings, quarterly, NUTS-2024

# All 18 municipalities of the Área Metropolitana de Lisboa
AML_MUNICIPALITIES = {
    # Grande Lisboa (north bank)
    "1A01105": "Cascais",
    "1A01106": "Lisboa",
    "1A01107": "Loures",
    "1A01109": "Mafra",
    "1A01110": "Oeiras",
    "1A01111": "Sintra",
    "1A01114": "Vila Franca de Xira",
    "1A01115": "Amadora",
    "1A01116": "Odivelas",
    # Península de Setúbal (south bank)
    "1B01502": "Alcochete",
    "1B01503": "Almada",
    "1B01504": "Barreiro",
    "1B01506": "Moita",
    "1B01507": "Montijo",
    "1B01508": "Palmela",
    "1B01510": "Seixal",
    "1B01511": "Sesimbra",
    "1B01512": "Setúbal",
}

CATEGORY_LABELS = {
    "H1":  "Total",
    "H11": "New",
    "H12": "Existing",
}


# ── Period helpers ─────────────────────────────────────────────────────────────

def _period_sort_key(label: str) -> str:
    """Convert "3rd Quarter 2025" → "2025Q3" for chronological sorting."""
    m = re.match(r'(\d+)(?:st|nd|rd|th) Quarter (\d{4})', label)
    if m:
        return f"{m.group(2)}Q{m.group(1)}"
    return label


# ── INE API fetch ─────────────────────────────────────────────────────────────

def fetch_ine_data(geocod: str) -> list:
    """
    Fetch all available quarterly housing transaction prices from INE for one
    municipality geocode. Returns a list of stat dicts ready for upsert.
    Returns empty list if the municipality has no data (smaller areas may lack it).
    """
    url = f"{INE_API}?op=2&varcd={INDICATOR}&Dim1=T&Dim2={geocod}&lang=EN"
    log.info(f"  {geocod} ({AML_MUNICIPALITIES.get(geocod, geocod)}): {url}")

    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; lisbon-realestate-bot/1.0)"})
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except URLError as e:
        log.warning(f"  Request failed for {geocod}: {e}")
        return []

    payload = json.loads(raw)
    if not payload:
        return []

    item = payload[0]
    sucesso = item.get("Sucesso", {})
    if "Falso" in sucesso:
        log.warning(f"  INE API error for {geocod}: {sucesso['Falso']}")
        return []

    dados = item.get("Dados", {})
    latest_label = item.get("UltimoPref", "")

    if not dados:
        log.info(f"  No data available for {geocod}")
        return []

    log.info(f"  → {len(dados)} period(s), latest: {latest_label}")

    fetched_at = datetime.utcnow().isoformat()
    results = []

    for period_label in sorted(dados.keys(), key=_period_sort_key):
        for rec in dados[period_label]:
            category = rec.get("dim_3", "H1")
            valor_str = (rec.get("valor") or "").strip()
            try:
                median_price = float(valor_str) if valor_str else None
            except ValueError:
                median_price = None

            results.append({
                "period_label":         period_label,
                "geocod":               rec.get("geocod", geocod),
                "geodsg":               rec.get("geodsg", AML_MUNICIPALITIES.get(geocod, geocod)),
                "category":             category,
                "category_label":       CATEGORY_LABELS.get(category, category),
                "median_price_per_sqm": median_price,
                "fetched_at":           fetched_at,
                "is_latest":            period_label == latest_label,
            })

    return results


# ── CLI entry point ────────────────────────────────────────────────────────────

def run(show_only: bool = False, single_geocod: Optional[str] = None):
    db.init_db()

    if show_only:
        stats = db.get_ine_stats(geocod=None, latest_only=True)
        if not stats:
            print("No INE data in database. Run without --show first.")
            return
        # Print one row per municipality (H1 = Total only)
        rows = [s for s in stats if s["category"] == "H1"]
        rows.sort(key=lambda s: s.get("median_price_per_sqm") or 0, reverse=True)
        print(f"\n{'Municipality':<25} {'Period':<20} {'€/m² sold':>10}")
        print("-" * 60)
        for s in rows:
            price = s["median_price_per_sqm"]
            price_str = f"€{price:,.0f}" if price else "—"
            print(f"{s['geodsg']:<25} {s['period_label']:<20} {price_str:>10}")
        return

    targets = {single_geocod: AML_MUNICIPALITIES.get(single_geocod, single_geocod)} \
        if single_geocod else AML_MUNICIPALITIES

    total_stored = 0
    municipalities_with_data = 0

    log.info(f"Fetching INE housing prices for {len(targets)} municipalities...")
    for geocod in targets:
        stats = fetch_ine_data(geocod)
        if stats:
            municipalities_with_data += 1
            for stat in stats:
                db.upsert_ine_stat(stat)
                total_stored += 1
        time.sleep(0.5)   # be polite to INE's servers

    log.info(f"Done. Stored/updated {total_stored} records across {municipalities_with_data} municipalities.")
    print(f"\n✓ INE data fetched for {municipalities_with_data}/{len(targets)} municipalities")
    print(f"  Run with --show to see latest prices")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch INE housing transaction prices for AML municipalities")
    parser.add_argument("--show", action="store_true",
                        help="Print latest stored stats for all municipalities and exit")
    parser.add_argument("--geocod", default=None,
                        help="Fetch a single municipality by geocod (e.g. 1A01106)")
    args = parser.parse_args()
    run(show_only=args.show, single_geocod=args.geocod)
