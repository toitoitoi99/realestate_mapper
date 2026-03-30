"""
Neighborhood amenity rating based on OpenStreetMap data.

Queries the Overpass API for nearby amenities within 1km of a listing's coordinates,
scores them across 5 categories, and returns an overall Class A/B/C classification.
Results are cached in SQLite to avoid repeated API calls.
"""

import json
import math
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import database as db

logger = logging.getLogger(__name__)

RADIUS_M = 1000  # 1km search radius
CACHE_TTL_DAYS = 30
COORD_PRECISION = 3  # round to ~111m for cache key

# ── Category definitions ─────────────────────────────────────────────────────

CATEGORY_TAGS: Dict[str, List[Tuple[str, str]]] = {
    "green_spaces": [
        ("leisure", "park"),
        ("leisure", "garden"),
        ("leisure", "playground"),
        ("leisure", "dog_park"),
        ("leisure", "fitness_station"),
        ("amenity", "community_centre"),
    ],
    "convenience": [
        ("shop", "supermarket"),
        ("shop", "convenience"),
        ("amenity", "cafe"),
        ("amenity", "restaurant"),
        ("shop", "mall"),
        ("shop", "bakery"),
    ],
    "education": [
        ("amenity", "school"),
        ("amenity", "university"),
        ("amenity", "kindergarten"),
        ("amenity", "college"),
        ("amenity", "library"),
    ],
    "transportation": [
        ("highway", "bus_stop"),
        ("railway", "station"),
        ("railway", "subway_entrance"),
        ("public_transport", "stop_position"),
        ("amenity", "bicycle_rental"),
    ],
    "healthcare": [
        ("amenity", "hospital"),
        ("amenity", "clinic"),
        ("amenity", "pharmacy"),
        ("amenity", "dentist"),
        ("amenity", "doctors"),
    ],
}

# Scale factors per category (tuned so typical urban area scores ~60-70)
SCALE_FACTORS: Dict[str, float] = {
    "green_spaces": 15.0,
    "convenience": 5.0,
    "education": 20.0,
    "transportation": 4.0,
    "healthcare": 12.0,
}

# Category weights for overall score (must sum to 1.0)
CATEGORY_WEIGHTS: Dict[str, float] = {
    "green_spaces": 0.15,
    "convenience": 0.25,
    "education": 0.15,
    "transportation": 0.25,
    "healthcare": 0.20,
}

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


# ── Build reverse lookup: (tag_key, tag_value) → category ────────────────────

_TAG_TO_CATEGORY: Dict[Tuple[str, str], str] = {}
for cat, tags in CATEGORY_TAGS.items():
    for tag in tags:
        _TAG_TO_CATEGORY[tag] = cat


# ── Haversine distance ───────────────────────────────────────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two lat/lon points."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _distance_weight(distance_m: float) -> float:
    """Linear decay: 1.0 at 0m, 0.0 at RADIUS_M."""
    return max(0.0, 1.0 - (distance_m / RADIUS_M))


# ── Overpass query ───────────────────────────────────────────────────────────

def _build_overpass_query(lat: float, lon: float) -> str:
    """Build a single combined Overpass QL query for all categories.

    Groups tags by key and uses regex alternation to minimise the number of
    union members, which avoids Overpass 504 timeouts.
    """
    # Group all (key, value) pairs by key across all categories
    from collections import defaultdict
    key_values = defaultdict(set)  # type: ignore[type-arg]
    for tags in CATEGORY_TAGS.values():
        for key, value in tags:
            key_values[key].add(value)

    lines = ["[out:json][timeout:60];", "("]
    for key, values in key_values.items():
        regex = "|".join(sorted(values))
        lines.append(
            f'  node["{key}"~"^({regex})$"](around:{RADIUS_M},{lat},{lon});'
        )
        lines.append(
            f'  way["{key}"~"^({regex})$"](around:{RADIUS_M},{lat},{lon});'
        )
    lines.append(");")
    lines.append("out center;")
    return "\n".join(lines)


def _fetch_overpass(lat: float, lon: float) -> list:
    """Query the Overpass API and return the elements list."""
    query = _build_overpass_query(lat, lon)
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(
        OVERPASS_URL,
        data=data,
        headers={"User-Agent": "RealEstateMapper/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("elements", [])
    except Exception as e:
        logger.error(f"[amenity] Overpass API error: {e}")
        return []


# ── Scoring ──────────────────────────────────────────────────────────────────

def _classify_element(element: dict) -> Optional[str]:
    """Return the category for an OSM element, or None if unrecognized."""
    tags = element.get("tags", {})
    for (key, value), cat in _TAG_TO_CATEGORY.items():
        if tags.get(key) == value:
            return cat
    return None


def _element_coords(element: dict) -> Optional[Tuple[float, float]]:
    """Extract lat/lon from an element (node or way with center)."""
    if element.get("type") == "node":
        return element.get("lat"), element.get("lon")
    center = element.get("center", {})
    if center.get("lat") and center.get("lon"):
        return center["lat"], center["lon"]
    return None


def _compute_scores(
    elements: list, lat: float, lon: float
) -> Dict[str, dict]:
    """Compute per-category scores from OSM elements."""
    category_data: Dict[str, dict] = {
        cat: {"weighted_sum": 0.0, "count": 0}
        for cat in CATEGORY_TAGS
    }

    for el in elements:
        cat = _classify_element(el)
        if cat is None:
            continue
        coords = _element_coords(el)
        if coords is None:
            continue
        el_lat, el_lon = coords
        dist = _haversine(lat, lon, el_lat, el_lon)
        weight = _distance_weight(dist)
        if weight > 0:
            category_data[cat]["weighted_sum"] += weight
            category_data[cat]["count"] += 1

    categories = {}
    for cat, data in category_data.items():
        score = min(100.0, data["weighted_sum"] * SCALE_FACTORS[cat])
        categories[cat] = {
            "score": round(score),
            "count": data["count"],
        }

    overall = sum(
        categories[cat]["score"] * CATEGORY_WEIGHTS[cat]
        for cat in CATEGORY_TAGS
    )
    overall = round(overall)

    if overall >= 80:
        classification = "A"
    elif overall >= 50:
        classification = "B"
    else:
        classification = "C"

    return {
        "overall_score": overall,
        "classification": classification,
        "categories": categories,
    }


# ── Public API ───────────────────────────────────────────────────────────────

def get_amenity_rating(lat: float, lon: float) -> dict:
    """
    Get the amenity rating for a location.
    Uses SQLite cache with coordinate rounding.
    """
    lat_key = round(lat, COORD_PRECISION)
    lon_key = round(lon, COORD_PRECISION)

    # Check cache
    cached = db.get_cached_amenity_rating(lat_key, lon_key)
    if cached is not None:
        cached["cached"] = True
        return cached

    # Fetch from Overpass
    elements = _fetch_overpass(lat_key, lon_key)
    if not elements:
        # Don't cache empty results (likely a transient API error/timeout)
        return {
            "overall_score": 0,
            "classification": "C",
            "categories": {cat: {"score": 0, "count": 0} for cat in CATEGORY_TAGS},
            "cached": False,
            "error": "overpass_empty",
        }

    result = _compute_scores(elements, lat_key, lon_key)
    result["cached"] = False

    # Store in cache
    db.cache_amenity_rating(lat_key, lon_key, result)

    return result
