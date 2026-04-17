"""
Backfill the `building_stage` column from listing descriptions.

`extract_text_signals.py` already writes this column, but its filter skips
rows that have any other text-derived field populated — so `building_stage`
never gets backfilled on legacy data. This script touches only
`building_stage` and leaves every other column alone.

Downstream effect: `signal_batch._renovation_signals` uses `building_stage`
as a text-fallback for the vision classifier. After running this, re-run
`signal_batch.py` so `reno_cost_estimate` (and the flip/rent scores) pick
up the new signal.

Run:
  python3 scripts/backfill_building_stage.py                    # sales + rentals
  python3 scripts/backfill_building_stage.py --listing-type sales
  python3 scripts/backfill_building_stage.py --force            # overwrite
  python3 scripts/backfill_building_stage.py --limit 500        # smoke test
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database as db  # noqa: E402
from signals_text import extract_building_stage  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def process(tbl: str, force: bool, limit) -> dict:
    conn = db.get_connection()
    where = "WHERE description IS NOT NULL"
    if not force:
        where += " AND building_stage IS NULL"
    q = f"SELECT id, description FROM {tbl} {where}"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q).fetchall()
    logger.info(f"[{tbl}] scanning {len(rows)} listings (force={force})")

    counts: dict = {}
    updated = 0
    for r in rows:
        stage = extract_building_stage(r["description"])
        if stage is None and not force:
            continue
        counts[stage or "none"] = counts.get(stage or "none", 0) + 1
        conn.execute(
            f"UPDATE {tbl} SET building_stage = ? WHERE id = ?",
            (stage, r["id"]),
        )
        updated += 1
        if updated % 500 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    logger.info(f"[{tbl}] updated {updated} rows; distribution: {counts}")
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listing-type", choices=["sales", "rentals"], default=None)
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing building_stage values")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    tables = [args.listing_type] if args.listing_type else ["sales", "rentals"]
    for tbl in tables:
        process(tbl, args.force, args.limit)


if __name__ == "__main__":
    main()
