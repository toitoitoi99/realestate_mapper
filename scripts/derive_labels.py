#!/usr/bin/env python3
"""Derive flip labels from listing_history status transitions + price cuts.

Rationale: listing_history records real market outcomes exogenous to the
scorer. A sale that went through fast with no price cuts is a "good" deal
ex-post regardless of what flip_score predicted. A listing that sat for
months through multiple price cuts before being withdrawn is empirically
a "bad" deal. This lets gradient-weight-style variants train on real
outcomes without circularity.

Rules (tunable via CLI):
  good     status → 'sold' within FAST_DAYS (default 45) AND 0 price cuts
  bad      status → 'delisted' after SLOW_DAYS (default 180) with ≥2 price cuts
           OR status → 'delisted' with cumulative cut ≥ BIG_CUT_PCT (default 10%)
  neutral  everything else that transitioned to a terminal state

We do NOT derive rent labels. Rental outcome data (did it find a tenant?
at what rent?) is not in the DB — auto-deriving from price behavior alone
would re-use the same features rent_score consumes, which IS circular.
Rent labels must be entered manually via scripts/label_deal.py.

Output merges into data/labeled_deals.json, flip_labels array. Existing
MANUAL labels are never overwritten. Existing DERIVED labels for the same
(source, source_id) are refreshed.

Usage:
  python3 scripts/derive_labels.py                    # default thresholds
  python3 scripts/derive_labels.py --fast-days 30 --slow-days 150
  python3 scripts/derive_labels.py --dry-run          # print only
  python3 scripts/derive_labels.py --limit 500        # cap output
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "backend" / "data" / "lisboa_realestate.db"
LABELS = REPO / "data" / "labeled_deals.json"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _days_between(a: str | None, b: str | None) -> float | None:
    da, db = _parse_dt(a), _parse_dt(b)
    if not da or not db:
        return None
    return (db - da).total_seconds() / 86400.0


def _price_cut_pct(events: list[tuple]) -> float:
    """Sum of relative cuts (positive = cuts, caps) across price_amount events."""
    total = 0.0
    for _, old, new in events:
        try:
            o, n = float(old), float(new)
        except (TypeError, ValueError):
            continue
        if o > 0 and n < o:
            total += (o - n) / o
    return total


def _price_cut_count(events: list[tuple]) -> int:
    n = 0
    for _, old, new in events:
        try:
            if float(new) < float(old):
                n += 1
        except (TypeError, ValueError):
            pass
    return n


def derive(db: Path, fast_days: float, slow_days: float, big_cut_pct: float,
           limit: int | None = None) -> list[dict]:
    """Return a list of derived flip-label dicts."""
    if not db.exists():
        print(f"DB not found: {db}", file=sys.stderr)
        return []

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        # Only sale listings; listing_history stores listing_type='sale' for those.
        hist = conn.execute(
            "SELECT listing_id, source, source_id, field, old_value, new_value, changed_at "
            "FROM listing_history WHERE listing_type = 'sale' "
            "ORDER BY listing_id, changed_at"
        ).fetchall()
        sales_created = {
            (r["source"], str(r["source_id"])): r["created_at"]
            for r in conn.execute(
                "SELECT source, source_id, created_at FROM sales"
            ).fetchall()
        }

    # Group history by (source, source_id)
    grouped: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for r in hist:
        grouped[(r["source"], str(r["source_id"]))].append(r)

    out: list[dict] = []
    today = date.today().isoformat()

    for key, rows in grouped.items():
        status_events = [r for r in rows if r["field"] == "status"]
        price_events = [(r["changed_at"], r["old_value"], r["new_value"])
                        for r in rows if r["field"] == "price_amount"]
        if not status_events:
            continue

        # Find terminal transition (first sold or delisted)
        terminal = next(
            (r for r in status_events if r["new_value"] in ("sold", "delisted")),
            None,
        )
        if not terminal:
            continue

        listed_at = sales_created.get(key) or rows[0]["changed_at"]
        days = _days_between(listed_at, terminal["changed_at"])
        cuts_n = _price_cut_count(price_events)
        cuts_pct = _price_cut_pct(price_events)

        terminal_state = terminal["new_value"]
        label: str | None = None
        why = ""

        if terminal_state == "sold" and days is not None and days <= fast_days and cuts_n == 0:
            label = "good"
            why = f"sold in {days:.0f}d, 0 cuts"
        elif terminal_state == "delisted" and (
            (days is not None and days >= slow_days and cuts_n >= 2)
            or cuts_pct >= big_cut_pct
        ):
            label = "bad"
            why = (
                f"delisted after {days:.0f}d, {cuts_n} cuts, "
                f"cumulative -{cuts_pct*100:.0f}%"
            )
        elif terminal_state in ("sold", "delisted"):
            label = "neutral"
            why = f"{terminal_state} in {days:.0f}d, {cuts_n} cuts"

        if label is None:
            continue
        source, sid = key
        out.append({
            "source": source,
            "source_id": sid,
            "label": label,
            "source_of_label": "derived",
            "notes": why,
            "labeled_at": today,
        })
        if limit and len(out) >= limit:
            break

    return out


def merge(existing: dict, derived: list[dict]) -> tuple[dict, int, int]:
    """Insert/refresh derived rows in flip_labels, preserving manual ones.

    Returns (data, n_added, n_refreshed).
    """
    bucket = existing.setdefault("flip_labels", [])
    by_key = {(r.get("source"), str(r.get("source_id"))): i for i, r in enumerate(bucket)}
    added = refreshed = 0
    for row in derived:
        k = (row["source"], str(row["source_id"]))
        if k in by_key:
            current = bucket[by_key[k]]
            if current.get("source_of_label") == "manual":
                continue  # never overwrite a hand-curated label
            bucket[by_key[k]] = row
            refreshed += 1
        else:
            bucket.append(row)
            added += 1
    return existing, added, refreshed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast-days", type=float, default=45)
    ap.add_argument("--slow-days", type=float, default=180)
    ap.add_argument("--big-cut-pct", type=float, default=0.10,
                    help="Cumulative price cut fraction that flags a listing bad (0.10 = 10%%)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    derived = derive(DB, args.fast_days, args.slow_days, args.big_cut_pct, args.limit)
    tally: dict[str, int] = {}
    for r in derived:
        tally[r["label"]] = tally.get(r["label"], 0) + 1
    print(f"derived {len(derived)} flip labels: {tally}")

    if args.dry_run:
        for r in derived[:20]:
            print(f"  {r['source']}/{r['source_id']}  {r['label']:<7}  {r['notes']}")
        if len(derived) > 20:
            print(f"  ... ({len(derived)-20} more)")
        return

    existing = json.loads(LABELS.read_text()) if LABELS.exists() else {
        "flip_labels": [], "rent_labels": []
    }
    merged, added, refreshed = merge(existing, derived)
    LABELS.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {LABELS} — added {added}, refreshed {refreshed}, "
          f"total flip_labels: {len(merged['flip_labels'])}")


if __name__ == "__main__":
    main()
