"""
Stratified sampling for the onboarding swipe deck.

Goal: return a small set of listings that maximises the information we learn
from each user swipe. Picks span multiple axes (parish, price tier, style,
outdoor, etc.) so consecutive cards differ on at least 2 dimensions.

Persona-aware: the relevant axes change for a flipper vs a home renter.

Pure SQL + Python — no ML, no external deps. Designed to be cheap (one
SELECT per call, in-memory pick).
"""
from __future__ import annotations

import json
import random
import sqlite3
from typing import Iterable, List, Optional

import database as db
from persona_engine import PERSONA_BUNDLE_SOURCE

# How many candidates to pull from SQLite before stratifying. Larger = better
# axis coverage; smaller = faster. 600 is plenty for a 12-card deck.
CANDIDATE_POOL = 600

# Columns we need for stratification + render. Keep narrow to keep the
# response small.
SELECT_COLS = (
    "id, source, source_id, url, price_amount, price_per_sqm, size_sqm, "
    "rooms, bedrooms, parish, neighborhood, lat, lon, images, "
    "style_primary, outdoor_type, renovation_class, flip_factors, rent_factors, "
    "scraped_at"
)

# Persona → which sale/rent table + which axes to vary across the deck.
# Each axis is a function (row) -> bin label (or None when unknown). The
# greedy picker tries to ensure consecutive picks differ on >= 2 axes.
PERSONA_VIEW = {
    "rental_investor": "sales",
    "flipper":         "sales",
    "home_buyer":      "sales",
    "home_renter":     "rentals",
}


def _price_tier(row: dict) -> Optional[str]:
    p = row.get("price_amount")
    if p is None:
        return None
    if p < 250_000:  return "entry"
    if p < 600_000:  return "mid"
    return "premium"


def _rent_tier(row: dict) -> Optional[str]:
    p = row.get("price_amount")
    if p is None:
        return None
    if p < 1200: return "budget"
    if p < 2000: return "mid"
    return "premium"


def _size_bucket(row: dict) -> Optional[str]:
    s = row.get("size_sqm")
    if s is None:
        return None
    if s < 60:  return "small"
    if s < 110: return "medium"
    return "large"


def _bedrooms_bucket(row: dict) -> Optional[str]:
    b = row.get("bedrooms")
    if b is None:
        return None
    if b <= 1: return "studio_1br"
    if b <= 2: return "2br"
    return "3br_plus"


def _outdoor_bucket(row: dict) -> Optional[str]:
    o = row.get("outdoor_type")
    if o is None:
        return None
    return "no_outdoor" if o == "none" else "has_outdoor"


def _signal_band(row: dict, signal: str, src_col: str) -> Optional[str]:
    """Bin a signal from the flip/rent factors bundle into low/mid/high."""
    raw = row.get(src_col)
    if not raw:
        return None
    try:
        positives = (json.loads(raw).get("bundle") or {}).get("positives") or {}
    except (json.JSONDecodeError, TypeError):
        return None
    v = positives.get(signal)
    if v is None:
        return None
    v = float(v)
    if v < 33:  return "low"
    if v < 66:  return "mid"
    return "high"


# Per-persona axis list. Order matters: the greedy picker enforces a difference
# on the first N axes for variety. Each entry is (name, fn).
def _axes_for(persona_id: str):
    if persona_id == "flipper":
        return [
            ("market_discount", lambda r: _signal_band(r, "market_discount", "flip_factors")),
            ("renovation",      lambda r: r.get("renovation_class")),
            ("parish",          lambda r: r.get("parish")),
            ("price_tier",      _price_tier),
        ]
    if persona_id == "rental_investor":
        return [
            ("yield_gross",     lambda r: _signal_band(r, "yield_gross", "flip_factors")),
            ("parish",          lambda r: r.get("parish")),
            ("price_tier",      _price_tier),
            ("size",            _size_bucket),
        ]
    if persona_id == "home_renter":
        return [
            ("parish",          lambda r: r.get("parish")),
            ("rent_tier",       _rent_tier),
            ("bedrooms",        _bedrooms_bucket),
            ("style",           lambda r: r.get("style_primary")),
            ("outdoor",         _outdoor_bucket),
        ]
    # Default: home_buyer (and unknown personas)
    return [
        ("parish",      lambda r: r.get("parish")),
        ("size",        _size_bucket),
        ("style",       lambda r: r.get("style_primary")),
        ("outdoor",     _outdoor_bucket),
        ("price_tier",  _price_tier),
    ]


