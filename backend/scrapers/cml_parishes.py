"""
cml_parishes.py — Fetch Lisbon parish (freguesia) boundaries from CML open data.

Tries the CML ArcGIS FeatureServer first, then falls back to the CML
Opendatasoft export endpoint. Saves the result to data/lisbon_parishes.geojson
which is served by the API at /api/parishes.

Run once (or to refresh):
    python scrapers/cml_parishes.py
"""

import sys
import json
import logging
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "lisbon_parishes.geojson"

# ── Source URLs ────────────────────────────────────────────────────────────────
# Same ArcGIS org as construction-projects scraper (1dSrzEWVQn5kHHyK)
ARCGIS_URL = (
    "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services"
    "/CML_DivAdm_Freg/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)
ARCGIS_URL_ALT = (
    "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services"
    "/CML_DivAdm/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)
OPENDATASOFT_URL = (
    "https://dados.cm-lisboa.pt/api/explore/v2.1/catalog/datasets"
    "/limite-das-freguesias-da-cidade-de-lisboa/exports/geojson"
    "?lang=pt&timezone=Europe%2FLisbon"
)

# Canonical 2012 parish names (case-normalised from whatever the API returns)
CANONICAL = {
    n.upper(): n for n in [
        "Ajuda", "Alcântara", "Alvalade", "Areeiro", "Arroios",
        "Avenidas Novas", "Beato", "Belém", "Benfica", "Campo de Ourique",
        "Campolide", "Carnide", "Estrela", "Lumiar", "Marvila",
        "Misericórdia", "Olivais", "Parque das Nações", "Penha de França",
        "Santa Clara", "Santa Maria Maior", "Santo António",
        "São Domingos de Benfica", "São Vicente",
    ]
}


def _fetch(url: str, label: str):
    log.info(f"[parishes] Trying {label} …")
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json, */*"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        features = data.get("features", [])
        log.info(f"[parishes] {label}: {len(features)} raw features")
        return features
    except Exception as e:
        log.warning(f"[parishes] {label} failed: {e}")
        return None


def _pick_name(props: dict) -> str:
    """Extract parish name from properties, trying common key names."""
    for key in ("FREGUESIA", "NOME_FREG", "NOME", "nome", "Name", "name", "DESIGNACAO"):
        val = props.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return ""


def _normalize_name(raw: str) -> str:
    """Map raw API name to canonical 2012 parish name."""
    # Direct lookup by uppercase key
    upper = raw.upper().strip()
    if upper in CANONICAL:
        return CANONICAL[upper]
    # Already correct case
    if raw in CANONICAL.values():
        return raw
    # Partial match (some APIs include municipality prefix like "Lisboa - Ajuda")
    for key, canonical in CANONICAL.items():
        if key in upper:
            return canonical
    return raw  # return as-is if unknown


def normalize_features(raw_features: list) -> list:
    """Normalize raw GeoJSON features to clean { name, code } properties."""
    out = []
    seen = set()
    for feat in raw_features:
        props = feat.get("properties") or {}
        geometry = feat.get("geometry")
        if not geometry:
            continue

        raw_name = _pick_name(props)
        if not raw_name:
            log.debug(f"[parishes] Skipping feature with no name — keys: {list(props.keys())[:8]}")
            continue

        name = _normalize_name(raw_name)

        if name in seen:
            log.debug(f"[parishes] Duplicate: {name}")
            continue
        seen.add(name)

        code = str(props.get("DICOFRE") or props.get("COD_FREG") or props.get("id") or "")

        out.append({
            "type": "Feature",
            "properties": {"name": name, "code": code},
            "geometry": geometry,
        })

    return out


def fetch_parishes() -> list:
    for url, label in [
        (ARCGIS_URL, "CML ArcGIS (CML_DivAdm_Freg)"),
        (ARCGIS_URL_ALT, "CML ArcGIS (CML_DivAdm)"),
        (OPENDATASOFT_URL, "CML Opendatasoft"),
    ]:
        raw = _fetch(url, label)
        if raw is None:
            continue
        normalized = normalize_features(raw)
        if normalized:
            log.info(f"[parishes] Using {label} — {len(normalized)} parishes")
            return normalized
        log.warning(f"[parishes] {label} returned features but none could be normalised")

    return []


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    features = fetch_parishes()

    if not features:
        log.error(
            "[parishes] Failed to fetch parish boundaries from any source.\n"
            "  Check network access and try again, or manually place a\n"
            f"  valid GeoJSON FeatureCollection at: {OUTPUT_FILE}"
        )
        sys.exit(1)

    geojson = {"type": "FeatureCollection", "features": features}
    OUTPUT_FILE.write_text(json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info(f"[parishes] Saved to {OUTPUT_FILE}")
    print(f"\n✓ {len(features)} parishes saved to {OUTPUT_FILE.name}:")
    for f in sorted(features, key=lambda x: x["properties"]["name"]):
        print(f"  {f['properties']['name']}")


if __name__ == "__main__":
    main()
