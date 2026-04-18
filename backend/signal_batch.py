"""
Signal batch runner — compute all Phase 1 signals for every listing and
persist flip/rent scores to the sales/rentals tables.

This is the glue that ties pure signal functions to the DB. For each listing:
  1. Resolve region profile (parish/municipality → urban_dense/historic_tourist/…)
  2. Load cached geospatial rows in a bounding box around the listing
  3. Compute geometric signals (noise, social_housing_adj, dev_momentum,
     public_project_adj, layout_openness, dark_unit)
  4. Compute market-derived signals (market_discount, yield_gross,
     expected_resale) from comparables
  5. Bundle + roll up via scoring_engine → persist flip_score, rent_score,
     breakdowns, and individual cached signal columns

Run:
    python3 signal_batch.py                    # all listings
    python3 signal_batch.py --listing-type sales --limit 50
    python3 signal_batch.py --listing-id 12345
    python3 signal_batch.py --only-stale       # new listings + price/status changes since last score
    python3 signal_batch.py --reno-cost-per-sqm 1800  # slider override
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import sys
from datetime import datetime, timezone
from typing import Optional, List

import database as db
import signals
from signals_text import has_light_hint
from region_profiles import resolve_profile, estimate_reno_cost
from scoring_engine import (
    SignalBundle,
    compute_both,
    compute_market_discount_signal,
    compute_expected_resale_signal,
    compute_yield_gross_signal,
    log1p_scale,
    LOG1P_SIGNALS,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


_EARTH_R = 6371000.0


def _bbox_degrees(lat: float, lon: float, radius_m: float):
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(0.1, math.cos(math.radians(lat))))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def _parse_vision(raw) -> dict:
    """Parse sales.photo_analysis JSON blob; return {} on missing/bad data."""
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        if isinstance(v, dict):
            return v
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


# Map the renovation_classifier.py (PR #85) output into scoring-engine signals.
# renovation_class → structural_red_flags blocker (vision-derived condition)
# Heavy-severity needs in the `needs` list bump the blocker further.
_RENO_CLASS_TO_STRUCT_BLOCKER = {
    "turnkey":         8.0,
    "cosmetic":       25.0,
    "full_renovation": 75.0,
}
# Layout-openness positive signal: a gutted shell is maximally flexible;
# a pristine unit gives no signal (use building_year fallback).
_RENO_CLASS_TO_LAYOUT = {
    "turnkey":        None,   # no info — let building_year rule
    "cosmetic":       None,
    "full_renovation": 90.0,
}


def _renovation_signals(row: dict) -> dict:
    """Extract flip/rent signal values from renovation_classifier columns.

    Fallback: if no vision-derived renovation_class exists but the description
    flagged a building_stage via text extraction, synthesize an equivalent
    class (so the signal math still runs).
    """
    out = {
        "structural_red_flags": None,
        "layout_openness":      None,
        "condition_tier":       None,
        "reno_cost_per_sqm":    None,
        "renovation_class":     None,
    }
    cls = row.get("renovation_class")
    stage = row.get("building_stage")
    if not cls and stage:
        # Map text-derived building_stage → synthetic renovation_class for the
        # downstream math. approved_project implies ground-up, not just a gut
        # reno — we mark it explicitly and the reno-cost lookup picks the
        # ground_up tier below.
        cls = {
            "approved_project": "full_renovation",
            "full_remodel":     "full_renovation",
            "needs_reno":       "full_renovation",
            "turnkey":          "turnkey",
        }.get(stage)
    if not cls:
        return out
    out["renovation_class"] = cls
    # Base blocker from class
    base = _RENO_CLASS_TO_STRUCT_BLOCKER.get(cls)
    if base is None:
        return out
    # Bump for each "full"-severity need (e.g. electrical=full adds ~5)
    try:
        needs = json.loads(row.get("renovation_needs") or "[]")
    except (json.JSONDecodeError, TypeError):
        needs = []
    full_count = sum(1 for n in needs if isinstance(n, dict) and n.get("severity") == "full")
    bumped = min(100.0, base + full_count * 5.0)

    conf = row.get("renovation_confidence") or 0.0
    # Dampen low-confidence signals toward neutral 20.
    if conf < 0.7 and cls != "turnkey":
        bumped = 20.0 + (bumped - 20.0) * (conf / 0.7)

    out["structural_red_flags"] = round(bumped, 1)
    out["layout_openness"] = _RENO_CLASS_TO_LAYOUT.get(cls)
    # For reno_cost_estimate, map the class to our tier labels so
    # region_profiles.estimate_reno_cost picks the right default. An
    # approved-project listing is ground-up, not just a gut renovation —
    # that gets the dedicated "ground_up" tier.
    if stage == "approved_project":
        out["condition_tier"] = "ground_up"
    else:
        out["condition_tier"] = {
            "turnkey":         "cosmetic",   # light refresh only
            "cosmetic":        "mid",
            "full_renovation": "gut",
        }[cls]
    # Use the vision model's €/m² estimate directly if present (PR 85 output).
    per_sqm = row.get("renovation_cost_estimate_eur_per_sqm")
    if per_sqm and per_sqm > 0:
        out["reno_cost_per_sqm"] = float(per_sqm)
    return out


# ---------------------------------------------------------------------------
# Cached-row loaders (single-listing scope)
# ---------------------------------------------------------------------------

def load_noise_sources_near(conn, lat, lon, radius_m=500.0):
    s_lat, n_lat, w_lon, e_lon = _bbox_degrees(lat, lon, radius_m)
    rows = conn.execute(
        """SELECT source, kind, geometry_geojson
           FROM noise_sources
           WHERE rowid IN (
             SELECT rowid FROM noise_sources
             WHERE 1=1
           )""",
    ).fetchall()
    # Filter to those with at least one vertex in the bbox (cheap coarse filter)
    out = []
    for r in rows:
        try:
            geo = json.loads(r["geometry_geojson"])
        except (json.JSONDecodeError, TypeError):
            continue
        coords = geo.get("coordinates") or []
        if any(w_lon <= pt[0] <= e_lon and s_lat <= pt[1] <= n_lat for pt in coords):
            out.append({"kind": r["kind"], "coords": coords})
    return out


def load_social_housing_near(conn, lat, lon, radius_m=600.0):
    s_lat, n_lat, w_lon, e_lon = _bbox_degrees(lat, lon, radius_m)
    rows = conn.execute(
        """SELECT name, category, lat, lon FROM social_housing
           WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?""",
        (s_lat, n_lat, w_lon, e_lon),
    ).fetchall()
    return [dict(r) for r in rows]


def load_projects_near(conn, lat, lon, radius_m=400.0):
    s_lat, n_lat, w_lon, e_lon = _bbox_degrees(lat, lon, radius_m)
    rows = conn.execute(
        """SELECT operation, subject, classification,
                  centroid_lat AS lat, centroid_lon AS lon,
                  date_permit, date_submitted
           FROM construction_projects
           WHERE centroid_lat BETWEEN ? AND ?
             AND centroid_lon BETWEEN ? AND ?""",
        (s_lat, n_lat, w_lon, e_lon),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # signals.py looks for `date` first
        d["date"] = d.get("date_permit") or d.get("date_submitted")
        # If classification column populated use it; else classify on the fly
        d["classification"] = d.get("classification") or signals.classify_project(d)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Comparables for market_discount / yield / resale
# ---------------------------------------------------------------------------

def _within(conn, table, lat, lon, radius_m, property_type=None, limit=200):
    s_lat, n_lat, w_lon, e_lon = _bbox_degrees(lat, lon, radius_m)
    params = [s_lat, n_lat, w_lon, e_lon]
    pt_clause = ""
    if property_type:
        pt_clause = " AND property_type = ?"
        params.append(property_type)
    q = (f"""SELECT price_amount, size_sqm, price_per_sqm, property_type
             FROM {table}
             WHERE lat BETWEEN ? AND ?
               AND lon BETWEEN ? AND ?
               AND price_per_sqm IS NOT NULL
               AND size_sqm > 0 {pt_clause}
             LIMIT {int(limit)}""")
    return conn.execute(q, params).fetchall()


def comparable_median_psqm(conn, lat, lon, property_type, table="sales", radius_m=500.0):
    rows = _within(conn, table, lat, lon, radius_m, property_type=property_type)
    if len(rows) < 5:
        rows = _within(conn, table, lat, lon, radius_m, property_type=None)
    if not rows:
        return None, 0
    vals = [r["price_per_sqm"] for r in rows if r["price_per_sqm"]]
    if not vals:
        return None, len(rows)
    return statistics.median(vals), len(rows)


def comparable_median_rent_psqm(conn, lat, lon, property_type, radius_m=500.0):
    """Returns monthly rent per m². rentals.price_amount is a monthly rent."""
    rows = _within(conn, "rentals", lat, lon, radius_m, property_type=property_type)
    if len(rows) < 5:
        rows = _within(conn, "rentals", lat, lon, radius_m, property_type=None)
    if not rows:
        return None
    psqm = [r["price_per_sqm"] for r in rows if r["price_per_sqm"]]
    return statistics.median(psqm) if psqm else None


def expected_post_reno_psqm(conn, lat, lon, property_type, radius_m=500.0):
    """Estimate what a renovated unit sells for — proxied by the 75th percentile
    of nearby sales. Renovated units are the top of the local distribution.
    """
    rows = _within(conn, "sales", lat, lon, radius_m, property_type=property_type)
    if len(rows) < 5:
        rows = _within(conn, "sales", lat, lon, radius_m, property_type=None)
    vals = sorted([r["price_per_sqm"] for r in rows if r["price_per_sqm"]])
    if len(vals) < 3:
        return None
    # 75th percentile using linear interpolation
    idx = 0.75 * (len(vals) - 1)
    lo = int(idx); frac = idx - lo
    if lo + 1 < len(vals):
        return vals[lo] + frac * (vals[lo + 1] - vals[lo])
    return vals[lo]


# ---------------------------------------------------------------------------
# Per-listing scoring
# ---------------------------------------------------------------------------

def score_one(
    conn,
    listing_type: str,
    row: dict,
    reno_cost_per_sqm: Optional[float] = None,
    reno_tier_override: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Score a single listing row. Writes back to the DB. Returns a summary."""
    now = now or datetime.now(timezone.utc)

    lat = row.get("lat"); lon = row.get("lon")
    listing = {
        "lat": lat, "lon": lon,
        "floor": row.get("floor"),
        "building_year": row.get("building_year"),
        "orientation": row.get("orientation"),
        # has_light_hint is cheap; re-run on description to avoid adding a column
        "light_hint": has_light_hint(row.get("description") or ""),
    }

    profile = resolve_profile(
        parish=row.get("parish"),
        municipality=row.get("city"),
        district=row.get("district"),
    )

    # Geometric signals
    if lat is not None and lon is not None:
        ns_rows  = load_noise_sources_near(conn, lat, lon, radius_m=500)
        sh_rows  = load_social_housing_near(conn, lat, lon, radius_m=600)
        pr_rows  = load_projects_near(conn, lat, lon, radius_m=400)
    else:
        ns_rows = sh_rows = pr_rows = []

    noise      = signals.noise_score(listing, ns_rows) if lat is not None else None
    social_adj = signals.social_housing_adj_score(listing, sh_rows) if lat is not None else None
    dev_mom    = signals.dev_momentum_score(listing, pr_rows, asof=now) if lat is not None else None
    pub_adj    = signals.public_project_adj_score(listing, pr_rows, asof=now) if lat is not None else None
    layout     = signals.layout_openness_score(listing)   # returns None if no building_year
    dark       = signals.dark_unit_score(listing, [])     # neighbour buildings TODO (Phase 3)
    light_text = signals.light_score(listing)             # orientation + floor + hint

    # Phase 3 — renovation classifier (PR #85) + legacy photo_analysis blob.
    reno = _renovation_signals(row)
    vision_data = _parse_vision(row.get("photo_analysis"))  # legacy/optional
    vis_light   = vision_data.get("light")
    vis_outdoor = vision_data.get("outdoor_space")
    vis_layout  = vision_data.get("layout_openness")

    # Structural red flags come from the renovation classifier primarily;
    # fall back to legacy photo_analysis.structural_red_flags if present.
    vis_struct = reno["structural_red_flags"] if reno["structural_red_flags"] is not None \
                 else vision_data.get("structural_red_flags")

    # Light: prefer vision when available, otherwise text.
    if vis_light is not None and light_text is not None:
        light = 0.6 * vis_light + 0.4 * light_text
    else:
        light = vis_light if vis_light is not None else light_text

    # Layout: renovation signal (gutted → flexible) beats vision beats building year.
    if reno["layout_openness"] is not None:
        layout = reno["layout_openness"]
    elif vis_layout is not None:
        layout = vis_layout

    # Market-derived signals
    table_sales = "sales"
    prop_type = row.get("property_type")
    median_psqm, comp_n = comparable_median_psqm(
        conn, lat, lon, prop_type, table=table_sales,
    ) if lat is not None else (None, 0)
    rent_psqm = comparable_median_rent_psqm(
        conn, lat, lon, prop_type,
    ) if lat is not None else None
    post_reno_psqm = expected_post_reno_psqm(
        conn, lat, lon, prop_type,
    ) if lat is not None else None

    # Vision classifier's own €/m² estimate takes precedence over the text
    # heuristic, unless caller explicitly overrode via CLI/slider.
    effective_reno_per_sqm = reno_cost_per_sqm
    effective_reno_tier = reno_tier_override
    if effective_reno_per_sqm is None and reno["reno_cost_per_sqm"] is not None:
        effective_reno_per_sqm = reno["reno_cost_per_sqm"]
    if effective_reno_tier is None and effective_reno_per_sqm is None:
        effective_reno_tier = reno["condition_tier"]

    reno_cost = estimate_reno_cost(
        size_sqm=row.get("size_sqm"),
        condition=row.get("condition"),
        profile_name=profile,
        tier_override=effective_reno_tier,
        cost_per_sqm_override=effective_reno_per_sqm,
    )

    # Gate market-derived signals on comparable count. With fewer than
    # MIN_COMPARABLES comps the parish median (and especially the p75 used
    # by expected_resale) are too noisy to distinguish a real discount
    # from sampling variance, so we treat the signals as missing and let
    # the roll-up renormalize rather than letting a noisy ratio dominate
    # the weighted average.
    MIN_COMPARABLES = 10
    comps_reliable = comp_n >= MIN_COMPARABLES

    market_disc = compute_market_discount_signal(
        row.get("price_amount"), row.get("size_sqm"), median_psqm,
    ) if comps_reliable else None

    yield_signal = compute_yield_gross_signal(
        row.get("price_amount"), row.get("size_sqm"), rent_psqm,
    )  # yield uses rent-side comps; gate separately if/when we track that.

    # Condition-aware expected resale: turnkey → market-growth only (no reno uplift);
    # cosmetic → modest bump; full_renovation → p75. See scoring_engine.py.
    # Use the synthesized class from _renovation_signals (combines vision-derived
    # renovation_class + text-derived building_stage).
    resale_signal = compute_expected_resale_signal(
        price=row.get("price_amount"),
        size_sqm=row.get("size_sqm"),
        parish_median_psqm=median_psqm,
        parish_p75_psqm=post_reno_psqm,
        reno_cost_estimate=reno_cost,
        renovation_class=reno["renovation_class"] or row.get("renovation_class"),
    ) if comps_reliable else None

    # Amenity score (reuse cached amenity_ratings if present; else leave None)
    amenity = None
    if lat is not None and lon is not None:
        cached = conn.execute(
            """SELECT overall_score FROM amenity_ratings
               WHERE lat_key = ROUND(?, 3) AND lon_key = ROUND(?, 3)
               LIMIT 1""", (lat, lon),
        ).fetchone()
        if cached:
            amenity = cached["overall_score"]

    # Build raw positives, then apply concave log1p scaling to
    # fat-tailed signals before the roll-up. See scoring_engine.LOG1P_SIGNALS.
    raw_positives = {
        "market_discount":   market_disc,
        "amenity":           amenity,
        "transit":           None,   # Phase 2
        "light":             light,             # vision + orientation + floor + hint
        "outdoor_space":     vis_outdoor,       # vision only for now
        "outdoor_space":     None,   # Phase 2/3
        "layout_openness":   layout,
        "dev_momentum":      dev_mom,
        "sea_view":          None,   # Phase 2
        "demand_durability": None,   # Phase 2
        "expected_resale":   resale_signal,
        "yield_gross":       yield_signal,
    }
    positives = {
        k: (log1p_scale(v) if k in LOG1P_SIGNALS else v)
        for k, v in raw_positives.items()
    }

    bundle = SignalBundle(
        positives=positives,
        blockers={
            "noise":                noise,
            "social_housing_adj":   social_adj,
            "public_project_adj":   pub_adj,
            "heritage_restriction": None,   # Phase 2 (parish flag)
            "dark_unit":            dark,
            "structural_red_flags": vis_struct,   # vision blocker
            # Construction/execution risk fires when the listing is sold with
            # planning approved but not built, or requires full reconstruction.
            "construction_risk": (
                80.0 if row.get("building_stage") == "approved_project"
                else 40.0 if row.get("building_stage") == "full_remodel"
                else 20.0 if row.get("building_stage") == "needs_reno"
                else None
            ),
        },
        comparables_count=comp_n,
        market_median_eur_sqm=median_psqm,
        expected_resale_eur_sqm=post_reno_psqm,
        expected_rent_eur_sqm=rent_psqm,
        notes={
            "profile": profile,
            "comparables": f"{comp_n} within 500m",
        },
    )

    results = compute_both(bundle, profile)
    flip = results["flip"]; rent = results["rent"]

    # Persist. (We write even if most positives are None — partial data is
    # useful and the engine flags it via `missing_signals`.)
    conn.execute(
        f"""UPDATE {listing_type}
            SET flip_score = ?, flip_factors = ?,
                rent_score = ?, rent_factors = ?,
                region_profile = ?, reno_cost_estimate = ?,
                noise_score = ?, light_score = ?,
                layout_openness_score = ?, social_housing_adj_score = ?,
                dev_momentum_score = ?,
                score_computed_at = ?
            WHERE id = ?""",
        (
            flip.score,
            json.dumps({"result": flip.to_dict(),
                        "bundle": {"positives": bundle.positives,
                                   "blockers": bundle.blockers,
                                   "notes": bundle.notes,
                                   "comparables_count": bundle.comparables_count,
                                   "market_median_eur_sqm": bundle.market_median_eur_sqm,
                                   "expected_resale_eur_sqm": bundle.expected_resale_eur_sqm,
                                   "expected_rent_eur_sqm": bundle.expected_rent_eur_sqm,
                                   }},
                       default=str),
            rent.score,
            json.dumps({"result": rent.to_dict(),
                        "bundle": {"positives": bundle.positives,
                                   "blockers": bundle.blockers}},
                       default=str),
            profile,
            reno_cost,
            noise,
            light,
            layout,
            social_adj,
            dev_mom,
            now.isoformat(),
            row["id"],
        ),
    )

    return {
        "id": row["id"],
        "profile": profile,
        "flip": flip.score,
        "rent": rent.score,
        "comparables": comp_n,
        "missing": len(flip.missing_signals),
    }


