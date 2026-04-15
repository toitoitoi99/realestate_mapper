"""
Self-contained signal tests. Run: python3 test_signals.py
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone

from signals import (
    haversine_m,
    noise_score,
    social_housing_adj_score,
    dev_momentum_score,
    public_project_adj_score,
    layout_openness_score,
    dark_unit_score,
    classify_project,
    PROJECT_CLASS_PRIVATE_DEV,
    PROJECT_CLASS_PUBLIC_DEV,
    PROJECT_CLASS_RENOVATION,
    PROJECT_CLASS_MINOR,
)


_failures: list = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    if not cond:
        _failures.append(name)
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


# ----- haversine sanity -------------------------------------------------------
print("# haversine")
# Lisbon latitude: 1 degree lon ~= 86.7 km, 1 degree lat ~= 111 km
d_1deg_lon = haversine_m(38.7, -9.0, 38.7, -8.0)
check("1deg lon at Lisbon ≈ 86.8 km", 86000 < d_1deg_lon < 88000, f"got {d_1deg_lon:.0f} m")
d_100m_lat = haversine_m(38.7000, -9.0, 38.7009, -9.0)   # ~100 m north
check("~100 m north is ~100 m", 95 < d_100m_lat < 105, f"got {d_100m_lat:.1f} m")


# ----- noise ------------------------------------------------------------------
print("# noise")
# A motorway 40 m south of a listing
listing = {"lat": 38.7000, "lon": -9.1500}
motorway_south = {
    "kind": "motorway",
    "coords": [
        [-9.1510, 38.69964],   # 40m south, slightly west
        [-9.1490, 38.69964],   # 40m south, slightly east
    ],
}
n = noise_score(listing, [motorway_south])
check("motorway @ 40m → high blocker", n is not None and n > 75, f"noise={n}")

# Same motorway 300 m away
motorway_far = {"kind": "motorway", "coords": [[-9.1510, 38.697], [-9.1490, 38.697]]}
n_far = noise_score(listing, [motorway_far])
check("motorway @ 300m → small blocker", n_far is not None and n_far < 30, f"noise={n_far}")

# A residential street 20 m away → near zero
residential = {"kind": "residential", "coords": [[-9.1502, 38.69982], [-9.1498, 38.69982]]}
n_r = noise_score(listing, [residential])
check("residential street @ 20m → negligible", n_r is not None and n_r < 10, f"noise={n_r}")

# No sources → 0
check("no sources → 0", noise_score(listing, []) == 0.0)

# Missing coords → None
check("missing lat → None", noise_score({}, [motorway_south]) is None)


# ----- social housing adjacency ----------------------------------------------
print("# social_housing_adj")
estate_close = {"lat": 38.70000, "lon": -9.14991}   # ~80m east
s_close = social_housing_adj_score(listing, [estate_close])
check("estate @ ~80m → moderate blocker", s_close and s_close > 50, f"score={s_close}")

estate_far = {"lat": 38.70500, "lon": -9.1500}   # ~555m north
s_far = social_housing_adj_score(listing, [estate_far])
check("estate @ 555m → 0 (out of range)", s_far == 0.0, f"score={s_far}")

estate_adjacent = {"lat": 38.70000, "lon": -9.15000}   # 0m
s_adj = social_housing_adj_score(listing, [estate_adjacent])
check("estate @ 0m → ~100", s_adj and s_adj > 95, f"score={s_adj}")


# ----- project classifier -----------------------------------------------------
print("# classify_project")
check("new construction → private_dev",
      classify_project({"operation": "Construção Nova de Edifício"}) == PROJECT_CLASS_PRIVATE_DEV)
check("municipal social → public_dev",
      classify_project({"operation": "Construção Nova", "description": "habitação social IHRU"}) == PROJECT_CLASS_PUBLIC_DEV)
check("renovation → renovation",
      classify_project({"operation": "Alteração"}) == PROJECT_CLASS_RENOVATION)
check("signage → minor",
      classify_project({"operation": "Publicidade"}) == PROJECT_CLASS_MINOR)
check("unknown → minor (conservative)",
      classify_project({"operation": "Qualquer coisa"}) == PROJECT_CLASS_MINOR)


# ----- dev momentum -----------------------------------------------------------
print("# dev_momentum")
asof = datetime(2026, 4, 1, tzinfo=timezone.utc)
recent = {"operation": "Construção Nova", "lat": 38.70030, "lon": -9.1500,
          "date": "2025-06-01"}      # 100m north, 10 months ago
old    = {"operation": "Construção Nova", "lat": 38.70030, "lon": -9.1500,
          "date": "2019-01-01"}      # too old
far    = {"operation": "Construção Nova", "lat": 38.71000, "lon": -9.1500,
          "date": "2025-06-01"}      # ~1.1 km away
minor  = {"operation": "Ocupação via pública", "lat": 38.70010, "lon": -9.15000,
          "date": "2025-06-01"}      # close but minor

check("0 recent → 30 baseline",
      dev_momentum_score(listing, [old, far, minor], asof=asof) == 30.0)
check("1 recent → 60",
      dev_momentum_score(listing, [recent, old, far, minor], asof=asof) == 60.0)
check("3 recent → 80",
      dev_momentum_score(listing, [recent, recent, recent], asof=asof) == 80.0)


# ----- public project adjacency ----------------------------------------------
print("# public_project_adj")
pub1 = {"operation": "Construção Nova", "description": "habitação municipal IHRU",
        "lat": 38.70020, "lon": -9.1500, "date": "2024-03-01"}
pub2 = {"operation": "Construção Nova", "description": "cooperativa de habitação",
        "lat": 38.70015, "lon": -9.1500, "date": "2024-03-01"}

check("0 public → 0", public_project_adj_score(listing, [], asof=asof) == 0.0)
check("1 public → 40", public_project_adj_score(listing, [pub1], asof=asof) == 40.0)
check("2 public → 65", public_project_adj_score(listing, [pub1, pub2], asof=asof) == 65.0)


# ----- layout openness --------------------------------------------------------
print("# layout_openness")
check("1900 → 20", layout_openness_score({}, building_year=1900) == 20.0)
check("1960 → 40", layout_openness_score({}, building_year=1960) == 40.0)
check("1985 → 70", layout_openness_score({}, building_year=1985) == 70.0)
check("2015 → 85", layout_openness_score({}, building_year=2015) == 85.0)
check("missing → None", layout_openness_score({}, building_year=None) is None)
check("reads from listing", layout_openness_score({"building_year": 2010}) == 85.0)


# ----- dark unit --------------------------------------------------------------
print("# dark_unit")
# Ground floor with tall neighbours → heavy blocker
ground_floor = {"lat": 38.7, "lon": -9.15, "floor": 0}
neigh_tall   = {"lat": 38.7, "lon": -9.14985, "height_m": 24}   # ~13m east, 8 storeys
neigh_tall2  = {"lat": 38.7, "lon": -9.15015, "height_m": 21}   # ~13m west, 7 storeys
score = dark_unit_score(ground_floor, [neigh_tall, neigh_tall2])
check("ground floor + 2 tall neighbours → >=70",
      score is not None and score >= 70, f"score={score}")

# Same floor, no neighbours → just floor baseline
check("ground floor alone → 40",
      dark_unit_score(ground_floor, []) == 40.0)

# Penthouse → 0 regardless of neighbours
penthouse = {"lat": 38.7, "lon": -9.15, "floor": 7}
check("penthouse alone → 0",
      dark_unit_score(penthouse, []) == 0.0)

# Floor unknown → None
check("floor unknown → None",
      dark_unit_score({"lat": 38.7, "lon": -9.15}, []) is None)


print()
if _failures:
    print(f"FAILURES: {len(_failures)}")
    for f in _failures:
        print(f"  - {f}")
    sys.exit(1)
print("All signal tests passed.")
