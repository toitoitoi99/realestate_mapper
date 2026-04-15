"""
Populate text-derived signal columns from listing descriptions.

Writes to:
  orientation, building_year, condominium_fee, energy_class,
  days_on_market, price_drop_count

Run:
  python3 scripts/extract_text_signals.py                # all listings
  python3 scripts/extract_text_signals.py --force        # overwrite
  python3 scripts/extract_text_signals.py --listing-type sales
  python3 scripts/extract_text_signals.py --limit 100
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database as db  # noqa: E402
from signals_text import extract_all  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _days_between(iso: str, now: datetime) -> int:
    """Return whole days between ISO timestamp and now. 0 if unparseable."""
    if not iso:
        return 0
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0
    delta = now - t
    return max(0, int(delta.total_seconds() // 86400))


def _walk_prev_chain(conn, tbl: str, listing_id: int, max_steps: int = 20):
    """Walk the previous_listing_id chain backwards and return a list of
    (listing_id, price_amount, scraped_at) tuples including the starting row.
    """
    chain = []
    current_id = listing_id
    seen = set()
    for _ in range(max_steps):
        if current_id in seen:
            break
        seen.add(current_id)
        row = conn.execute(
            f"SELECT id, price_amount, scraped_at, previous_listing_id "
            f"FROM {tbl} WHERE id = ?", (current_id,),
        ).fetchone()
        if not row:
            break
        chain.append((row["id"], row["price_amount"], row["scraped_at"]))
        prev = row["previous_listing_id"]
        if not prev:
            break
        current_id = prev
    return chain


def _count_price_drops(chain) -> int:
    """Count strictly-decreasing price transitions walking oldest → newest.

    chain is listed newest-first (how we walked it) — reverse for comparison.
    """
    prices = [p for (_, p, _) in reversed(chain) if p]
    if len(prices) < 2:
        return 0
    drops = 0
    for i in range(1, len(prices)):
        if prices[i] < prices[i - 1]:
            drops += 1
    return drops


def _oldest_scraped_at(chain) -> str:
    """Oldest scraped_at in the chain, for days-on-market."""
    stamps = [s for (_, _, s) in chain if s]
    if not stamps:
        return ""
    try:
        return min(stamps)
    except TypeError:
        return ""


def process(tbl: str, force: bool, limit, now: datetime) -> dict:
    conn = db.get_connection()
    where = "WHERE description IS NOT NULL"
    if not force:
        # Only rows missing ALL text-derived fields (cheap heuristic).
        where += (
            " AND orientation IS NULL AND building_year IS NULL "
            "AND condominium_fee IS NULL AND energy_class IS NULL"
        )
    q = f"SELECT id, description FROM {tbl} {where}"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q).fetchall()

    logger.info(f"[{tbl}] extracting text signals for {len(rows)} listings "
                f"(force={force})")

    # Extraction
    stats = {"orientation": 0, "building_year": 0, "condominium_fee": 0,
             "energy_class": 0, "light_hint": 0}
    for r in rows:
        res = extract_all(r["description"])
        if res["orientation"]:     stats["orientation"] += 1
        if res["building_year"]:   stats["building_year"] += 1
        if res["condominium_fee"]: stats["condominium_fee"] += 1
        if res["energy_class"]:    stats["energy_class"] += 1
        if res["light_hint"]:      stats["light_hint"] += 1
        conn.execute(
            f"""UPDATE {tbl} SET
                    orientation = ?,
                    building_year = ?,
                    condominium_fee = ?,
                    energy_class = ?,
                    building_stage = ?
                WHERE id = ?""",
            (res["orientation"], res["building_year"], res["condominium_fee"],
             res["energy_class"], res.get("building_stage"), r["id"]),
        )

    conn.commit()
    logger.info(f"[{tbl}] text signals: {stats}")

    # Days-on-market + price drops (independent of description; always run)
    all_ids = [r["id"] for r in conn.execute(
        f"SELECT id FROM {tbl}").fetchall()]
    logger.info(f"[{tbl}] computing DOM + price drops for {len(all_ids)} rows")
    for lid in all_ids:
        chain = _walk_prev_chain(conn, tbl, lid)
        oldest = _oldest_scraped_at(chain)
        dom = _days_between(oldest, now) if oldest else 0
        drops = _count_price_drops(chain)
        conn.execute(
            f"UPDATE {tbl} SET days_on_market = ?, price_drop_count = ? "
            f"WHERE id = ?", (dom, drops, lid),
        )
    conn.commit()
    conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listing-type", choices=["sales", "rentals"], default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    tables = [args.listing_type] if args.listing_type else ["sales", "rentals"]
    for tbl in tables:
        process(tbl, args.force, args.limit, now)


if __name__ == "__main__":
    main()
