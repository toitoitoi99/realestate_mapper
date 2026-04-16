"""Evaluation rubric for scoring experiments.

Three dimensions:
  1. Distribution shape: spread + A/B/C/D band share + skew/kurtosis.
  2. Label correlation: Spearman rho between variant score and numeric label
     from data/labeled_deals.json. Higher = better ordering of known deals.
  3. Neighborhood stability: median absolute deviation of per-neighborhood
     mean score, normalized by overall sd. Lower = more stable (less
     sensitive to which parish a listing happens to sit in).

Produces a single composite `rubric_score` in [0, 1] plus the component
numbers so the aggregator can rank variants reproducibly.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
from pathlib import Path
from typing import Iterable, Optional

from .labels import label_lookup, load_labels


# -- small stats utilities (no scipy dependency) ----------------------------

def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # 1-based average rank over ties
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 3 or len(a) != len(b):
        return None
    ra, rb = _rank(a), _rank(b)
    ma = sum(ra) / len(ra)
    mb = sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def _moments(xs: list[float]) -> dict:
    n = len(xs)
    if n == 0:
        return {"n": 0}
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n if n > 1 else 0.0
    sd = math.sqrt(var)
    if sd == 0:
        skew = 0.0
        kurt = 0.0
    else:
        skew = sum((x - mean) ** 3 for x in xs) / n / (sd ** 3)
        kurt = sum((x - mean) ** 4 for x in xs) / n / (sd ** 4) - 3
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "min": min(xs),
        "p25": _quantile(xs, 0.25),
        "p50": _quantile(xs, 0.50),
        "p75": _quantile(xs, 0.75),
        "max": max(xs),
        "skew": skew,
        "excess_kurtosis": kurt,
    }


def _quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    idx = (len(s) - 1) * q
    lo, hi = int(math.floor(idx)), int(math.ceil(idx))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _histogram(xs: list[float], bins: int = 20, lo: float = 0, hi: float = 100) -> list[int]:
    counts = [0] * bins
    width = (hi - lo) / bins
    for x in xs:
        if x is None:
            continue
        b = int((x - lo) / width)
        b = max(0, min(bins - 1, b))
        counts[b] += 1
    return counts


def ascii_histogram(xs: list[float], bins: int = 20, width: int = 40, lo: float = 0, hi: float = 100) -> str:
    counts = _histogram(xs, bins=bins, lo=lo, hi=hi)
    peak = max(counts) or 1
    out = []
    step = (hi - lo) / bins
    for i, c in enumerate(counts):
        bar = "█" * int(c / peak * width)
        out.append(f"{lo + i*step:5.1f}-{lo + (i+1)*step:5.1f} │ {bar} {c}")
    return "\n".join(out)


def band_shares(xs: list[float], cutoffs=(80, 65, 50)) -> dict:
    """A >= 80, B >= 65, C >= 50, D < 50 — matches production rating() bands."""
    a, b, c, d = cutoffs[0], cutoffs[1], cutoffs[2], 0
    counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    for x in xs:
        if x is None:
            continue
        if x >= a:
            counts["A"] += 1
        elif x >= b:
            counts["B"] += 1
        elif x >= c:
            counts["C"] += 1
        else:
            counts["D"] += 1
    n = sum(counts.values()) or 1
    return {k: v / n for k, v in counts.items()}


# -- rubric -----------------------------------------------------------------

def fetch_scores(db: Path, table: str, score_col: str, extra_cols: Iterable[str] = ()) -> list[dict]:
    cols = ["source", "source_id", "neighborhood", "parish", score_col, *extra_cols]
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} WHERE {score_col} IS NOT NULL"
        ).fetchall()
    return [dict(r) for r in rows]


def evaluate(
    db: Path,
    variant_score_col: str,
    production_score_col: str = "flip_score",
    labels_path: Optional[Path] = None,
    table: str = "sales",
    label_kind: Optional[str] = None,
) -> dict:
    """Run the full rubric on `table` in `db`. Returns a dict of metrics.

    `label_kind` is 'flip' for sales or 'rent' for rentals. If None, it's
    inferred from `table`. Passing a mismatched combination (e.g.
    table='sales' with label_kind='rent') raises — this is the guardrail
    that prevents rent outcomes from grading flip and vice versa.
    """
    inferred = "flip" if table == "sales" else "rent" if table == "rentals" else None
    if label_kind is None:
        label_kind = inferred
    if inferred and label_kind != inferred:
        raise ValueError(
            f"label_kind={label_kind!r} does not match table={table!r}. "
            f"Flip labels grade sales; rent labels grade rentals."
        )
    rows = fetch_scores(db, table, variant_score_col, extra_cols=[production_score_col])
    xs = [r[variant_score_col] for r in rows if r[variant_score_col] is not None]
    prod = [r[production_score_col] for r in rows if r[production_score_col] is not None]

    # Distribution shape
    dist = _moments(xs)
    bands = band_shares(xs)

    # Correlation with production (sanity: not too high — would mean no change;
    # not too low — would mean we broke the signal)
    prod_pairs = [(r[variant_score_col], r[production_score_col])
                  for r in rows
                  if r[variant_score_col] is not None and r[production_score_col] is not None]
    rho_prod = spearman([a for a, _ in prod_pairs], [b for _, b in prod_pairs]) if prod_pairs else None

    # Label correlation (disjoint by kind — rent labels never grade flip)
    rho_label = None
    label_n = 0
    if labels_path is not None and label_kind in ("flip", "rent"):
        lut = label_lookup(load_labels(Path(labels_path), label_kind))
        if lut:
            pairs = []
            for r in rows:
                key = (r["source"], str(r["source_id"]))
                if key in lut:
                    pairs.append((r[variant_score_col], lut[key]))
            label_n = len(pairs)
            if label_n >= 3:
                rho_label = spearman([a for a, _ in pairs], [b for _, b in pairs])

    # Neighborhood stability: MAD of per-neighborhood mean / overall sd
    by_hood: dict[str, list[float]] = {}
    for r in rows:
        h = r["neighborhood"] or r["parish"] or "?"
        by_hood.setdefault(h, []).append(r[variant_score_col])
    hood_means = [statistics.mean(v) for v in by_hood.values() if len(v) >= 5]
    overall_sd = dist.get("sd") or 1.0
    if len(hood_means) >= 3 and overall_sd:
        med = statistics.median(hood_means)
        mad = statistics.median([abs(m - med) for m in hood_means])
        stability = 1.0 - min(1.0, mad / overall_sd)
    else:
        stability = None

    # Composite (0..1). Missing pieces are skipped (renormalize weights).
    components = []
    # Distribution spread: want sd in [10, 25] (avoid collapse AND avoid
    # uniform-random look). Map sd -> score in [0, 1].
    sd = dist.get("sd") or 0
    spread_score = max(0.0, 1.0 - abs(sd - 17.5) / 17.5)
    components.append(("spread", spread_score, 0.2))
    # Production sanity band: rho_prod in [0.3, 0.9] is healthy.
    if rho_prod is not None:
        prod_sanity = max(0.0, 1.0 - max(0.0, abs(rho_prod - 0.6) - 0.3) / 0.4)
        components.append(("prod_sanity", prod_sanity, 0.15))
    if rho_label is not None:
        components.append(("label_corr", max(0.0, rho_label), 0.45))
    if stability is not None:
        components.append(("stability", stability, 0.2))

    total_w = sum(w for _, _, w in components) or 1.0
    rubric = sum(v * w for _, v, w in components) / total_w

    return {
        "table": table,
        "score_col": variant_score_col,
        "n": dist.get("n", 0),
        "distribution": dist,
        "band_shares": bands,
        "histogram_ascii": ascii_histogram(xs),
        "spearman_vs_production": rho_prod,
        "spearman_vs_labels": rho_label,
        "labeled_n": label_n,
        "neighborhood_stability": stability,
        "rubric_components": {name: val for name, val, _ in components},
        "rubric_score": rubric,
    }


def top_bottom(
    db: Path,
    variant_score_col: str,
    production_score_col: str = "flip_score",
    table: str = "sales",
    n: int = 20,
) -> dict:
    cols = [
        "source", "source_id", "url", "neighborhood", "parish",
        "price_amount", "size_sqm", "price_per_sqm", "rooms",
        variant_score_col, production_score_col,
    ]
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        top = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} "
            f"WHERE {variant_score_col} IS NOT NULL ORDER BY {variant_score_col} DESC LIMIT ?",
            (n,),
        ).fetchall()
        bot = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} "
            f"WHERE {variant_score_col} IS NOT NULL ORDER BY {variant_score_col} ASC LIMIT ?",
            (n,),
        ).fetchall()
    return {"top": [dict(r) for r in top], "bottom": [dict(r) for r in bot]}
