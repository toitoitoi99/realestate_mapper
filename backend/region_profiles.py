"""
Region profiles for the flip/rent scoring engine.

A profile bundles:
  * weight vectors for the flip and rent scores
  * blocker multipliers (how harshly a given negative signal counts)
  * default renovation cost tier (overridable per-session via UI slider)

Rationale: the *signal inventory* is shared across regions, but the *weighting*
is not. Noise is a large penalty in dense urban Lisbon; barely matters in
Algarve where a main road is how tourists reach the rental. Sea-view weighs
almost nothing in Arroios; it dominates in Albufeira.

Keep this file data-only so tuning never requires touching scoring code.
"""
from __future__ import annotations

from typing import Dict, Optional
import unicodedata


# -- Signal keys ---------------------------------------------------------------
# Centralized so that typos in profile dicts fail fast in tests.
SIGNAL_KEYS = {
    # Positive-direction signals (higher = better)
    "market_discount",       # price vs comparable median
    "amenity",               # OSM-based livability
    "transit",               # dedicated transit subscore
    "light",                 # floor + orientation + vision brightness
    "outdoor_space",         # balcony / terrace, size-weighted
    "layout_openness",       # vision + building age
    "dev_momentum",          # new *private* construction within 300m (past 4y)
    "sea_view",              # coastal regions only; 0 elsewhere
    "demand_durability",     # universities/hospitals/offices for rent stability
    "yield_gross",           # rent/price ratio (rent score only)
    "expected_resale",       # post-reno resale ceiling (flip score only)
}

BLOCKER_KEYS = {
    "noise",                 # OSM road class + rail + airport corridor
    "social_housing_adj",    # proximity to municipal housing
    "public_project_adj",    # proximity to social/infra projects (as opposed to private dev)
    "heritage_restriction",  # parish-level conservation zone penalty
    "dark_unit",             # low floor + tall neighbours + poor orientation
    "structural_red_flags",  # vision/text indicators of deep structural problems
    "construction_risk",     # approved-project or not-yet-built — build cost/timeline uncertainty
}


# -- Profile definitions -------------------------------------------------------
# Weights do NOT need to sum to any particular value; the scoring engine
# normalizes per signal profile. Comments describe the intent, not a contract.

URBAN_DENSE = {
    "name": "urban_dense",
    "description": "Typical resident-oriented city parish — Lisbon, Porto centro.",
    "flip_weights": {
        # The flip thesis is margin-driven. expected_resale captures price vs
        # realistic post-hold value net of reno and txn; market_discount alone
        # is just "cheap" which may just mean "correctly priced for condition".
        "market_discount":    0.15,
        "amenity":            0.10,
        "transit":            0.05,
        "light":              0.10,
        "outdoor_space":      0.10,
        "layout_openness":    0.10,
        "dev_momentum":       0.15,
        "sea_view":           0.00,
        "demand_durability":  0.00,
        "expected_resale":    0.25,   # was 0.10 — now the primary flip signal
        "yield_gross":        0.00,
    },
    "rent_weights": {
        "yield_gross":        0.35,
        "amenity":            0.15,
        "transit":            0.15,
        "demand_durability":  0.15,
        "light":              0.05,
        "outdoor_space":      0.05,
        "layout_openness":    0.00,
        "dev_momentum":       0.05,
        "market_discount":    0.05,
        "sea_view":           0.00,
        "expected_resale":    0.00,
    },
    "blocker_weights_flip": {
        "noise":                 0.30,
        "social_housing_adj":    0.25,
        "public_project_adj":    0.10,
        "heritage_restriction":  0.15,
        "dark_unit":             0.10,
        "structural_red_flags":  0.10,
        "construction_risk":     0.20,   # approved-project / not-yet-built penalty
    },
    "blocker_weights_rent": {
        "noise":                 0.15,  # tenants tolerate noise more than buyers
        "social_housing_adj":    0.10,
        "public_project_adj":    0.05,
        "heritage_restriction":  0.05,
        "dark_unit":             0.30,  # dark rental is a vacancy driver
        "structural_red_flags":  0.35,
        "construction_risk":     0.05,   # mostly irrelevant for renters
    },
    "default_reno_tier": "mid",
    "reno_cost_multiplier": 1.00,   # Lisbon baseline
}

