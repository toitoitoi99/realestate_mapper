"""
cml_projects.py — Fetch Lisbon construction projects from the CML open data portal.

Sources (ArcGIS FeatureServer, live GeoJSON API):
  Layer 0 — Issued permits (alvarás emitidos): confirmed construction approved
  Layer 1 — In-process applications (processos em curso): planned / pending

Run:
    python scrapers/cml_projects.py           # fetch both layers
    python scrapers/cml_projects.py --layer 0 # permits only
    python scrapers/cml_projects.py --layer 1 # applications only
"""

import sys
import json
import time
import logging
import argparse
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL = (
    "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services"
    "/AlvarasObras/FeatureServer/{layer}/query"
    "?outFields=*&where=1%3D1&f=geojson"
    "&resultOffset={offset}&resultRecordCount={page_size}"
)

LAYER_NAMES = {
    0: "permit",
    1: "application",
}

PAGE_SIZE = 1000


# ── Geometry helpers ───────────────────────────────────────────────────────────

def centroid(geometry: dict) -> tuple:
    """Compute centroid of a GeoJSON Polygon or MultiPolygon."""
    try:
        gtype = geometry.get("type")
        if gtype == "Polygon":
            rings = geometry["coordinates"]
            coords = rings[0]  # exterior ring
        elif gtype == "MultiPolygon":
            # use first polygon's exterior ring
            coords = geometry["coordinates"][0][0]
        else:
            return None, None

        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]
        return sum(lats) / len(lats), sum(lons) / len(lons)
    except (KeyError, IndexError, ZeroDivisionError):
        return None, None


def epoch_ms_to_iso(val) -> Optional[str]:
    """Convert ArcGIS epoch-millisecond timestamp to ISO date string."""
    if val is None:
        return None
    try:
        return datetime.utcfromtimestamp(int(val) / 1000).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ── Fetcher ───────────────────────────────────────────────────────────────────

def fetch_layer(layer_num: int) -> list[dict]:
    """Fetch all features from one FeatureServer layer, handling pagination."""
    layer_name = LAYER_NAMES[layer_num]
    features = []
    offset = 0

    while True:
        url = BASE_URL.format(layer=layer_num, offset=offset, page_size=PAGE_SIZE)
        log.info(f"[cml/{layer_name}] Fetching offset={offset}…")

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            log.error(f"[cml/{layer_name}] Request failed: {e}")
            break

        batch = data.get("features", [])
        log.info(f"[cml/{layer_name}] Got {len(batch)} features")
        features.extend(batch)

        # Continue paginating if we received a full page (API may not set exceededTransferLimit)
        if len(batch) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(0.5)  # be polite to the API

    return features


def feature_to_record(feature: dict, layer_num: int) -> Optional[dict]:
    """Convert a GeoJSON feature to a DB record dict."""
    props = feature.get("properties") or {}
    geometry = feature.get("geometry")

    source_id = str(props.get("N_PROCESSO") or props.get("OBJECTID") or "")
    if not source_id:
        return None

    lat, lon = centroid(geometry) if geometry else (None, None)

    return {
        "source_id":      source_id,
        "layer":          LAYER_NAMES[layer_num],
        "address":        props.get("MORADA"),
        "parish":         props.get("FREGUESIA"),
        "operation":      props.get("OP_URBANISTICA"),
        "subject":        props.get("ASSUNTO"),
        "procedure":      props.get("PROCEDIMENTO"),
        "typology":       props.get("TIPOLOGIA"),
        "date_submitted": epoch_ms_to_iso(props.get("DATA_ENTRADA")),
        "permit_number":  props.get("N_ALVARA"),
        "date_permit":    epoch_ms_to_iso(props.get("DATA_ALVARA")),
        "permit_type":    props.get("TIPO_ALVARA"),
        "geometry":       json.dumps(geometry) if geometry else None,
        "centroid_lat":   lat,
        "centroid_lon":   lon,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def fetch_and_store(layers: list[int] = (0, 1)):
    db.init_db()
    total_new = total_updated = total_errors = 0

    for layer_num in layers:
        features = fetch_layer(layer_num)
        log.info(f"[cml] Processing {len(features)} features for layer {layer_num}…")

        new = updated = errors = 0
        for feat in features:
            try:
                record = feature_to_record(feat, layer_num)
                if record is None:
                    errors += 1
                    continue
                is_new = db.upsert_project(record)
                if is_new:
                    new += 1
                else:
                    updated += 1
            except Exception as e:
                log.warning(f"[cml] Error processing feature: {e}")
                errors += 1

        log.info(
            f"[cml/layer{layer_num}] Done: {new} new, {updated} updated, {errors} errors"
        )
        total_new += new
        total_updated += updated
        total_errors += errors

    print(f"\n✓ CML projects fetch complete")
    print(f"  New     : {total_new}")
    print(f"  Updated : {total_updated}")
    print(f"  Errors  : {total_errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Lisbon construction projects from CML open data")
    parser.add_argument("--layer", type=int, choices=[0, 1],
                        help="Fetch only one layer: 0=permits, 1=applications")
    args = parser.parse_args()

    layers = [args.layer] if args.layer is not None else [0, 1]
    fetch_and_store(layers)