def _table_for_persona(persona_id: Optional[str]) -> str:
    return PERSONA_VIEW.get(persona_id or "", "sales")


def _build_pool(
    table: str,
    *,
    city: Optional[str] = None,
    parish: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_sqm: Optional[float] = None,
    max_sqm: Optional[float] = None,
    pool_size: int = CANDIDATE_POOL,
) -> List[dict]:
    """Pull a candidate pool from SQLite. Live listings only, with coords +
    a primary image (so the deck card has something to render)."""
    clauses = [
        "status = 'active'",
        "lat IS NOT NULL AND lon IS NOT NULL",
        "images IS NOT NULL AND images != ''",
        "price_amount IS NOT NULL",
        "size_sqm IS NOT NULL",
    ]
    params: list = []
    if city:
        clauses.append("LOWER(city) = LOWER(?)")
        params.append(city)
    if parish:
        clauses.append("parish = ?"); params.append(parish)
    if min_price is not None:
        clauses.append("price_amount >= ?"); params.append(min_price)
    if max_price is not None:
        clauses.append("price_amount <= ?"); params.append(max_price)
    if min_sqm is not None:
        clauses.append("size_sqm >= ?"); params.append(min_sqm)
    if max_sqm is not None:
        clauses.append("size_sqm <= ?"); params.append(max_sqm)

    where = " AND ".join(clauses)
    sql = (
        f"SELECT {SELECT_COLS} FROM {table} "
        f"WHERE {where} "
        f"ORDER BY scraped_at DESC LIMIT ?"
    )
    params.append(pool_size)
    conn = db.get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _row_axis_bins(row: dict, axes) -> dict:
    return {name: fn(row) for name, fn in axes}


def _difference(a: dict, b: dict) -> int:
    """Count axes where two listings have different (non-None) bin values."""
    return sum(1 for k, v in a.items() if v is not None and b.get(k) is not None and b[k] != v)


def _first_image(images_field) -> Optional[str]:
    if not images_field:
        return None
    if isinstance(images_field, list) and images_field:
        return images_field[0]
    if isinstance(images_field, str):
        s = images_field.strip()
        if s.startswith("["):
            try:
                arr = json.loads(s)
                return arr[0] if arr else None
            except (json.JSONDecodeError, IndexError):
                return None
        if s.startswith("http"):
            # Comma-separated URLs.
            return s.split(",")[0].strip()
    return None