HISTORIC_TOURIST = {
    "name": "historic_tourist",
    "description": "Historic/tourist core — Alfama, Baixa, Chiado, Bairro Alto, Porto Ribeira.",
    "flip_weights": {
        "market_discount":    0.10,
        "amenity":            0.10,
        "transit":            0.05,
        "light":              0.15,   # matters more because many units are dark
        "outdoor_space":      0.15,
        "layout_openness":    0.05,
        "dev_momentum":       0.10,
        "sea_view":           0.00,   # handled separately if present
        "demand_durability":  0.00,
        "expected_resale":    0.30,   # was 0.15 — primary flip signal
        "yield_gross":        0.00,
    },
    "rent_weights": {
        "yield_gross":        0.40,
        "amenity":            0.10,
        "transit":            0.10,
        "demand_durability":  0.10,
        "light":              0.10,
        "outdoor_space":      0.10,
        "layout_openness":    0.00,
        "dev_momentum":       0.05,
        "market_discount":    0.05,
        "sea_view":           0.00,
        "expected_resale":    0.00,
    },
    "blocker_weights_flip": {
        "noise":                 0.20,
        "social_housing_adj":    0.15,
        "public_project_adj":    0.05,
        "heritage_restriction":  0.30,   # heritage rules dominate reno cost
        "dark_unit":             0.15,
        "structural_red_flags":  0.15,
        "construction_risk":     0.20,
    },
    "blocker_weights_rent": {
        "noise":                 0.10,
        "social_housing_adj":    0.05,
        "public_project_adj":    0.05,
        "heritage_restriction":  0.10,
        "dark_unit":             0.35,
        "structural_red_flags":  0.35,
        "construction_risk":     0.05,
    },
    "default_reno_tier": "mid",
    "reno_cost_multiplier": 1.35,   # heritage permits + artisan trades
}

COASTAL_RESORT = {
    "name": "coastal_resort",
    "description": "Coastal holiday rental market — Algarve, Cascais, Sesimbra.",
    "flip_weights": {
        "market_discount":    0.10,
        "amenity":            0.05,
        "transit":            0.00,   # near-irrelevant
        "light":              0.10,
        "outdoor_space":      0.15,
        "layout_openness":    0.05,
        "dev_momentum":       0.05,
        "sea_view":           0.25,   # dominant
        "demand_durability":  0.00,
        "expected_resale":    0.25,   # was 0.10
        "yield_gross":        0.00,
    },
    "rent_weights": {
        "yield_gross":        0.45,   # seasonal yield dominates
        "amenity":            0.05,
        "transit":            0.00,
        "demand_durability":  0.05,
        "light":              0.10,
        "outdoor_space":      0.15,
        "layout_openness":    0.00,
        "dev_momentum":       0.00,
        "market_discount":    0.05,
        "sea_view":           0.15,
        "expected_resale":    0.00,
    },
    "blocker_weights_flip": {
        "noise":                 0.10,   # beach/tourism noise tolerated
        "social_housing_adj":    0.15,
        "public_project_adj":    0.10,
        "heritage_restriction":  0.10,
        "dark_unit":             0.20,
        "structural_red_flags":  0.35,   # salt air → hidden damage matters
        "construction_risk":     0.20,
    },
    "blocker_weights_rent": {
        "noise":                 0.05,
        "social_housing_adj":    0.05,
        "public_project_adj":    0.05,
        "heritage_restriction":  0.05,
        "dark_unit":             0.40,
        "structural_red_flags":  0.40,
        "construction_risk":     0.05,
    },
    "default_reno_tier": "mid",
    "reno_cost_multiplier": 1.10,   # slightly higher than Lisbon baseline
}

