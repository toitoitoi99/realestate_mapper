"""
Fetch OSM building footprints for listings that don't have one yet.

Strategy:
  1. Group listings without building_geojson by proximity (bounding boxes)
  2. Query the Overpass API once per bbox to get all building ways
  3. Use Shapely point-in-polygon to match each listing to its building
  4. Store the GeoJSON polygon in listings.building_geojson

Usage:
    python3 scrapers/osm_buildings.py          # backfill all listings missing footprints
    python3 scrapers/osm_buildings.py --limit 50   # process at most 50 listings
"""

import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import List, Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database as db

try:
    from shapely.geometry import shape, Point, mapping
except ImportError:
    print("ERROR: shapely is required.  pip3 install shapely")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Pause between Overpass requests to respect rate limits
REQUEST_DELAY = 1.5
# Radius in metres around a listing to search for buildings
SEARCH_RADIUS_M = 35
# Group listings within this degree-distance into a single bbox query
CLUSTER_DEG = 0.005  # ~500 m


def _overpass_query(query: str) -> dict:
    """Send a query to the Overpass API and return the JSON response."""
    encoded = query.encode("utf-8")
    req = Request(
        OVERPASS_URL,
        data=b"data=" + encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    for attempt in range(3):
        try:
            with urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 429:
                wait = 10 * (attempt + 1)
                logger.warning(f"Overpass 429 — waiting {wait}s")
                time.sleep(wait)
                continue
            raise
        except URLError:
            if attempt < 2:
                time.sleep(5)
                continue
            raise
    return {"elements": []}


def _build_polygons_from_overpass(data: dict) -> list:
    """
    Convert Overpass JSON elements into a list of (osm_id, Shapely Polygon, geojson_dict).
    Handles 'way' elements with their node references.
    """
    # Build node lookup
    nodes = {}
    for el in data.get("elements", []):
        if el["type"] == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])

    polygons = []
    for el in data.get("elements", []):
        if el["type"] != "way":
            continue
        node_ids = el.get("nodes", [])
        if len(node_ids) < 4:
            continue
        coords = []
        for nid in node_ids:
            if nid in nodes:
                lat, lon = nodes[nid]
                coords.append((lon, lat))  # GeoJSON is [lon, lat]
        if len(coords) < 4:
            continue
        # Ensure closed ring
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        try:
            geojson = {"type": "Polygon", "coordinates": [coords]}
            poly = shape(geojson)
            if poly.is_valid and poly.area > 0:
                polygons.append((el["id"], poly, geojson))
        except Exception:
            continue
    return polygons


def _find_building_for_point(lat: float, lon: float, polygons: list) -> Optional[dict]:
    """Return the GeoJSON dict of the building polygon containing (lat, lon), or None."""
    pt = Point(lon, lat)
    best = None
    best_area = float("inf")
    for osm_id, poly, geojson in polygons:
        if poly.contains(pt):
            # If point is inside multiple buildings (rare), pick the smallest
            if poly.area < best_area:
                best = geojson
                best_area = poly.area
    if best:
        return best

    # Fallback: find the nearest building within a small buffer (~20m ≈ 0.0002°)
    min_dist = float("inf")
    for osm_id, poly, geojson in polygons:
        d = poly.distance(pt)
        if d < min_dist:
            min_dist = d
            best = geojson
    # Only accept if within ~25m (roughly 0.00025°)
    if min_dist < 0.00025:
        return best
    return None


def _cluster_listings(listings: list) -> list:
    """
    Group listings into bounding-box clusters so we can batch Overpass queries.
    Returns list of (bbox_dict, [listings]) where bbox_dict has s, n, w, e.
    """
    if not listings:
        return []

    # Simple grid-based clustering
    clusters = {}
    for l in listings:
        lat, lon = l["lat"], l["lon"]
        grid_key = (round(lat / CLUSTER_DEG) * CLUSTER_DEG,
                    round(lon / CLUSTER_DEG) * CLUSTER_DEG)
        clusters.setdefault(grid_key, []).append(l)

    result = []
    for (clat, clon), group in clusters.items():
        lats = [l["lat"] for l in group]
        lons = [l["lon"] for l in group]
        # Add padding so Overpass catches buildings at the edges
        pad = 0.0005  # ~50m
        bbox = {
            "s": min(lats) - pad,
            "n": max(lats) + pad,
            "w": min(lons) - pad,
            "e": max(lons) + pad,
        }
        result.append((bbox, group))
    return result


def fetch_buildings_for_listings(limit: Optional[int] = None):
    """Main entry point: fetch and store building footprints for listings."""
    conn = db.get_connection()

    # Get listings that need building footprints
    query = """
        SELECT id, lat, lon FROM listings
        WHERE lat IS NOT NULL AND lon IS NOT NULL
          AND (building_geojson IS NULL OR building_geojson = '')
        ORDER BY id
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    listings = [dict(r) for r in conn.execute(query).fetchall()]
    conn.close()

    if not listings:
        logger.info("All listings already have building footprints (or no listings with coords)")
        return

    logger.info(f"Found {len(listings)} listings needing building footprints")

    clusters = _cluster_listings(listings)
    logger.info(f"Grouped into {len(clusters)} Overpass queries")

    matched = 0
    total = 0

    for i, (bbox, group) in enumerate(clusters):
        # Overpass QL: get all building ways in the bbox
        query = f"""
[out:json][timeout:30];
way["building"]({bbox['s']:.6f},{bbox['w']:.6f},{bbox['n']:.6f},{bbox['e']:.6f});
(._;>;);
out body;
"""
        logger.info(
            f"[{i+1}/{len(clusters)}] Querying Overpass for bbox "
            f"({bbox['s']:.4f},{bbox['w']:.4f},{bbox['n']:.4f},{bbox['e']:.4f}) "
            f"— {len(group)} listings"
        )

        try:
            data = _overpass_query(query)
        except Exception as e:
            logger.error(f"Overpass query failed: {e}")
            time.sleep(REQUEST_DELAY)
            continue

        polygons = _build_polygons_from_overpass(data)
        logger.info(f"  Got {len(polygons)} building polygons")

        conn = db.get_connection()
        for l in group:
            total += 1
            geojson = _find_building_for_point(l["lat"], l["lon"], polygons)
            if geojson:
                conn.execute(
                    "UPDATE listings SET building_geojson=? WHERE id=?",
                    (json.dumps(geojson), l["id"])
                )
                matched += 1
        conn.commit()
        conn.close()

        if i < len(clusters) - 1:
            time.sleep(REQUEST_DELAY)

    logger.info(f"Done: matched {matched}/{total} listings to building footprints")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch OSM building footprints for listings")
    parser.add_argument("--limit", type=int, default=None, help="Max listings to process")
    args = parser.parse_args()

    db.init_db()
    fetch_buildings_for_listings(limit=args.limit)