def _factor_positives(row: dict, persona_id: Optional[str]) -> dict:
    """Pull the per-signal positives bundle the persona will score on. Stored
    on each swipe so we can later derive weight deltas (signals that were high
    on liked listings get boosted, vice versa for dislikes)."""
    src_col = PERSONA_BUNDLE_SOURCE.get(persona_id or "", "flip_factors")
    raw = row.get(src_col)
    if not raw:
        return {}
    try:
        return (json.loads(raw).get("bundle") or {}).get("positives") or {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _shape_for_response(row: dict, bins: dict, listing_type: str, persona_id: Optional[str]) -> dict:
    """Return only what the deck card needs to render + log a swipe."""
    return {
        "id":             row["id"],
        "source":         row["source"],
        "source_id":      row["source_id"],
        "url":            row["url"],
        "listing_type":   listing_type,
        "price_amount":   row["price_amount"],
        "price_per_sqm":  row["price_per_sqm"],
        "size_sqm":       row["size_sqm"],
        "rooms":          row["rooms"],
        "bedrooms":       row["bedrooms"],
        "parish":         row["parish"],
        "neighborhood":   row["neighborhood"],
        "lat":            row["lat"],
        "lon":            row["lon"],
        "image_url":      _first_image(row.get("images")),
        "style_primary":  row.get("style_primary"),
        "outdoor_type":   row.get("outdoor_type"),
        "renovation_class": row.get("renovation_class"),
        "axis_bins":      {k: v for k, v in bins.items() if v is not None},
        "factor_positives": _factor_positives(row, persona_id),
    }


def build_deck(
    *,
    persona: Optional[str] = None,
    n: int = 12,
    city: Optional[str] = None,
    parish: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_sqm: Optional[float] = None,
    max_sqm: Optional[float] = None,
    seed: Optional[int] = None,
) -> List[dict]:
    """Build a stratified deck of `n` listings for the given persona.

    Algorithm:
      1. Pull CANDIDATE_POOL most-recent active listings matching hard filters.
      2. Compute per-row bins on the persona's axes.
      3. Bucket rows by (axis_a, axis_b) cells; pick from the most populated
         cells first to guarantee the cheapest information up front.
      4. Greedy fill: each new pick must differ from the previous on >= 2 axes.

    Returns at most `n` shaped dicts; fewer if the pool can't satisfy variety.
    """
    rng = random.Random(seed)
    table = _table_for_persona(persona)
    listing_type = "rent" if table == "rentals" else "sale"
    axes = _axes_for(persona)

    pool = _build_pool(
        table,
        city=city,
        parish=parish,
        min_price=min_price,
        max_price=max_price,
        min_sqm=min_sqm,
        max_sqm=max_sqm,
    )
    if not pool:
        return []

    # Pre-compute bins for every candidate.
    binned = [(row, _row_axis_bins(row, axes)) for row in pool]

    # Group by the first two axes (most discriminative pair) for stratification.
    cells: dict[tuple, list] = {}
    for row, bins in binned:
        key = (bins.get(axes[0][0]), bins.get(axes[1][0]))
        cells.setdefault(key, []).append((row, bins))

    # Shuffle inside each cell so we don't always get the same listing per cell.
    for items in cells.values():
        rng.shuffle(items)

    # Iterate cells round-robin, biggest first (those represent the most
    # common "shapes" of listings — show the user one of each early).
    cell_order = sorted(cells.keys(), key=lambda k: -len(cells[k]))

    picked: list = []
    picked_bins: list[dict] = []
    seen_ids: set[int] = set()

    while len(picked) < n:
        progressed = False
        for key in cell_order:
            if not cells[key]:
                continue
            row, bins = cells[key].pop()
            if row["id"] in seen_ids:
                progressed = True
                continue
            # Variety constraint vs the previous pick — skip if too similar.
            if picked_bins and _difference(bins, picked_bins[-1]) < 2:
                # Put it back at the bottom; try later.
                cells[key].insert(0, (row, bins))
                continue
            picked.append(_shape_for_response(row, bins, listing_type, persona))
            picked_bins.append(bins)
            seen_ids.add(row["id"])
            progressed = True
            if len(picked) >= n:
                break
        if not progressed:
            break  # pool exhausted

    # Fallback: if variety constraint left us short, top up with the best of
    # whatever remains, ignoring the constraint.
    if len(picked) < n:
        remaining = [
            (row, bins)
            for items in cells.values()
            for row, bins in items
            if row["id"] not in seen_ids
        ]
        rng.shuffle(remaining)
        for row, bins in remaining:
            if len(picked) >= n:
                break
            picked.append(_shape_for_response(row, bins, listing_type, persona))
            seen_ids.add(row["id"])

    return picked
