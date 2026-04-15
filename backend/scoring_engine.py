"""
Flip/Rent scoring engine.

Shared signal pipeline, two weighted roll-ups. One listing → one score each
for flip potential and rental potential, plus a breakdown of inputs.

Design notes:
  * All signals are normalized to 0..100 (higher = better for that listing).
  * Blockers are separate 0..100 penalty signals (higher = worse).
  * The scoring formula is:
        score = 0.5 * weighted_positive + 0.5 * (100 - weighted_blocker)
    — i.e. positive signals and blocker-freeness contribute equally.
    Rationale: a listing with great signals AND crippling blockers should not
    score A; conversely, a clean listing with only OK positives should not score D.
  * Weights per profile need not sum to 1; we normalize inside the roll-up.
  * Signals that cannot be computed (e.g. no photos yet) are simply absent
    from the weighted average — the remaining weights renormalize. This keeps
    the engine resilient while data coverage grows.

All the heavy signal computation lives in separate modules (signals_geo.py,
signals_text.py, signals_vision.py) added in later phases. This file is the
roll-up layer: it takes pre-computed signals in, produces scores out.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, Any

from region_profiles import (
    get_profile,
    resolve_profile,
    estimate_reno_cost,
    SIGNAL_KEYS,
    BLOCKER_KEYS,
)

logger = logging.getLogger(__name__)


# -- Public dataclasses --------------------------------------------------------

@dataclass
class SignalBundle:
    """All the inputs the scoring engine needs about one listing.

    Positive signals in `positives` and blockers in `blockers` are 0..100
    floats (None if not computable). Keep this a plain dict-of-floats on the
    wire so it serializes cleanly to JSON.
    """
    positives: Dict[str, Optional[float]] = field(default_factory=dict)
    blockers: Dict[str, Optional[float]] = field(default_factory=dict)

    # Context used to render "why" text, not used in math.
    comparables_count: int = 0
    market_median_eur_sqm: Optional[float] = None
    expected_resale_eur_sqm: Optional[float] = None
    expected_rent_eur_sqm: Optional[float] = None
    notes: Dict[str, str] = field(default_factory=dict)


@dataclass
class ScoreResult:
    score: float                     # 0..100
    rating: str                      # A/B/C/D
    positive_contributions: Dict[str, float]  # signal → (weight * score)
    blocker_contributions: Dict[str, float]   # blocker → (weight * penalty)
    weights_used: Dict[str, float]
    blocker_weights_used: Dict[str, float]
    profile: str
    missing_signals: list

    def to_dict(self) -> dict:
        return asdict(self)


# -- Helpers -------------------------------------------------------------------

def _rating(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def _clip01to100(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    if x != x:  # NaN
        return None
    return max(0.0, min(100.0, float(x)))


def _weighted_average(
    values: Dict[str, Optional[float]],
    weights: Dict[str, float],
) -> tuple[float, float, Dict[str, float], list]:
    """Weighted average, skipping signals that are None.

    Returns (score, total_weight, per_signal_contributions, missing_signals).
    When every signal is missing, returns (50.0, 0.0, {}, missing_list) —
    neutral score keeps downstream math sane.
    """
    contributions: Dict[str, float] = {}
    missing: list = []
    total_w = 0.0
    weighted_sum = 0.0

    for key, w in weights.items():
        v = _clip01to100(values.get(key))
        if v is None:
            missing.append(key)
            continue
        contributions[key] = w * v
        weighted_sum += w * v
        total_w += w

    if total_w == 0:
        return 50.0, 0.0, contributions, missing
    return weighted_sum / total_w, total_w, contributions, missing


# -- Value, yield, expected-resale helpers -------------------------------------
# These are "derived" positive signals — they depend on the listing's price and
# external comparables rather than on geographic/text/vision inputs. They live
# here (not in a signals module) because they are cheap and depend on the
# result of comparables queries which the caller already performs.

def compute_market_discount_signal(
    price: Optional[float],
    size_sqm: Optional[float],
    comparable_median_psqm: Optional[float],
) -> Optional[float]:
    """Discount vs local comparable median, mapped to 0..100.

    0% discount → 50; 30% discount → 100; 30% premium → 0. Linear in between.
    """
    if not price or not size_sqm or size_sqm <= 0 or not comparable_median_psqm:
        return None
    listing_psqm = price / size_sqm
    discount = (comparable_median_psqm - listing_psqm) / comparable_median_psqm
    return max(0.0, min(100.0, 50.0 + discount * (50.0 / 0.30)))


def compute_expected_resale_signal(
    price: Optional[float],
    size_sqm: Optional[float],
    parish_median_psqm: Optional[float],
    parish_p75_psqm: Optional[float],
    reno_cost_estimate: Optional[float],
    renovation_class: Optional[str] = None,
    transaction_cost_pct: float = 0.10,
    appreciation_pct: float = 0.04,
) -> Optional[float]:
    """Expected margin on a flip, mapped to 0..100.

    Maps condition → realistic post-hold sale target:
      turnkey          → current listing price × (1 + appreciation). No reno uplift.
      cosmetic         → median + 35% of (p75 − median).
      full_renovation  → p75 (aspirational; model worth top-tier prices after gut).
      unknown          → conservative: median + 25% of (p75 − median).

    margin% = (target_value − price − reno_cost − txn_costs) / price.
    −10% → 0, +30% → 100, linear. Negative margins saturate at 0.
    """
    if not price or not size_sqm or size_sqm <= 0 or not parish_median_psqm:
        return None

    p75 = parish_p75_psqm or parish_median_psqm * 1.15   # conservative fallback
    current_psqm = price / size_sqm

    if renovation_class == "turnkey":
        # Already at its ceiling. Only market appreciation contributes.
        target_psqm = current_psqm * (1.0 + appreciation_pct)
    elif renovation_class == "cosmetic":
        target_psqm = parish_median_psqm + 0.35 * (p75 - parish_median_psqm)
    elif renovation_class == "full_renovation":
        target_psqm = p75
    else:
        # No classifier data — blend conservatively toward the middle.
        target_psqm = parish_median_psqm + 0.25 * (p75 - parish_median_psqm)

    target_value = target_psqm * size_sqm
    txn = transaction_cost_pct * price
    reno = reno_cost_estimate or 0.0
    margin = (target_value - price - reno - txn) / price
    if margin <= -0.10:
        return 0.0
    if margin >= 0.30:
        return 100.0
    return (margin + 0.10) / 0.40 * 100.0


def compute_yield_gross_signal(
    price: Optional[float],
    size_sqm: Optional[float],
    monthly_rent_estimate_psqm: Optional[float],
) -> Optional[float]:
    """Gross annual yield, mapped to 0..100.

    2% → 0, 4% → 50, 7%+ → 100. Lisbon resident rentals are typically 3–5%,
    coastal STRs can exceed 7% so the top of the scale reflects that ceiling.
    """
    if not price or not size_sqm or size_sqm <= 0 or not monthly_rent_estimate_psqm:
        return None
    annual_rent = monthly_rent_estimate_psqm * size_sqm * 12.0
    yield_pct = (annual_rent / price) * 100.0
    if yield_pct <= 2.0:
        return 0.0
    if yield_pct >= 7.0:
        return 100.0
    return (yield_pct - 2.0) / 5.0 * 100.0


# -- Roll-up -------------------------------------------------------------------

def compute_score(
    bundle: SignalBundle,
    profile_name: str,
    mode: str,
    disabled_signals: Optional[set] = None,
) -> ScoreResult:
    """Roll up a SignalBundle into a flip or rent score.

    mode: 'flip' or 'rent'
    disabled_signals: iterable of signal keys (positive or blocker) to ignore
        during roll-up. Disabled signals are treated as missing, so remaining
        weights renormalize. Used by the review UI/CLI to toggle signals off
        and see the effect on the final score without recomputing upstream
        data.
    """
    assert mode in ("flip", "rent"), "mode must be 'flip' or 'rent'"
    profile = get_profile(profile_name)
    disabled = set(disabled_signals or ())

    weights = profile["flip_weights"] if mode == "flip" else profile["rent_weights"]
    blocker_weights = (
        profile["blocker_weights_flip"] if mode == "flip"
        else profile["blocker_weights_rent"]
    )

    positives = {k: (None if k in disabled else v) for k, v in bundle.positives.items()}
    blockers = {k: (None if k in disabled else v) for k, v in bundle.blockers.items()}

    pos_score, _, pos_contribs, pos_missing = _weighted_average(
        positives, weights,
    )
    blk_score, _, blk_contribs, blk_missing = _weighted_average(
        blockers, blocker_weights,
    )
    # Blocker signals are penalties: higher blocker_score = WORSE listing.
    # Convert to "blocker-freeness" before averaging with positives.
    blocker_freeness = 100.0 - blk_score

    final = 0.5 * pos_score + 0.5 * blocker_freeness

    return ScoreResult(
        score=round(final, 1),
        rating=_rating(final),
        positive_contributions={k: round(v, 2) for k, v in pos_contribs.items()},
        blocker_contributions={k: round(v, 2) for k, v in blk_contribs.items()},
        weights_used={k: round(v, 3) for k, v in weights.items()},
        blocker_weights_used={k: round(v, 3) for k, v in blocker_weights.items()},
        profile=profile_name,
        missing_signals=sorted(set(pos_missing) | set(blk_missing)),
    )


def compute_both(
    bundle: SignalBundle,
    profile_name: str,
    disabled_signals: Optional[set] = None,
) -> Dict[str, ScoreResult]:
    """Convenience: compute both flip and rent in one call."""
    return {
        "flip": compute_score(bundle, profile_name, "flip", disabled_signals),
        "rent": compute_score(bundle, profile_name, "rent", disabled_signals),
    }


# -- DB write-through ---------------------------------------------------------

def persist_scores(
    conn,
    listing_type: str,     # 'sales' or 'rentals'
    listing_id: int,
    bundle: SignalBundle,
    flip: ScoreResult,
    rent: ScoreResult,
    reno_cost_estimate: Optional[float] = None,
    profile_name: Optional[str] = None,
) -> None:
    """Write scoring outputs back to the listing row.

    `conn` is expected to be a sqlite3 connection (same one the API uses).
    The caller is responsible for commit().
    """
    assert listing_type in ("sales", "rentals")

    factors = {
        "bundle": {
            "positives": bundle.positives,
            "blockers": bundle.blockers,
            "comparables_count": bundle.comparables_count,
            "market_median_eur_sqm": bundle.market_median_eur_sqm,
            "expected_resale_eur_sqm": bundle.expected_resale_eur_sqm,
            "expected_rent_eur_sqm": bundle.expected_rent_eur_sqm,
            "notes": bundle.notes,
        },
    }
    flip_factors = dict(factors, result=flip.to_dict())
    rent_factors = dict(factors, result=rent.to_dict())

    conn.execute(
        f"""UPDATE {listing_type}
            SET flip_score = ?,
                flip_factors = ?,
                rent_score = ?,
                rent_factors = ?,
                region_profile = COALESCE(?, region_profile),
                reno_cost_estimate = COALESCE(?, reno_cost_estimate),
                noise_score = COALESCE(?, noise_score),
                light_score = COALESCE(?, light_score),
                layout_openness_score = COALESCE(?, layout_openness_score),
                social_housing_adj_score = COALESCE(?, social_housing_adj_score),
                dev_momentum_score = COALESCE(?, dev_momentum_score)
            WHERE id = ?""",
        (
            flip.score,
            json.dumps(flip_factors, default=str),
            rent.score,
            json.dumps(rent_factors, default=str),
            profile_name,
            reno_cost_estimate,
            bundle.positives.get("_cached_noise"),   # aliases below
            bundle.positives.get("light"),
            bundle.positives.get("layout_openness"),
            100.0 - bundle.blockers["social_housing_adj"]
                if bundle.blockers.get("social_housing_adj") is not None else None,
            bundle.positives.get("dev_momentum"),
            listing_id,
        ),
    )


__all__ = [
    "SignalBundle",
    "ScoreResult",
    "compute_score",
    "compute_both",
    "compute_market_discount_signal",
    "compute_expected_resale_signal",
    "compute_yield_gross_signal",
    "persist_scores",
    # re-exports for callers
    "resolve_profile",
    "estimate_reno_cost",
    "SIGNAL_KEYS",
    "BLOCKER_KEYS",
]
