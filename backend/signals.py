"""
Pure signal-producer functions for the flip/rent scoring engine.

Each function takes a listing (dict) plus any cached geospatial rows it needs,
and returns a 0..100 score. None is returned when the signal cannot be
computed (missing inputs). The scoring engine treats None as "skip this
signal and renormalize the remaining weights."

Design rules:
  * Pure functions, no DB I/O inside the signal math. Callers pass the
    geospatial rows. DB-aware wrappers live in signal_batch.py.
  * Distance math uses haversine with a small-delta approximation (fine at
    Lisbon latitudes). No Shapely dependency in the hot path.
  * Scale mappings are parameterized at the top of the file so tuning
    doesn't require touching the function bodies.

Signals implemented (Phase 1):
  * noise                  — blocker, OSM roads + rail + airport corridor
  * social_housing_adj     — blocker, distance to nearest social housing
  * dev_momentum           — positive, private new-construction count nearby
  * public_project_adj     — blocker, social/public projects nearby
  * layout_openness        — positive, derived from building year
  * dark_unit              — blocker, floor × neighbouring-building heights
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable, List, Optional


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

_EARTH_R_M = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r = math.radians(lat1); lat2_r = math.radians(lat2)
    dlat = lat2_r - lat1_r
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(math.sqrt(a))


def _distance_to_linestring_m(
    lat: float, lon: float, coords: List[List[float]],
) -> float:
    """Approximate minimum distance from a point to a polyline (degrees).

    We project into a local equirectangular plane — accurate to <1% at
    Lisbon's latitude for line segments of a few km.
    """
    if not coords or len(coords) < 2:
        return float("inf")
    # meters-per-degree at this latitude
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))

    def _proj(pt):
        # (x, y) in meters, origin at (lat, lon)
        x = (pt[0] - lon) * m_per_deg_lon
        y = (pt[1] - lat) * m_per_deg_lat
        return x, y

    min_d = float("inf")
    for i in range(len(coords) - 1):
        x1, y1 = _proj(coords[i])
        x2, y2 = _proj(coords[i + 1])
        # distance from (0,0) to segment (x1,y1)-(x2,y2)
        dx = x2 - x1; dy = y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-9:
            d = math.hypot(x1, y1)
        else:
            t = -(x1 * dx + y1 * dy) / seg_len_sq
            t = max(0.0, min(1.0, t))
            px = x1 + t * dx; py = y1 + t * dy
            d = math.hypot(px, py)
        if d < min_d:
            min_d = d
    return min_d


# ---------------------------------------------------------------------------
# Noise signal
# ---------------------------------------------------------------------------

# Base impact scores per source kind (0..100 at zero distance).
_NOISE_KIND_IMPACT = {
    # OSM road classifications
    "motorway":      100,
    "trunk":          90,
    "primary":        75,
    "secondary":      55,
    "tertiary":       35,
    "residential":    10,   # basically ignore
    # Rail / airport
    "rail":           90,
    "airport_runway": 95,
    "airport_corridor": 70,
}

# Decay distance in meters: distance at which impact drops to ~37% (exp(-1)).
# Smaller decay = more localized noise.
_NOISE_KIND_DECAY = {
    "motorway":      200,
    "trunk":         175,
    "primary":       120,
    "secondary":     80,
    "tertiary":      50,
    "residential":   30,
    "rail":          120,
    "airport_runway": 500,
    "airport_corridor": 1200,
}


def noise_score(
    listing: dict,
    noise_sources: Iterable[dict],
    max_consider_m: float = 400.0,
) -> Optional[float]:
    """Return the noise exposure for a listing as a 0..100 blocker.

    listing needs `lat` and `lon`.
    noise_sources: iterable of dicts with keys:
        kind   — one of _NOISE_KIND_IMPACT keys
        coords — a polyline as [[lon, lat], ...] OR {"type":"LineString","coordinates":[...]}
    """
    lat = listing.get("lat"); lon = listing.get("lon")
    if lat is None or lon is None:
        return None

    max_impact = 0.0
    for src in noise_sources:
        kind = src.get("kind")
        if kind not in _NOISE_KIND_IMPACT:
            continue
        coords = src.get("coords")
        if isinstance(coords, dict) and coords.get("type") == "LineString":
            coords = coords.get("coordinates")
        if not coords:
            continue

        d = _distance_to_linestring_m(lat, lon, coords)
        if d > max_consider_m:
            continue
        impact = _NOISE_KIND_IMPACT[kind] * math.exp(-d / _NOISE_KIND_DECAY[kind])
        if impact > max_impact:
            max_impact = impact

    return round(max(0.0, min(100.0, max_impact)), 1)


# ---------------------------------------------------------------------------
# Social housing adjacency
# ---------------------------------------------------------------------------

def social_housing_adj_score(
    listing: dict,
    estates: Iterable[dict],
    decay_m: float = 150.0,
    max_consider_m: float = 500.0,
) -> Optional[float]:
    """Blocker 0..100 — proximity to nearest social-housing estate.

    Formula: 100 * exp(-d / decay_m). 0m → 100, 150m → 37, 450m → 5.
    Only estates within max_consider_m contribute; further = 0.
    """
    lat = listing.get("lat"); lon = listing.get("lon")
    if lat is None or lon is None:
        return None

    best = 0.0
    for e in estates:
        e_lat = e.get("lat"); e_lon = e.get("lon")
        if e_lat is None or e_lon is None:
            continue
        d = haversine_m(lat, lon, e_lat, e_lon)
        if d > max_consider_m:
            continue
        impact = 100.0 * math.exp(-d / decay_m)
        if impact > best:
            best = impact
    return round(max(0.0, min(100.0, best)), 1)


# ---------------------------------------------------------------------------
# Construction-project signals
# ---------------------------------------------------------------------------

# Project classification keys. A project row should have these classification
# tags attached by classify_project() below.
PROJECT_CLASS_PRIVATE_DEV = "private_dev"
PROJECT_CLASS_PUBLIC_DEV  = "public_dev"
PROJECT_CLASS_RENOVATION  = "renovation"
PROJECT_CLASS_MINOR       = "minor"


def classify_project(project: dict) -> str:
    """Classify a construction_projects row into one of the PROJECT_CLASS_* tags.

    Heuristics:
      * 'Construção Nova' / new construction → private_dev (unless operator/text
        indicates social/public — word "habitação social", "municipal", "PHR",
        "cooperativa" → public_dev)
      * Renovation-family operations → renovation
      * Tiny scopes ('ocupação via pública', signage, awnings, small
        extensions under 40m²) → minor
    """
    op = (project.get("operation") or project.get("tipo_operacao") or "").lower()
    desc = (project.get("description") or project.get("proprietario") or "").lower()
    combined = op + " " + desc

    public_markers = (
        "habitação social", "habitacao social", "municipal", "phr ",
        " phr", "cooperativa", "camara", "câmara", "ihru",
    )
    minor_markers = (
        "ocupação", "ocupacao", "tapume", "andaime", "toldo",
        "reclamo", "publicidade", "esplanada",
    )
    renovation_markers = (
        "alteração", "alteracao", "reconstrução", "reconstrucao",
        "ampliação", "ampliacao", "beneficiação", "beneficiacao",
        "remodelação", "remodelacao",
    )
    new_markers = (
        "construção nova", "construcao nova", "nova edificação",
        "nova edificacao", "edificação nova",
    )

    if any(m in combined for m in minor_markers):
        return PROJECT_CLASS_MINOR
    if any(m in combined for m in public_markers):
        return PROJECT_CLASS_PUBLIC_DEV
    if any(m in combined for m in new_markers):
        return PROJECT_CLASS_PRIVATE_DEV
    if any(m in combined for m in renovation_markers):
        return PROJECT_CLASS_RENOVATION
    # Default: assume a permit we can't classify is minor (conservative — we
    # don't want unclassified noise driving either signal).
    return PROJECT_CLASS_MINOR


def dev_momentum_score(
    listing: dict,
    projects: Iterable[dict],
    radius_m: float = 300.0,
    recency_years: float = 4.0,
    asof: Optional[datetime] = None,
) -> Optional[float]:
    """Positive 0..100 — private new-construction within radius over the past
    recency_years.

    Scale:  0 projects → 30, 1–2 → 60, 3–5 → 80, 6+ → 95.
    Baseline 30 (not 0) because absence of construction isn't automatically
    negative — it may just mean a stable area.
    """
    lat = listing.get("lat"); lon = listing.get("lon")
    if lat is None or lon is None:
        return None

    asof = asof or datetime.now(timezone.utc)
    cutoff = asof.replace(year=asof.year - int(recency_years))

    count = 0
    for p in projects:
        p_lat = p.get("lat"); p_lon = p.get("lon")
        if p_lat is None or p_lon is None:
            continue
        if classify_project(p) != PROJECT_CLASS_PRIVATE_DEV:
            continue
        # Date filter if available
        p_date = p.get("date") or p.get("alvara_date") or p.get("application_date")
        if p_date:
            try:
                d = datetime.fromisoformat(str(p_date).replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                if d < cutoff:
                    continue
            except Exception:
                pass
        if haversine_m(lat, lon, p_lat, p_lon) > radius_m:
            continue
        count += 1

    if count == 0:    return 30.0
    if count <= 2:    return 60.0
    if count <= 5:    return 80.0
    return 95.0


def public_project_adj_score(
    listing: dict,
    projects: Iterable[dict],
    radius_m: float = 300.0,
    recency_years: float = 4.0,
    asof: Optional[datetime] = None,
) -> Optional[float]:
    """Blocker 0..100 — public/social housing or infra projects nearby.

    Scale: 0 → 0, 1 → 40, 2 → 65, 3+ → 85.
    Tighter penalty curve than dev_momentum — a single social housing project
    is already a meaningful signal for the area trajectory.
    """
    lat = listing.get("lat"); lon = listing.get("lon")
    if lat is None or lon is None:
        return None

    asof = asof or datetime.now(timezone.utc)
    cutoff = asof.replace(year=asof.year - int(recency_years))

    count = 0
    for p in projects:
        p_lat = p.get("lat"); p_lon = p.get("lon")
        if p_lat is None or p_lon is None:
            continue
        if classify_project(p) != PROJECT_CLASS_PUBLIC_DEV:
            continue
        p_date = p.get("date") or p.get("alvara_date") or p.get("application_date")
        if p_date:
            try:
                d = datetime.fromisoformat(str(p_date).replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                if d < cutoff:
                    continue
            except Exception:
                pass
        if haversine_m(lat, lon, p_lat, p_lon) > radius_m:
            continue
        count += 1

    if count == 0:    return 0.0
    if count == 1:    return 40.0
    if count == 2:    return 65.0
    return 85.0


# ---------------------------------------------------------------------------
# Layout openness from building age
# ---------------------------------------------------------------------------

def layout_openness_score(
    listing: dict,
    building_year: Optional[int] = None,
) -> Optional[float]:
    """Positive 0..100 — how likely the unit has a flexible structural system.

    Pre-1930 masonry → rigid → 20
    1930-1970 mixed  → 40
    1970-2000 concrete-frame → 70
    2000+ modern → 85

    When building_year is not provided, try building_year on the listing row.
    """
    year = building_year if building_year is not None else listing.get("building_year")
    if year is None:
        return None
    year = int(year)
    if year < 1930:  return 20.0
    if year < 1970:  return 40.0
    if year < 2000:  return 70.0
    return 85.0


# ---------------------------------------------------------------------------
# Dark unit blocker (floor × shading)
# ---------------------------------------------------------------------------

def dark_unit_score(
    listing: dict,
    neighbour_buildings: Iterable[dict],
    neighbour_radius_m: float = 30.0,
) -> Optional[float]:
    """Blocker 0..100 — likelihood the unit is dark.

    Inputs:
      listing.floor — integer (0 = ground)
      listing.lat/lon
      neighbour_buildings — iterable of {lat, lon, height_m (or levels)}

    Logic:
      * Floor contributes a baseline: 0 → 40, 1 → 25, 2 → 10, 3+ → 0
      * Plus penalty from each tall neighbour within neighbour_radius_m:
        neighbour_floors = height_m/3 (fallback: levels)
        if neighbour_floors >= listing.floor + 2: +15 per neighbour (cap +45)
      * Capped at 100.

    When floor is unknown we can't score meaningfully → return None.
    """
    lat = listing.get("lat"); lon = listing.get("lon")
    floor = listing.get("floor")
    if lat is None or lon is None or floor is None:
        return None
    try:
        floor = int(floor)
    except (ValueError, TypeError):
        return None

    if   floor <= 0:  base = 40.0
    elif floor == 1:  base = 25.0
    elif floor == 2:  base = 10.0
    else:             base = 0.0

    neighbour_penalty = 0.0
    for b in neighbour_buildings:
        b_lat = b.get("lat"); b_lon = b.get("lon")
        if b_lat is None or b_lon is None:
            continue
        if haversine_m(lat, lon, b_lat, b_lon) > neighbour_radius_m:
            continue
        h_m = b.get("height_m")
        if h_m is None and b.get("levels") is not None:
            try: h_m = float(b["levels"]) * 3.0
            except (ValueError, TypeError): h_m = None
        if h_m is None:
            continue
        try: b_floors = float(h_m) / 3.0
        except (ValueError, TypeError): continue
        if b_floors >= floor + 2:
            neighbour_penalty += 15.0

    neighbour_penalty = min(45.0, neighbour_penalty)
    total = base + neighbour_penalty
    return round(max(0.0, min(100.0, total)), 1)


# ---------------------------------------------------------------------------
# Light score (positive, from orientation + floor + hints)
# ---------------------------------------------------------------------------

# Base contributions per cardinal. South is best in the northern hemisphere;
# east (morning sun) slightly better than west (afternoon); north is poor.
_ORIENT_BASE = {
    "S":  70.0,
    "E":  55.0,
    "W":  50.0,
    "N":  20.0,
    # "DUAL" = dual exposure without named cardinal — modest positive
    "DUAL": 55.0,
}


def light_score(listing: dict) -> Optional[float]:
    """Positive 0..100 — expected natural light quality.

    Inputs (on the listing dict):
      orientation     — comma-separated cardinals e.g. "S,E" or "DUAL"
      floor           — integer; higher = more light
      light_hint      — bool; text said "muita luz"/"soalheiro"/etc.

    Returns None only when orientation AND floor are both missing — in that
    case we have no signal at all. If at least one is present we return a
    value so the engine has something to work with.
    """
    orientation = listing.get("orientation")
    floor = listing.get("floor")
    hint = bool(listing.get("light_hint"))

    if orientation is None and floor is None and not hint:
        return None

    # Orientation contribution
    if orientation:
        cards = [c.strip().upper() for c in str(orientation).split(",") if c.strip()]
        base_vals = [_ORIENT_BASE.get(c, 30.0) for c in cards if c]
        if base_vals:
            # Multiple cardinals mean the flat has multiple exposures —
            # take the best one plus a small bonus for diversity.
            base = max(base_vals)
            if len(base_vals) > 1:
                base = min(85.0, base + 10.0)
        else:
            base = 40.0
    else:
        # No orientation info — neutral starting point.
        base = 40.0

    # Floor modifier
    try:
        f = int(floor) if floor is not None else None
    except (ValueError, TypeError):
        f = None
    if f is not None:
        if f <= 0:
            base -= 15.0
        elif f == 1:
            base -= 8.0
        elif f == 2:
            base -= 3.0
        elif f >= 4:
            base += 15.0
        # else floor == 3: neutral

    # Text hint
    if hint:
        base += 15.0

    return round(max(0.0, min(100.0, base)), 1)


__all__ = [
    "noise_score",
    "social_housing_adj_score",
    "dev_momentum_score",
    "public_project_adj_score",
    "layout_openness_score",
    "dark_unit_score",
    "light_score",
    "classify_project",
    "haversine_m",
    "PROJECT_CLASS_PRIVATE_DEV",
    "PROJECT_CLASS_PUBLIC_DEV",
    "PROJECT_CLASS_RENOVATION",
    "PROJECT_CLASS_MINOR",
]
