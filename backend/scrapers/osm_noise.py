"""
Fetch major roads + rail lines from OpenStreetMap for the AML/Algarve bbox
and populate the `noise_sources` table for the flip/rent scoring engine.

Strategy:
  1. Single Overpass query per area, filtered to motorway/trunk/primary/
     secondary/tertiary + railway=rail.
  2. Store each way's geometry as a LineString GeoJSON in noise_sources.
  3. Upsert by (source, osm_id) so re-runs are idempotent.

Usage:
    python3 scrapers/osm_noise.py                    # default AML bbox
    python3 scrapers/osm_noise.py --area lisbon      # Lisbon only (faster)
    python3 scrapers/osm_noise.py --area algarve
    python3 scrapers/osm_noise.py --bbox lat1,lon1,lat2,lon2
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database as db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
REQUEST_TIMEOUT = 180
RETRY_DELAY = 10

# Overpass query templates. Keep the scope tight — we only need things that
# produce meaningful residential noise.
ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary")

# (south, west, north, east) bboxes
AREAS = {
    "lisbon":  (38.69, -9.24, 38.80, -9.08),   # just Lisbon city
    "aml":     (38.40, -9.55, 38.95, -8.60),   # full AML (Greater Lisbon)
    "algarve": (36.95, -9.00, 37.55, -7.35),
    "porto":   (41.05, -8.75, 41.25, -8.50),
}


def _overpass_query(q: str) -> dict:
    encoded = q.encode("utf-8")
    req = Request(OVERPASS_URL, data=encoded, method="POST",
                  headers={"User-Agent": "lisbon-realestate-noise/1.0"})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as e:
            logger.warning(f"Overpass attempt {attempt+1} failed: {e}; retrying in {RETRY_DELAY}s")
            time.sleep(RETRY_DELAY)
    raise RuntimeError("Overpass failed after 3 attempts")


def _build_query(bbox: Tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    classes = "|".join(ROAD_CLASSES)
    return f"""
[out:json][timeout:{REQUEST_TIMEOUT}];
(
  way[highway~"^({classes})$"]({s},{w},{n},{e});
  way[railway=rail]({s},{w},{n},{e});
);
out geom;
"""


def _elements_to_rows(elements: list) -> List[dict]:
    """Convert Overpass 'way' elements with 'geometry' into noise_sources rows."""
    rows: List[dict] = []
    for el in elements:
        if el.get("type") != "way":
            continue
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        tags = el.get("tags", {}) or {}
        if "highway" in tags:
            kind = tags["highway"]
            source = "osm_road"
        elif tags.get("railway") == "rail":
            kind = "rail"
            source = "osm_rail"
        else:
            continue
        coords = [[pt["lon"], pt["lat"]] for pt in geom]
        rows.append({
            "source": source,
            "osm_id": f"way/{el['id']}",
            "kind": kind,
            "geometry_geojson": json.dumps({"type": "LineString", "coordinates": coords}),
        })
    return rows


def upsert_rows(rows: List[dict]) -> int:
    """Insert/replace into noise_sources. Returns inserted count."""
    if not rows:
        return 0
    conn = db.get_connection()
    now = datetime.now(timezone.utc).isoformat()
    # Use osm_id + source as the natural key (cannot easily enforce via UNIQUE
    # without a migration; delete-then-insert per osm_id is simpler and safe
    # given modest dataset size).
    osm_ids = [r["osm_id"] for r in rows]
    # Delete in chunks (SQLite IN clause limit)
    CHUNK = 400
    for i in range(0, len(osm_ids), CHUNK):
        chunk = osm_ids[i:i + CHUNK]
        placeholders = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM noise_sources WHERE osm_id IN ({placeholders})", chunk)
    conn.executemany(
        """INSERT INTO noise_sources
               (source, osm_id, kind, geometry_geojson, fetched_at)
           VALUES (?, ?, ?, ?, ?)""",
        [(r["source"], r["osm_id"], r["kind"], r["geometry_geojson"], now)
         for r in rows],
    )
    conn.commit()
    conn.close()
    return len(rows)


def fetch_and_store(bbox: Tuple[float, float, float, float], label: str) -> int:
    logger.info(f"[{label}] querying Overpass ({bbox})...")
    q = _build_query(bbox)
    data = _overpass_query(q)
    elements = data.get("elements", [])
    logger.info(f"[{label}] Overpass returned {len(elements)} elements")
    rows = _elements_to_rows(elements)
    inserted = upsert_rows(rows)
    logger.info(f"[{label}] stored {inserted} noise_sources rows")
    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--area", choices=list(AREAS.keys()), default="aml",
                        help="Named bbox to fetch (default: aml)")
    parser.add_argument("--bbox", type=str, default=None,
                        help="Custom bbox 'south,west,north,east' (overrides --area)")
    args = parser.parse_args()

    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            raise SystemExit("--bbox must be 'south,west,north,east'")
        bbox = tuple(parts); label = "custom"
    else:
        bbox = AREAS[args.area]; label = args.area

    n = fetch_and_store(bbox, label)
    print(f"Done. {n} rows in noise_sources.")


if __name__ == "__main__":
    main()
