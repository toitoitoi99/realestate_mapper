"""
Persona-aware scoring on top of existing flip/rent signals.

Personas are soft lenses: each maps to a weighted blend of the per-signal
positives already computed by scoring_engine.py and stored on each listing
inside `flip_factors.bundle.positives` / `rent_factors.bundle.positives`.

Keep this in lockstep with frontend/src/lib/personas.js (same ids + weights).
"""
from __future__ import annotations

import json
from typing import Optional

# Weight configs — mirror frontend/src/lib/personas.js exactly.
PERSONA_WEIGHTS: dict[str, dict[str, float]] = {
    "rental_investor": {
        "yield_gross": 0.5,
        "demand_durability": 0.2,
        "market_discount": 0.2,
        "amenity": 0.1,
    },
    "flipper": {
        "market_discount": 0.5,
        "expected_resale": 0.3,
        "dev_momentum": 0.2,
    },
    "home_buyer": {
        "amenity": 0.4,
        "light": 0.2,
        "outdoor_space": 0.2,
        "demand_durability": 0.2,
    },
    "home_renter": {
        "amenity": 0.4,
        "transit": 0.3,
        "light": 0.15,
        "outdoor_space": 0.15,
    },
}

# For home_renter we read the rent_factors bundle; everyone else uses flip_factors.
PERSONA_BUNDLE_SOURCE: dict[str, str] = {
    "rental_investor": "flip_factors",
    "flipper": "flip_factors",
    "home_buyer": "flip_factors",
    "home_renter": "rent_factors",
}


def is_known_persona(persona_id: Optional[str]) -> bool:
    return persona_id in PERSONA_WEIGHTS


def compute_persona_score(
    row: dict,
    persona_id: str,
    weights_override: Optional[dict] = None,
) -> Optional[float]:
    """Compute a 0-100 persona score for a listing row.

    Reads positives from the row's flip_factors / rent_factors JSON blob,
    re-normalises the weights over the signals that actually have a value
    (so listings missing a signal aren't penalised below the others).

    `weights_override` lets the caller pass a swipe-derived weight vector
    (computed in frontend/src/lib/swipeWeights.js) so the same scoring path
    can express "your prior + your demonstrated preferences." Falls back to
    PERSONA_WEIGHTS when not provided.

    Returns None if no relevant signals are present.
    """
    weights = weights_override or PERSONA_WEIGHTS.get(persona_id)
    if not weights:
        return None

    src_col = PERSONA_BUNDLE_SOURCE.get(persona_id, "flip_factors")
    raw = row.get(src_col)
    if not raw:
        return None
    try:
        bundle = json.loads(raw).get("bundle") or {}
    except (json.JSONDecodeError, TypeError):
        return None
    positives = bundle.get("positives") or {}

    weighted_sum = 0.0
    weight_total = 0.0
    for signal, w in weights.items():
        v = positives.get(signal)
        if v is None:
            continue
        weighted_sum += float(v) * w
        weight_total += w

    if weight_total == 0.0:
        return None
    return round(weighted_sum / weight_total, 1)