SUBURBAN = {
    "name": "suburban",
    "description": "Outer AML / Margem Sul commuter belt.",
    "flip_weights": {
        "market_discount":    0.15,
        "amenity":            0.10,
        "transit":            0.15,   # commute matters
        "light":              0.10,
        "outdoor_space":      0.10,
        "layout_openness":    0.05,
        "dev_momentum":       0.10,
        "sea_view":           0.00,
        "demand_durability":  0.00,
        "expected_resale":    0.25,
        "yield_gross":        0.00,
    },
    "rent_weights": {
        "yield_gross":        0.40,
        "amenity":            0.10,
        "transit":            0.20,
        "demand_durability":  0.15,
        "light":              0.05,
        "outdoor_space":      0.05,
        "layout_openness":    0.00,
        "dev_momentum":       0.00,
        "market_discount":    0.05,
        "sea_view":           0.00,
        "expected_resale":    0.00,
    },
    "blocker_weights_flip": {
        # Note: highway proximity is NOT penalized the same way — the suburban
        # noise signal is re-scored in signals.noise() using this profile.
        "noise":                 0.15,
        "social_housing_adj":    0.25,
        "public_project_adj":    0.15,
        "heritage_restriction":  0.05,
        "dark_unit":             0.15,
        "structural_red_flags":  0.25,
        "construction_risk":     0.20,
    },
    "blocker_weights_rent": {
        "noise":                 0.10,
        "social_housing_adj":    0.15,
        "public_project_adj":    0.10,
        "heritage_restriction":  0.05,
        "dark_unit":             0.25,
        "structural_red_flags":  0.35,
        "construction_risk":     0.05,
    },
    "default_reno_tier": "mid",
    "reno_cost_multiplier": 0.90,   # cheaper trades outside the city
}


PROFILES: Dict[str, dict] = {
    p["name"]: p for p in (URBAN_DENSE, HISTORIC_TOURIST, COASTAL_RESORT, SUBURBAN)
}


# -- Parish / municipality → profile mapping ----------------------------------
# Lookup order when resolving a profile for a listing:
#   1. Lisbon parish (exact match, normalized)
#   2. Municipality-level mapping (covers AML + Algarve + anywhere else)
#   3. DEFAULT_PROFILE

# Lisbon parishes that behave as historic/tourist first, resident second.
# Santo António is borderline but stays urban_dense (more resident than touristy).
LISBON_HISTORIC_PARISHES = {
    "Santa Maria Maior",   # Baixa + Alfama
    "São Vicente",         # Alfama / Graça
    "Misericórdia",        # Chiado + Bairro Alto + Cais do Sodré
}

# Lisbon-resident parishes are "urban_dense" (the default for Lisbon).
# We do NOT enumerate them — the municipality fallback handles them.

# AML municipalities. Lisboa handled above by parish.
AML_COASTAL = {"Cascais", "Sesimbra"}
# Oeiras is coast-adjacent but behaves like an affluent suburb; keep suburban.
AML_SUBURBAN = {
    "Alcochete", "Almada", "Amadora", "Barreiro", "Loures", "Mafra",
    "Moita", "Montijo", "Odivelas", "Oeiras", "Palmela", "Seixal",
    "Setúbal", "Sintra", "Vila Franca de Xira",
}

# Algarve — default all coastal_resort; the inland few get suburban.
ALGARVE_INLAND = {"Alcoutim", "São Brás de Alportel"}
# Everything else in Algarve is coastal_resort.
ALGARVE_ALL = {
    "Albufeira", "Alcoutim", "Aljezur", "Castro Marim", "Faro", "Lagoa",
    "Lagos", "Loulé", "Olhão", "Portimão", "Silves", "São Brás de Alportel",
    "Tavira", "Vila Real de Santo António", "Vila do Bispo",
}

# Porto municipality (city core) — treat like urban_dense for now. The Ribeira
# historic zone would ideally be its own overlay but parishes aren't yet mapped.
PORTO_URBAN = {"Porto"}


DEFAULT_PROFILE = "urban_dense"


def _normalize(s: Optional[str]) -> str:
    if not s:
        return ""
    # Strip accents and lowercase for fuzzy comparison.
    nfkd = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.strip().lower()


_LISBON_HISTORIC_NORM = {_normalize(p) for p in LISBON_HISTORIC_PARISHES}
_AML_COASTAL_NORM = {_normalize(m) for m in AML_COASTAL}
_AML_SUBURBAN_NORM = {_normalize(m) for m in AML_SUBURBAN}
_ALGARVE_ALL_NORM = {_normalize(m) for m in ALGARVE_ALL}
_ALGARVE_INLAND_NORM = {_normalize(m) for m in ALGARVE_INLAND}
_PORTO_URBAN_NORM = {_normalize(m) for m in PORTO_URBAN}


