#!/usr/bin/env python3
"""Sanity-check derived flip labels before trusting them for training.

Samples N derived labels per class (good / neutral / bad), pulls the full
listing row + history timeline + current flip_score from the DB, and
prints a compact card for each so you can eyeball whether the derivation
rules produced sensible labels.

Two modes:

  non-interactive (default) — just print the audit report:
    python3 scripts/audit_labels.py
    python3 scripts/audit_labels.py -n 20 --only bad

  interactive — for each sampled listing, keep / override / discard:
    python3 scripts/audit_labels.py --interactive
      k  keep as-is
      g  override to good (promotes to manual — won't be re-derived)
      n  override to neutral (manual)
      b  override to bad (manual)
      d  discard this label entirely (remove row)
      s  skip (leave, don't record anything)
      q  quit, save changes so far

Only audits derived labels by default — manual labels already have human
attestation. Use --include-manual to audit those too.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "backend" / "data" / "lisboa_realestate.db"
LABELS = REPO / "data" / "labeled_deals.json"


def _load_labels() -> dict:
    if not LABELS.exists():
        return {"flip_labels": [], "rent_labels": []}
    return json.loads(LABELS.read_text())


def _save_labels(data: dict) -> None:
    LABELS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _fetch_listing(conn: sqlite3.Connection, source: str, source_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, source, source_id, url, status, price_amount, price_per_sqm, "
        "size_sqm, rooms, neighborhood, parish, flip_score, created_at "
        "FROM sales WHERE source = ? AND source_id = ?",
        (source, str(source_id)),
    ).fetchone()
    return dict(row) if row else None


def _fetch_history(conn: sqlite3.Connection, source: str, source_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT field, old_value, new_value, changed_at FROM listing_history "
        "WHERE listing_type = 'sale' AND source = ? AND source_id = ? "
        "ORDER BY changed_at",
        (source, str(source_id)),
    ).fetchall()
    return [dict(r) for r in rows]


def _fmt_money(x) -> str:
    try:
        return f"€{float(x):,.0f}"
    except (TypeError, ValueError):
        return str(x)


def _print_card(i: int, total: int, label_row: dict, listing: dict | None,
                history: list[dict]) -> None:
    src = label_row.get("source")
    sid = label_row.get("source_id")
    label = label_row.get("label")
    notes = label_row.get("notes", "")
    lbltype = label_row.get("source_of_label", "?")

    header = f"[{i}/{total}]  {src}/{sid}  label={label!r:<9}  ({lbltype})"
    print("\n" + "═" * 78)
    print(header)
    print(f"derivation rule: {notes}")

    if not listing:
        print("  ⚠ listing row not found in sales — stale label?")
        return

    print(f"  url:       {listing.get('url')}")
    print(f"  location:  {listing.get('parish') or '?'} / {listing.get('neighborhood') or '?'}")
    print(
        f"  listing:   {_fmt_money(listing.get('price_amount'))}  "
        f"{listing.get('size_sqm') or '?'} m²  "
        f"{_fmt_money(listing.get('price_per_sqm'))}/m²  "
        f"{listing.get('rooms') or '?'}q  status={listing.get('status')}"
    )
    fs = listing.get("flip_score")
    print(f"  flip_score (production): {fs:.1f}" if fs is not None else "  flip_score: n/a")

    # History timeline
    if history:
        print("  timeline:")
        print(f"    {listing.get('created_at', '?')[:10]}  created")
        for h in history:
            when = (h.get("changed_at") or "")[:10]
            fld = h.get("field")
            ov, nv = h.get("old_value"), h.get("new_value")
            if fld == "price_amount":
                try:
                    delta = (float(nv) - float(ov)) / float(ov) * 100
                    arrow = f"{_fmt_money(ov)} → {_fmt_money(nv)}  ({delta:+.1f}%)"
                except (TypeError, ValueError, ZeroDivisionError):
                    arrow = f"{ov} → {nv}"
                print(f"    {when}  price: {arrow}")
            elif fld == "status":
                print(f"    {when}  status: {ov} → {nv}")
            else:
                print(f"    {when}  {fld}: {ov} → {nv}")


def _choose(prompt: str, opts: str) -> str:
    while True:
        ans = input(prompt).strip().lower()
        if ans and ans[0] in opts:
            return ans[0]
        print(f"  (choose one of: {', '.join(opts)})")


def _apply_action(bucket: list[dict], key: tuple, action: str) -> str:
    """Apply user's choice in interactive mode. Returns a short log string."""
    idx = next((i for i, r in enumerate(bucket)
                if (r.get("source"), str(r.get("source_id"))) == key), None)
    if idx is None:
        return "not found"
    if action == "d":
        bucket.pop(idx)
        return "discarded"
    if action in ("g", "n", "b"):
        new_label = {"g": "good", "n": "neutral", "b": "bad"}[action]
        bucket[idx]["label"] = new_label
        bucket[idx]["source_of_label"] = "manual"
        bucket[idx]["labeled_at"] = date.today().isoformat()
        bucket[idx]["notes"] = (bucket[idx].get("notes", "") + " [audited]").strip()
        return f"override → {new_label} (manual)"
    return "kept"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--per-class", type=int, default=10,
                    help="How many labels per class to sample (default 10)")
    ap.add_argument("--only", choices=["good", "neutral", "bad"],
                    help="Audit only one class")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--include-manual", action="store_true",
                    help="Also audit labels marked source_of_label=manual")
    ap.add_argument("--interactive", action="store_true",
                    help="Prompt for keep/override/discard on each label")
    args = ap.parse_args()

    data = _load_labels()
    bucket = data.get("flip_labels", [])
    if not bucket:
        print("no flip_labels in data/labeled_deals.json — nothing to audit")
        return
    if not DB.exists():
        print(f"DB not found: {DB}", file=sys.stderr)
        sys.exit(1)

    # Partition + sample
    random.seed(args.seed)
    by_class: dict[str, list[dict]] = {"good": [], "neutral": [], "bad": []}
    for row in bucket:
        if not args.include_manual and row.get("source_of_label") != "derived":
            continue
        lab = row.get("label")
        if lab in by_class and (args.only is None or args.only == lab):
            by_class[lab].append(row)

    samples: list[dict] = []
    for lab, rows in by_class.items():
        if args.only and lab != args.only:
            continue
        if not rows:
            continue
        k = min(args.per_class, len(rows))
        samples += random.sample(rows, k)

    if not samples:
        filters = f"only={args.only!r}" if args.only else "any class"
        print(f"no derived labels matching filter ({filters})")
        return

    print(f"auditing {len(samples)} labels "
          f"({'interactive' if args.interactive else 'read-only'} mode)")

    change_log: list[str] = []
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        for i, lbl in enumerate(samples, 1):
            src, sid = lbl.get("source"), str(lbl.get("source_id"))
            listing = _fetch_listing(conn, src, sid)
            history = _fetch_history(conn, src, sid)
            _print_card(i, len(samples), lbl, listing, history)
            if args.interactive:
                ans = _choose(
                    "  action [k]eep / [g]ood / [n]eutral / [b]ad / [d]iscard / [s]kip / [q]uit: ",
                    "kgnbdsq",
                )
                if ans == "q":
                    print("quitting, saving changes")
                    break
                if ans == "s":
                    continue
                result = _apply_action(bucket, (src, sid), ans)
                change_log.append(f"  {src}/{sid}: {result}")

    if args.interactive and change_log:
        data["flip_labels"] = bucket
        _save_labels(data)
        print("\nchanges:")
        for line in change_log:
            print(line)
        print(f"saved → {LABELS}")
    elif args.interactive:
        print("\nno changes made")


if __name__ == "__main__":
    main()
