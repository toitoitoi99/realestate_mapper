"""
Run the Haiku vision photo tagger over all sale + rental listings whose
flip_score OR rent_score is at least the configured threshold (default B = 45).

Writes renovation + style tag fields back to the listing row. Idempotent:
listings already tagged are skipped unless --force.

Usage:
  python3 scripts/run_photo_tagging.py                 # all B+, both tables
  python3 scripts/run_photo_tagging.py --threshold 60  # A only
  python3 scripts/run_photo_tagging.py --limit 20      # try a sample first
  python3 scripts/run_photo_tagging.py --table sales   # one table
  python3 scripts/run_photo_tagging.py --concurrency 4 # parallel workers
  python3 scripts/run_photo_tagging.py --force         # re-tag already done

Cost: ~$0.01 per listing with claude-haiku-4-5.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic

# Make 'scorers' and 'database' importable when run from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scorers.photo_tagger import tag_listing, save_tags  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lisboa_realestate.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("photo_tagging")


def select_listings(conn: sqlite3.Connection, table: str, threshold: float,
                    force: bool, limit: int | None) -> list[tuple[int, list[str], str | None]]:
    """Return (id, image_urls, description) for B+ listings needing tagging."""
    where = ["images IS NOT NULL",
             "(flip_score >= ? OR rent_score >= ?)"]
    params: list = [threshold, threshold]
    if not force:
        where.append("(style_primary IS NULL OR renovation_class IS NULL)")
    # SQLite: MAX(a,b) is the scalar max function (not the aggregate).
    sql = (f"SELECT id, images, description FROM {table} "
           f"WHERE {' AND '.join(where)} "
           f"ORDER BY MAX(COALESCE(flip_score,0), COALESCE(rent_score,0)) DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"

    out: list[tuple[int, list[str], str | None]] = []
    for row in conn.execute(sql, params):
        try:
            urls = json.loads(row["images"]) if row["images"] else []
        except (json.JSONDecodeError, TypeError):
            urls = []
        if urls:
            out.append((row["id"], urls, row["description"]))
    return out


def tag_one(client: anthropic.Anthropic, listing_id: int, urls: list[str],
            desc: str | None) -> tuple[int, object]:
    """Worker: returns (listing_id, TagResult)."""
    return listing_id, tag_listing(urls, description=desc, client=client)


def run_for_table(conn: sqlite3.Connection, table: str, threshold: float,
                  concurrency: int, force: bool, limit: int | None) -> tuple[int, int, int]:
    rows = select_listings(conn, table, threshold, force, limit)
    if not rows:
        logger.info(f"[{table}] nothing to tag (threshold={threshold}, force={force})")
        return 0, 0, 0

    logger.info(f"[{table}] tagging {len(rows)} listings "
                f"(threshold={threshold}, concurrency={concurrency})")

    client = anthropic.Anthropic()
    ok = err = saved = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(tag_one, client, lid, urls, desc): lid
                   for lid, urls, desc in rows}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                listing_id, result = fut.result()
            except Exception as e:
                err += 1
                logger.warning(f"[{table}] worker exception: {e}")
                continue

            if result.error:
                err += 1
                logger.debug(f"[{table}] {listing_id} skipped: {result.error}")
            else:
                ok += 1
                save_tags(conn, listing_id, result, table=table)
                saved += 1

            if i % 25 == 0 or i == len(rows):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(rows) - i) / rate if rate > 0 else 0
                logger.info(f"[{table}] {i}/{len(rows)} "
                            f"(ok={ok} err={err}) "
                            f"{rate:.1f}/s ETA {eta:.0f}s")

    return ok, err, saved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=45.0,
                    help="Minimum flip OR rent score to include (default 45 = B)")
    ap.add_argument("--table", choices=["sales", "rentals", "both"], default="both")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap rows per table (for testing)")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="Parallel API workers (default 4)")
    ap.add_argument("--force", action="store_true",
                    help="Re-tag listings even if already classified")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    tables = ["sales", "rentals"] if args.table == "both" else [args.table]
    grand_ok = grand_err = 0
    for t in tables:
        ok, err, _ = run_for_table(conn, t, args.threshold, args.concurrency,
                                   args.force, args.limit)
        grand_ok += ok
        grand_err += err

    logger.info(f"DONE — tagged {grand_ok}, errors {grand_err}")
    conn.close()
    return 0 if grand_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
