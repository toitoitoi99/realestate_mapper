"""
cml_security.py — Fetch Lisbon public safety POIs from CML open data.

Source: POISeguranca FeatureServer (geodados-cml ArcGIS)
  Layer 0 — Polícia Municipal  (1 station)
  Layer 1 — Polícia de Segurança Pública  (100 stations / esquadras)
  Layer 2 — CCTV_BairroAlto  (25 cameras in Bairro Alto)

Run:
    python3 scrapers/cml_security.py
"""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).parent.parent))
import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL = (
    "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services"
    "/POISeguranca/FeatureServer/{layer}/query"
    "?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)

LAYERS = {
    0: "police_municipal",
    1: "police_psp",
    2: "cctv",
}


def fetch_layer(layer_id: int) -> list:
    url = BASE_URL.format(layer=layer_id)
    log.info(f"Fetching layer {layer_id} ({LAYERS[layer_id]}): {url}")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; lisbon-realestate-bot/1.0)"})
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        raise RuntimeError(f"Request failed for layer {layer_id}: {e}") from e

    features = data.get("features", [])
    log.info(f"  → {len(features)} features")
    return features


def parse_feature(feature: dict, layer_type: str, fetched_at: str) -> dict:
    props = feature.get("properties") or feature.get("attributes") or {}
    geom  = feature.get("geometry", {})
    coords = geom.get("coordinates", [None, None])
    lon, lat = coords[0], coords[1]

    # Normalise field names across the three layers
    if layer_type == "police_municipal":
        source_id = props.get("COD_SIG") or str(props.get("OBJECTID", ""))
        name      = props.get("INF_NOME")
        address   = props.get("INF_MORADA")
        parish    = props.get("FREGUESIA")
        phone     = props.get("INF_TELEFONE")
    elif layer_type == "police_psp":
        source_id = props.get("COD_SIG") or str(props.get("OBJECTID", ""))
        name      = props.get("NOME")
        address   = props.get("MORADA")
        parish    = None
        phone     = props.get("TELEFONE")
    else:  # cctv
        source_id = props.get("COD_SIG") or str(props.get("OBJECTID", ""))
        name      = props.get("NOME")
        address   = props.get("MORADA")
        parish    = None
        phone     = None

    return {
        "source_id":  source_id,
        "layer":      layer_type,
        "name":       name,
        "address":    address,
        "parish":     parish,
        "phone":      phone,
        "lat":        lat,
        "lon":        lon,
        "fetched_at": fetched_at,
    }


def run():
    db.init_db()
    fetched_at = datetime.utcnow().isoformat()
    totals = {}

    for layer_id, layer_type in LAYERS.items():
        features = fetch_layer(layer_id)
        count = 0
        for feat in features:
            poi = parse_feature(feat, layer_type, fetched_at)
            if poi["lat"] is None or poi["lon"] is None:
                continue
            db.upsert_security_poi(poi)
            count += 1
        totals[layer_type] = count
        log.info(f"  Stored {count} {layer_type} POIs")

    print(f"\n✓ Security POIs fetched:")
    for layer_type, count in totals.items():
        print(f"  {layer_type:<20} {count}")


if __name__ == "__main__":
    run()
