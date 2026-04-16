#!/usr/bin/env python3
"""Append a manually-curated label to data/labeled_deals.json.

Usage:
  # Label by listing DB id (preferred — joins into sales/rentals first):
  python3 scripts/label_deal.py 12345 good -n "T2 Anjos, strong comps"
  python3 scripts/label_deal.py 67890 bad -n "overpriced, 3 price cuts"

  # Label a rental:
  python3 scripts/label_deal.py 55123 --rent good -n "yield ~6%, Arroios"

  # Label by source + source_id directly:
  python3 scripts/label_deal.py --source idealista --source-id 33123456 good

  # Inspect the label set:
  python3 scripts/label_deal.py --list
  python3 scripts/label_deal.py --list --rent

Idempotent: relabeling the same (source, source_id) overwrites and bumps
labeled_at. Sale vs rental is inferred from which table the listing_id
lives in; you can force it with --rent / --flip.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DB = REPO / "backend" / "data" / "lisboa_realestate.db"
LABELS = REPO / "data" / "labeled_deals.json"


def _load() -> dict:
    if not LABELS.exists():
        return {"flip_labels": [], "rent_labels": []}
    return json.loads(LABELS.read_text())


def _save(data: dict) -> None:
    LABELS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _lookup_by_id(listing_id: int) -> tuple[str, str, str] | None:
    """Return (kind, source, source_id) for a listing row, or None."""
    if not DB.exists():
        return None
    with sqlite3.connect(DB) as conn:
        for table, kind in (("sales", "flip"), ("rentals", "rent")):
            row = conn.execute(
                f"SELECT source, source_id FROM {table} WHERE id = ?", (listing_id,)
            ).fetchone()
            if row:
                return kind, row[0], str(row[1])
    return None


def _upsert(data: dict, kind: str, source: str, source_id: str,
            label: str, notes: str | None) -> str:
    key = f"{kind}_labels"
    bucket = data.setdefault(key, [])
    today = date.today().isoformat()
    for row in bucket:
        if row.get("source") == source and str(row.get("source_id")) == str(source_id):
            row["label"] = label
            row["source_of_label"] = "manual"
            if notes:
                row["notes"] = notes
            row["labeled_at"] = today
            return "updated"
    bucket.append({
        "source": source,
        "source_id": str(source_id),
        "label": label,
        "source_of_label": "manual",
        "notes": notes or "",
        "labeled_at": today,
    })
    return "added"


def _list(data: dict, kind: str, limit: int = 20) -> None:
    bucket = data.get(f"{kind}_labels", [])
    print(f"{kind}_labels: {len(bucket)} total")
    tally: dict[str, int] = {}
    for r in bucket:
        tally[r.get("label", "?")] = tally.get(r.get("label", "?"), 0) + 1
    print("  by label:", tally)
    for r in bucket[-limit:]:
        notes = (r.get("notes") or "")[:60]
        print(f"  {r.get('source')}/{r.get('source_id')}  "
              f"{r.get('label'):<7} {r.get('source_of_label', '?'):<8} "
              f"{r.get('labeled_at', ''):<12} {notes}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("listing_id", nargs="?", type=int)
    ap.add_argument("label", nargs="?", choices=["good", "neutral", "bad"])
    ap.add_argument("-n", "--notes", help="Free-text justification")
    ap.add_argument("--source", help="Source (e.g. idealista) if not using listing_id")
    ap.add_argument("--source-id", help="Source ID if not using listing_id")
    kind = ap.add_mutually_exclusive_group()
    kind.add_argument("--flip", action="store_true", help="Force flip (sale) label")
    kind.add_argument("--rent", action="store_true", help="Force rent label")
    ap.add_argument("--list", action="store_true", help="List existing labels and exit")
    args = ap.parse_args()

    data = _load()

    if args.list:
        which = "rent" if args.rent else "flip"
        _list(data, which)
        return

    if not args.label:
        ap.error("label (good|neutral|bad) is required")

    # Resolve (kind, source, source_id)
    if args.source and args.source_id:
        kind = "rent" if args.rent else "flip"  # default to flip if not specified
        source, source_id = args.source, args.source_id
    elif args.listing_id is not None:
        found = _lookup_by_id(args.listing_id)
        if not found:
            print(f"listing_id {args.listing_id} not found in sales or rentals", file=sys.stderr)
            sys.exit(1)
        inferred_kind, source, source_id = found
        if args.flip and inferred_kind != "flip":
            print(f"--flip given but listing is a rental", file=sys.stderr); sys.exit(1)
        if args.rent and inferred_kind != "rent":
            print(f"--rent given but listing is a sale", file=sys.stderr); sys.exit(1)
        kind = inferred_kind
    else:
        ap.error("provide either a listing_id or --source + --source-id")

    action = _upsert(data, kind, source, source_id, args.label, args.notes)
    _save(data)
    print(f"{action}: {kind} label for {source}/{source_id} → {args.label}")


if __name__ == "__main__":
    main()
