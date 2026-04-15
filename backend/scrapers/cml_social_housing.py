"""
Populate the `social_housing` table with Lisbon municipal housing estates
(Bairros Municipais) used as a blocker input to the flip/rent scoring engine.

Strategy (fallback chain):
  1. If --arcgis-url is provided or CML_SOCIAL_HOUSING_URL env var set,
     fetch from that ArcGIS FeatureServer (GeoJSON).
  2. Otherwise, try the best-known candidate URLs for CML open data.
  3. Fallback to data/social_housing_seed.csv (curated list of ~20 major
     estates). This seed is always loaded unless --no-seed is passed so
     we have *some* coverage even when the municipal endpoint is offline.

The seed CSV columns: name, municipality, parish, category, lat, lon, notes.

Usage:
    python3 scrapers/cml_social_housing.py                  # seed + try ArcGIS
    python3 scrapers/cml_social_housing.py --seed-only      # only CSV seed
    python3 scrapers/cml_social_housing.py --arcgis-url URL # explicit endpoint
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database as db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SEED_CSV = Path(__file__).resolve().parent.parent / "data" / "social_housing_seed.csv"

# Best-guess candidates. If neither works, the --arcgis-url flag takes over.
# These follow the CML ArcGIS pattern used by cml_projects.py and cml_security.py.
CANDIDATE_ARCGIS_URLS = [
    # Municipal housing patrimony (BAirros — if the layer name matches)
    "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services"
    "/BairrosMunicipais/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&outSR=4326&f=geojson",
    "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services"
    "/HabitacaoMunicipal/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&outSR=4326&f=geojson",
]


def _polygon_centroid(geom: dict) -> Optional[tuple]:
    """Very simple centroid for Polygon/MultiPolygon (first ring of first poly)."""
    if not geom:
        return None
    t = geom.get("type")
    if t == "Point":
        c = geom.get("coordinates")
        if c and len(c) >= 2:
            return float(c[1]), float(c[0])
    if t == "Polygon":
        coords = geom.get("coordinates", [[]])[0]
    elif t == "MultiPolygon":
        coords = (geom.get("coordinates") or [[[]]])[0][0]
    else:
        return None
    if not coords:
        return None
    lat_sum = sum(pt[1] for pt in coords)
    lon_sum = sum(pt[0] for pt in coords)
    n = len(coords)
    return lat_sum / n, lon_sum / n


def _try_arcgis(url: str) -> List[dict]:
    logger.info(f"Trying ArcGIS: {url}")
    req = Request(url, headers={"User-Agent": "lisbon-realestate-scoring/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as e:
        logger.info(f"  → failed: {e}")
        return []
    features = data.get("features") or []
    if not features:
        logger.info("  → 0 features")
        return []

    rows: List[dict] = []
    for f in features:
        props = f.get("properties") or f.get("attributes") or {}
        geom = f.get("geometry") or {}
        c = _polygon_centroid(geom)
        if not c:
            continue
        lat, lon = c
        name = (props.get("NOME") or props.get("DESIGNACAO") or props.get("Name")
                or props.get("name") or f"bairro_{f.get('id')}")
        parish = props.get("FREGUESIA") or props.get("Freguesia") or None
        rows.append({
            "source": "cml_arcgis",
            "name": name,
            "municipality": "Lisboa",
            "parish": parish,
            "category": "bairro_municipal",
            "lat": lat,
            "lon": lon,
            "polygon_geojson": json.dumps(geom) if geom.get("type") != "Point" else None,
        })
    logger.info(f"  → {len(rows)} rows parsed")
    return rows


def load_seed() -> List[dict]:
    if not SEED_CSV.exists():
        logger.info(f"Seed CSV not found: {SEED_CSV}")
        return []
    rows: List[dict] = []
    with SEED_CSV.open() as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                lat = float(r["lat"]); lon = float(r["lon"])
            except (ValueError, TypeError):
                continue
            rows.append({
                "source": "seed_csv",
                "name": r["name"].strip(),
                "municipality": (r.get("municipality") or "Lisboa").strip(),
                "parish": (r.get("parish") or "").strip() or None,
                "category": (r.get("category") or "bairro_municipal").strip(),
                "lat": lat,
                "lon": lon,
                "polygon_geojson": None,
            })
    logger.info(f"Seed CSV: {len(rows)} rows")
    return rows


def upsert(rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    conn = db.get_connection()
    now = datetime.now(timezone.utc).isoformat()
    # Natural key: (source, name). Clear existing for (source,name) pairs then insert.
    keys = [(r["source"], r["name"]) for r in rows]
    conn.executemany(
        "DELETE FROM social_housing WHERE source=? AND name=?", keys,
    )
    conn.executemany(
        """INSERT INTO social_housing
               (source, name, municipality, parish, category, lat, lon,
                polygon_geojson, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(r["source"], r["name"], r["municipality"], r["parish"], r["category"],
          r["lat"], r["lon"], r["polygon_geojson"], now) for r in rows],
    )
    conn.commit()
    conn.close()
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arcgis-url", default=os.environ.get("CML_SOCIAL_HOUSING_URL"))
    parser.add_argument("--seed-only", action="store_true",
                        help="Skip ArcGIS, load seed CSV only")
    parser.add_argument("--no-seed", action="store_true",
                        help="Skip seed CSV")
    args = parser.parse_args()

    all_rows: List[dict] = []
    if not args.seed_only:
        urls = [args.arcgis_url] if args.arcgis_url else CANDIDATE_ARCGIS_URLS
        for u in urls:
            if not u:
                continue
            got = _try_arcgis(u)
            if got:
                all_rows.extend(got)
                break  # first success wins

    if not args.no_seed:
        all_rows.extend(load_seed())

    n = upsert(all_rows)
    print(f"Done. {n} rows in social_housing "
          f"(arcgis: {'yes' if any(r['source']=='cml_arcgis' for r in all_rows) else 'no'}, "
          f"seed: {'yes' if any(r['source']=='seed_csv' for r in all_rows) else 'no'}).")


if __name__ == "__main__":
    main()
