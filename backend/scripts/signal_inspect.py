"""
Inspect signals and scores from the CLI.

Four modes:
  1. Distribution: summary stats for a single signal across all scored listings
        python3 scripts/signal_inspect.py dist noise_score
  2. Top-N by a signal (or score)
        python3 scripts/signal_inspect.py top flip_score --n 20 --table sales
  3. Per-listing breakdown
        python3 scripts/signal_inspect.py show 12345
  4. Toggle preview: rescore one listing with signals disabled, no DB write
        python3 scripts/signal_inspect.py toggle 12345 --off noise,dev_momentum
        python3 scripts/signal_inspect.py toggle 12345 --off noise --off social_housing_adj

The toggle command does NOT mutate the DB. It loads the persisted
flip_factors JSON blob (which holds the original SignalBundle), drops the
requested signals, and re-rolls the score through scoring_engine.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database as db  # noqa: E402
from scoring_engine import SignalBundle, compute_both  # noqa: E402


SCORE_COLUMNS = {
    "flip_score", "rent_score",
    "noise_score", "light_score", "layout_openness_score",
    "social_housing_adj_score", "dev_momentum_score",
    "reno_cost_estimate",
}


def cmd_dist(args):
    col = args.signal
    conn = db.get_connection()
    vals = []
    for tbl in (args.table,) if args.table else ("sales", "rentals"):
        rows = conn.execute(
            f"SELECT {col} FROM {tbl} WHERE {col} IS NOT NULL"
        ).fetchall()
        vals.extend([r[col] for r in rows])
    print(f"Signal: {col}   samples: {len(vals)}")
    if not vals:
        print("  (no data — has signal_batch.py been run?)")
        return
    vals = sorted(vals)
    q = lambda p: vals[min(len(vals) - 1, int(p * len(vals)))]
    print(f"  min={vals[0]:.1f}  p25={q(0.25):.1f}  median={statistics.median(vals):.1f}  "
          f"p75={q(0.75):.1f}  p90={q(0.90):.1f}  max={vals[-1]:.1f}")
    print(f"  mean={statistics.mean(vals):.2f}  stdev={statistics.pstdev(vals):.2f}")
    # Histogram-ish bucketing by deciles
    buckets = [0] * 10
    for v in vals:
        idx = min(9, max(0, int(v / 10)))
        buckets[idx] += 1
    print("  distribution by bucket of 10:")
    for i, c in enumerate(buckets):
        bar = "█" * min(40, int(c / max(1, max(buckets)) * 40))
        print(f"    {i*10:>3}–{i*10+9:<3}  {c:>5}  {bar}")


def cmd_top(args):
    col = args.signal
    desc = "DESC" if args.order == "desc" else "ASC"
    conn = db.get_connection()
    tbl = args.table or "sales"
    extra = "" if col in {
        "id","url","price_amount","size_sqm","price_per_sqm","parish","city",
        "flip_score","rent_score","region_profile",
        "noise_score","social_housing_adj_score","dev_momentum_score",
        "layout_openness_score",
    } else f", {col}"
    q = f"""SELECT id, url, price_amount, size_sqm, price_per_sqm, parish, city,
                  flip_score, rent_score, region_profile,
                  noise_score, social_housing_adj_score, dev_momentum_score,
                  layout_openness_score{extra}
           FROM {tbl}
           WHERE {col} IS NOT NULL
           ORDER BY {col} {desc}
           LIMIT {int(args.n)}"""
    rows = conn.execute(q).fetchall()
    if not rows:
        print(f"No rows with {col} set in {tbl}.")
        return
    print(f"Top {args.n} {tbl} by {col} ({args.order}):")
    hdr = f'{"id":>6} {"flip":>5} {"rent":>5} {col:>14} {"noise":>5} {"soc":>5} {"parish":20} {"city":12} {"€":>10}'
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        fmt = lambda v: f"{v:.1f}" if isinstance(v, (int, float)) else "-"
        price = f"{r['price_amount']:,.0f}" if r['price_amount'] else "-"
        parish = (r['parish'] or '')[:20]
        city = (r['city'] or '')[:12]
        print(f"{r['id']:>6} {fmt(r['flip_score']):>5} {fmt(r['rent_score']):>5} "
              f"{fmt(r[col]):>14} "
              f"{fmt(r['noise_score']):>5} {fmt(r['social_housing_adj_score']):>5} "
              f"{parish:20} {city:12} {price:>10}")


def cmd_show(args):
    conn = db.get_connection()
    for tbl in ("sales", "rentals"):
        r = conn.execute(
            f"SELECT * FROM {tbl} WHERE id = ?", (args.id,),
        ).fetchone()
        if r:
            break
    else:
        print(f"listing id {args.id} not found in sales or rentals")
        return

    print(f"Listing {r['id']} ({tbl})")
    print(f"  url:     {r['url']}")
    print(f"  parish:  {r['parish']}   city: {r['city']}   district: {r['district']}")
    print(f"  price:   €{r['price_amount']:,.0f}   size: {r['size_sqm']} m²   psqm: €{r['price_per_sqm']}")
    print(f"  profile: {r['region_profile']}")
    print(f"  FLIP {r['flip_score']}     RENT {r['rent_score']}   reno_est: €{r['reno_cost_estimate'] or 0:,.0f}")
    print("  cached signals:")
    for col in ("noise_score", "social_housing_adj_score", "dev_momentum_score",
                "layout_openness_score", "light_score"):
        print(f"    {col:>28}: {r[col]}")

    if r["flip_factors"]:
        data = json.loads(r["flip_factors"])
        bundle = data.get("bundle", {})
        result = data.get("result", {})
        print("  FLIP breakdown:")
        for k, v in bundle.get("positives", {}).items():
            print(f"    +  {k:22} = {v}")
        for k, v in bundle.get("blockers", {}).items():
            print(f"    -  {k:22} = {v}")
        missing = result.get("missing_signals") or []
        if missing:
            print(f"  missing: {', '.join(missing)}")


def cmd_toggle(args):
    conn = db.get_connection()
    for tbl in ("sales", "rentals"):
        r = conn.execute(
            f"SELECT id, region_profile, flip_factors FROM {tbl} WHERE id = ?",
            (args.id,),
        ).fetchone()
        if r:
            break
    else:
        print(f"listing id {args.id} not found")
        return

    if not r["flip_factors"]:
        print("Listing has no flip_factors (never scored). Run signal_batch.py first.")
        return

    data = json.loads(r["flip_factors"])
    bundle_json = data.get("bundle") or {}
    positives = bundle_json.get("positives") or {}
    blockers = bundle_json.get("blockers") or {}
    bundle = SignalBundle(
        positives={k: (float(v) if v is not None else None) for k, v in positives.items()},
        blockers={k: (float(v) if v is not None else None) for k, v in blockers.items()},
    )
    profile = r["region_profile"] or "urban_dense"

    # Comma-split multiple --off values
    disabled = set()
    for item in (args.off or []):
        for part in item.split(","):
            p = part.strip()
            if p:
                disabled.add(p)

    base = compute_both(bundle, profile)
    toggled = compute_both(bundle, profile, disabled_signals=disabled)
    print(f"Listing {args.id}  profile={profile}")
    print(f"  Baseline:       FLIP {base['flip'].score:5.1f} ({base['flip'].rating})   "
          f"RENT {base['rent'].score:5.1f} ({base['rent'].rating})")
    if disabled:
        print(f"  Disabled:       {sorted(disabled)}")
        print(f"  After toggle:   FLIP {toggled['flip'].score:5.1f} ({toggled['flip'].rating})   "
              f"RENT {toggled['rent'].score:5.1f} ({toggled['rent'].rating})")
        dflip = toggled['flip'].score - base['flip'].score
        drent = toggled['rent'].score - base['rent'].score
        print(f"  Δ:              flip {dflip:+.1f}          rent {drent:+.1f}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dist = sub.add_parser("dist", help="Distribution of a signal")
    p_dist.add_argument("signal", help="column name (flip_score, noise_score, …)")
    p_dist.add_argument("--table", choices=["sales", "rentals"], default=None)
    p_dist.set_defaults(func=cmd_dist)

    p_top = sub.add_parser("top", help="Top-N listings by signal")
    p_top.add_argument("signal")
    p_top.add_argument("--n", type=int, default=10)
    p_top.add_argument("--order", choices=["asc", "desc"], default="desc")
    p_top.add_argument("--table", choices=["sales", "rentals"], default="sales")
    p_top.set_defaults(func=cmd_top)

    p_show = sub.add_parser("show", help="Detailed breakdown for a listing")
    p_show.add_argument("id", type=int)
    p_show.set_defaults(func=cmd_show)

    p_tog = sub.add_parser("toggle", help="Re-score with some signals disabled (no DB write)")
    p_tog.add_argument("id", type=int)
    p_tog.add_argument("--off", action="append", default=[],
                       help="Signal keys to disable (repeat or comma-separate)")
    p_tog.set_defaults(func=cmd_toggle)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