def run_batch(
    listing_type: Optional[str] = None,
    listing_id: Optional[int] = None,
    limit: Optional[int] = None,
    reno_cost_per_sqm: Optional[float] = None,
    reno_tier_override: Optional[str] = None,
    only_stale: bool = False,
):
    db.init_db()
    conn = db.get_connection()
    tables = [listing_type] if listing_type else ["sales", "rentals"]
    now = datetime.now(timezone.utc)

    for tbl in tables:
        score_col = "flip_score" if tbl == "sales" else "rent_score"
        history_lt = "sale" if tbl == "sales" else "rent"
        q = f"""SELECT id, lat, lon, price_amount, size_sqm, price_per_sqm,
                       property_type, condition, floor, building_year,
                       orientation, description, photo_analysis,
                       renovation_class, renovation_confidence,
                       renovation_cost_estimate_eur_per_sqm,
                       renovation_needs, building_stage,
                       parish, city, district
                FROM {tbl}
                WHERE lat IS NOT NULL AND lon IS NOT NULL"""
        params = []
        if listing_id:
            q += " AND id = ?"; params.append(listing_id)
        if only_stale and not listing_id:
            # Re-score if never scored, or if price/status changed since the
            # last score timestamp.
            q += f"""
                AND (
                    {score_col} IS NULL
                    OR score_computed_at IS NULL
                    OR EXISTS (
                        SELECT 1 FROM listing_history h
                        WHERE h.listing_id = {tbl}.id
                          AND h.listing_type = ?
                          AND h.field IN ('price_amount', 'status')
                          AND h.changed_at > {tbl}.score_computed_at
                    )
                )"""
            params.append(history_lt)
        if limit:
            q += f" LIMIT {int(limit)}"
        rows = conn.execute(q, params).fetchall()
        logger.info(f"[{tbl}] scoring {len(rows)} listings")

        n_done = 0
        for r in rows:
            try:
                score_one(
                    conn, tbl, dict(r),
                    reno_cost_per_sqm=reno_cost_per_sqm,
                    reno_tier_override=reno_tier_override,
                    now=now,
                )
            except Exception as e:
                logger.warning(f"[{tbl}] id={r['id']} failed: {e}")
                continue
            n_done += 1
            if n_done % 200 == 0:
                conn.commit()
                logger.info(f"[{tbl}]   {n_done}/{len(rows)}")
        conn.commit()
        logger.info(f"[{tbl}] done: {n_done}/{len(rows)}")

    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listing-type", choices=["sales", "rentals"], default=None)
    parser.add_argument("--listing-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--reno-cost-per-sqm", type=float, default=None,
                        help="Override reno cost €/m² (UI slider equivalent)")
    parser.add_argument("--reno-tier", choices=["cosmetic", "mid", "gut"], default=None)
    parser.add_argument("--only-stale", action="store_true",
                        help="Score only listings that are unscored or whose "
                             "price/status changed since the last score.")
    args = parser.parse_args()
    run_batch(
        listing_type=args.listing_type,
        listing_id=args.listing_id,
        limit=args.limit,
        reno_cost_per_sqm=args.reno_cost_per_sqm,
        reno_tier_override=args.reno_tier,
        only_stale=args.only_stale,
    )


if __name__ == "__main__":
    main()
