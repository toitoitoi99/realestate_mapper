"""
caop_parishes.py — Fetch parish (freguesia) boundaries for all configured areas.

Uses the Overpass API (OpenStreetMap) to fetch admin_level=8 boundaries
(Portuguese freguesias) within each municipality, then converts the OSM
relations to GeoJSON polygons.

Run once (or to refresh):
    python scrapers/caop_parishes.py
    python scrapers/caop_parishes.py --area porto    # single area
"""

import sys
import json
import logging
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"

# ── Municipality definitions per area ────────────────────────────────────────
# Keys are used to look up OSM areas; values are the display name + admin_level
# for the Overpass area query.

# AML = 18 municipalities across Grande Lisboa + Península de Setúbal
AML_MUNICIPALITIES = [
    # Grande Lisboa
    "Lisboa", "Amadora", "Cascais", "Loures", "Mafra",
    "Odivelas", "Oeiras", "Sintra", "Vila Franca de Xira",
    # Península de Setúbal
    "Alcochete", "Almada", "Barreiro", "Moita", "Montijo",
    "Palmela", "Seixal", "Sesimbra", "Setúbal",
]

PORTO_MUNICIPALITIES = ["Porto"]

# Faro district = Algarve (16 municipalities)
ALGARVE_MUNICIPALITIES = [
    "Albufeira", "Alcoutim", "Aljezur", "Castro Marim", "Faro",
    "Lagoa", "Lagos", "Loulé", "Monchique", "Olhão", "Portimão",
    "São Brás de Alportel", "Silves", "Tavira", "Vila do Bispo",
    "Vila Real de Santo António",
]

AREA_CONFIG = {
    "aml": {"municipalities": AML_MUNICIPALITIES, "output": "aml_parishes.geojson"},
    "porto": {"municipalities": PORTO_MUNICIPALITIES, "output": "porto_parishes.geojson"},
    "algarve": {"municipalities": ALGARVE_MUNICIPALITIES, "output": "algarve_parishes.geojson"},
}

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def _overpass_post(query: str, timeout: int = 180) -> Optional[dict]:
    """POST a query to the Overpass API and return parsed JSON."""
    data = "data=" + urllib.request.quote(query)
    req = urllib.request.Request(
        OVERPASS_URL,
        data=data.encode("utf-8"),
        headers={"User-Agent": "realestate-mapper/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            log.warning("[overpass] Rate limited — waiting 30s ...")
            time.sleep(30)
            # Retry once
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e2:
                log.error(f"[overpass] Retry failed: {e2}")
                return None
        log.error(f"[overpass] HTTP {e.code}: {e.reason}")
        return None
    except Exception as e:
        log.error(f"[overpass] Failed: {e}")
        return None


def _merge_ways(ways: List[List[Tuple[float, float]]]) -> List[List[Tuple[float, float]]]:
    """Merge a list of coordinate arrays into closed rings."""
    rings = []
    open_ways = []

    for w in ways:
        if len(w) >= 4 and w[0] == w[-1]:
            rings.append(w)
        else:
            open_ways.append(list(w))

    while open_ways:
        current = open_ways.pop(0)
        changed = True
        while changed:
            changed = False
            for i, w in enumerate(open_ways):
                if current[-1] == w[0]:
                    current.extend(w[1:])
                    open_ways.pop(i)
                    changed = True
                    break
                elif current[-1] == w[-1]:
                    current.extend(list(reversed(w))[1:])
                    open_ways.pop(i)
                    changed = True
                    break
                elif current[0] == w[-1]:
                    current = w + current[1:]
                    open_ways.pop(i)
                    changed = True
                    break
                elif current[0] == w[0]:
                    current = list(reversed(w)) + current[1:]
                    open_ways.pop(i)
                    changed = True
                    break

        if current[0] != current[-1]:
            current.append(current[0])
        if len(current) >= 4:
            rings.append(current)

    return rings


def _relation_to_geojson(rel: dict) -> Optional[dict]:
    """Convert an Overpass relation (with `out geom`) to a GeoJSON geometry."""
    outer_ways = []
    for m in rel.get("members", []):
        if m.get("type") == "way" and m.get("role", "outer") in ("outer", "") and "geometry" in m:
            coords = [(pt["lon"], pt["lat"]) for pt in m["geometry"]]
            outer_ways.append(coords)

    if not outer_ways:
        return None

    rings = _merge_ways(outer_ways)
    if not rings:
        return None

    if len(rings) == 1:
        return {"type": "Polygon", "coordinates": [rings[0]]}
    return {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}


def fetch_municipality_parishes(municipality_name: str) -> List[dict]:
    """Fetch all parishes (freguesias) within a single municipality via Overpass."""
    # Use area lookup: admin_level=7 for Portuguese municipalities
    query = f"""[out:json][timeout:120];
area["name"="{municipality_name}"]["admin_level"="7"]["boundary"="administrative"]->.mun;
rel(area.mun)["admin_level"="8"]["boundary"="administrative"];
out geom;
"""
    log.info(f"[overpass] Fetching parishes for {municipality_name} ...")
    result = _overpass_post(query)
    if not result:
        return []

    features = []
    for el in result.get("elements", []):
        if el.get("type") != "relation":
            continue
        geom = _relation_to_geojson(el)
        if not geom:
            log.warning(f"[overpass] Could not convert geometry for {el.get('tags', {}).get('name', '?')}")
            continue

        tags = el.get("tags", {})
        name = tags.get("name", "")
        if not name:
            continue

        features.append({
            "type": "Feature",
            "properties": {
                "name": name,
                "municipality": municipality_name,
                "code": tags.get("ref:INE", ""),
            },
            "geometry": geom,
        })

    log.info(f"[overpass] {municipality_name}: {len(features)} parishes")
    return features


def fetch_area(area_key: str) -> list:
    """Fetch parishes for all municipalities in an area."""
    cfg = AREA_CONFIG.get(area_key)
    if not cfg:
        log.error(f"Unknown area: {area_key}")
        return []

    municipalities = cfg["municipalities"]
    log.info(f"[{area_key}] Fetching parishes for {len(municipalities)} municipalities ...")

    all_features = []
    for i, mun_name in enumerate(municipalities):
        features = fetch_municipality_parishes(mun_name)
        all_features.extend(features)

        # Be polite to Overpass
        if i < len(municipalities) - 1:
            time.sleep(2)

    log.info(f"[{area_key}] Total: {len(all_features)} parishes")
    return all_features


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch parish boundaries from OpenStreetMap")
    parser.add_argument("--area", choices=list(AREA_CONFIG.keys()), help="Fetch single area")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    areas_to_fetch = [args.area] if args.area else list(AREA_CONFIG.keys())

    for area_key in areas_to_fetch:
        cfg = AREA_CONFIG[area_key]
        output_path = DATA_DIR / cfg["output"]

        features = fetch_area(area_key)
        if not features:
            log.error(f"[{area_key}] No features — skipping")
            continue

        geojson = {"type": "FeatureCollection", "features": features}
        output_path.write_text(
            json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Summary by municipality
        by_mun = {}  # type: Dict[str, List[str]]
        for f in features:
            mun = f["properties"]["municipality"]
            by_mun.setdefault(mun, []).append(f["properties"]["name"])

        print(f"\n✓ {area_key}: {len(features)} parishes saved to {output_path.name}")
        for mun, parishes in sorted(by_mun.items()):
            names = ", ".join(sorted(parishes)[:5])
            suffix = "..." if len(parishes) > 5 else ""
            print(f"  {mun} ({len(parishes)}): {names}{suffix}")


if __name__ == "__main__":
    main()