def resolve_profile(
    parish: Optional[str] = None,
    municipality: Optional[str] = None,
    district: Optional[str] = None,
) -> str:
    """Resolve a (parish, municipality, district) tuple to a profile name.

    Returns one of the keys in PROFILES. Never raises — falls back to
    DEFAULT_PROFILE if nothing matches, which keeps the scorer resilient to
    dirty geocoding.
    """
    parish_n = _normalize(parish)
    muni_n = _normalize(municipality)
    district_n = _normalize(district)

    # 1. Lisbon parish check
    if parish_n in _LISBON_HISTORIC_NORM:
        return "historic_tourist"
    if muni_n == "lisboa" or parish_n and district_n == "lisboa":
        return "urban_dense"

    # 2. AML municipalities
    if muni_n in _AML_COASTAL_NORM:
        return "coastal_resort"
    if muni_n in _AML_SUBURBAN_NORM:
        return "suburban"

    # 3. Algarve
    if muni_n in _ALGARVE_INLAND_NORM:
        return "suburban"
    if muni_n in _ALGARVE_ALL_NORM or district_n == "faro":
        return "coastal_resort"

    # 4. Porto
    if muni_n in _PORTO_URBAN_NORM or district_n == "porto":
        return "urban_dense"

    return DEFAULT_PROFILE


def get_profile(name: str) -> dict:
    """Look up a profile by name, falling back to the default."""
    return PROFILES.get(name) or PROFILES[DEFAULT_PROFILE]


# -- Reno-cost defaults (€/m², parameterizable via UI slider) ------------------
# Tiers describe the *scope* of work, applied to listing `size_sqm`. The
# engine uses condition + vision output to pick a tier, then multiplies by
# `reno_cost_multiplier` from the profile.
RENO_COST_TIERS = {
    "cosmetic":  800.0,   # paint, floors, light kitchen/bath refresh
    "mid":      1500.0,   # full kitchen + baths, electrical refresh, finishes
    "gut":      2500.0,   # rip-to-shell, full rewire/replumb, layout changes
    "ground_up": 2200.0,  # approved-project / empty shell / new construction
                          # (Lisbon new-build realistic: 2000–2800/m² depending
                          # on finish level; 2200 is a reasonable midpoint.)
}


def estimate_reno_cost(
    size_sqm: Optional[float],
    condition: Optional[str],
    profile_name: str,
    tier_override: Optional[str] = None,
    cost_per_sqm_override: Optional[float] = None,
) -> Optional[float]:
    """Return a € estimate of renovation cost.

    Order of precedence:
      1. `cost_per_sqm_override` (straight €/m² from the UI slider)
      2. `tier_override` ("cosmetic"/"mid"/"gut")
      3. Condition-derived tier (new→0, used→mid, used_for_refurb→gut)
    """
    if not size_sqm or size_sqm <= 0:
        return None

    profile = get_profile(profile_name)
    multiplier = profile.get("reno_cost_multiplier", 1.0)

    if cost_per_sqm_override is not None:
        return float(size_sqm) * float(cost_per_sqm_override) * multiplier

    if tier_override and tier_override in RENO_COST_TIERS:
        base = RENO_COST_TIERS[tier_override]
        return float(size_sqm) * base * multiplier

    # Condition → tier
    cond = (condition or "").lower()
    if cond in ("new", "novo"):
        return 0.0
    if "refurb" in cond or "ruína" in cond or "needs_work" in cond:
        tier = "gut"
    elif cond in ("used", "usado", "good", "bom"):
        tier = "mid"
    else:
        tier = profile.get("default_reno_tier", "mid")

    base = RENO_COST_TIERS[tier]
    return float(size_sqm) * base * multiplier


__all__ = [
    "PROFILES",
    "SIGNAL_KEYS",
    "BLOCKER_KEYS",
    "RENO_COST_TIERS",
    "DEFAULT_PROFILE",
    "resolve_profile",
    "get_profile",
    "estimate_reno_cost",
]
