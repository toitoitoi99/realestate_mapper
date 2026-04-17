"""
Batch driver for the vision-based renovation classifier.

Iterates every listing missing a `renovation_class` and has photo URLs, and
calls `scorers.renovation_classifier.classify_listing` on each, persisting
the result. Uses a pool of threads to overlap network + Anthropic calls.

Cost: Claude Haiku 4.5 with up to 6 images per listing. Plan for tens of
dollars on the full catalog. Use `--limit` for smoke tests first.

Requires `ANTHROPIC_API_KEY` in the environment.

Run:
  python3 scripts/classify_renovation.py                      # sales + rentals
  python3 scripts/classify_renovation.py --listing-type sales
  python3 scripts/classify_renovation.py --limit 20           # smoke test
  python3 scripts/classify_renovation.py --workers 8          # parallelism
  python3 scripts/classify_renovation.py --skip-if-staged     # skip rows where
                                                              # text already
                                                              # set a confident
                                                              # building_stage

After this, re-run signal_batch.py so reno_cost_estimate and the flip/rent
scores pick up the new `renovation_cost_estimate_eur_per_sqm`.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anthropic  # noqa: E402
import database as db  # noqa: E402
from scorers.renovation_classifier import classify_listing, save_result  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# Text-derived building_stage values that are strong enough to skip vision on.
# `needs_reno` / `full_remodel` / `turnkey` / `approved_project` → confident.
CONFIDENT_STAGES = {"needs_reno", "full_remodel", "turnkey", "approved_project"}


def _parse_images(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        urls = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [u for u in urls if isinstance(u, str) and u.startswith("http")]


def _select_rows(conn, tbl: str, skip_if_staged: bool, limit: Optional[int],
                 listing_id: Optional[int]):
    where = ["renovation_class IS NULL", "images IS NOT NULL", "LENGTH(images) > 10"]
    params: list = []
    if listing_id is not None:
        where.append("id = ?")
        params.append(listing_id)
    if skip_if_staged:
        placeholders = ",".join("?" for _ in CONFIDENT_STAGES)
        where.append(
            f"(building_stage IS NULL OR building_stage NOT IN ({placeholders}))"
        )
        params.extend(CONFIDENT_STAGES)
    q = (f"SELECT id, images, description FROM {tbl} "
         f"WHERE {' AND '.join(where)}")
    if limit:
        q += f" LIMIT {int(limit)}"
    return conn.execute(q, params).fetchall()


def _process_table(tbl: str, workers: int, skip_if_staged: bool,
                   limit: Optional[int], listing_id: Optional[int]) -> None:
    conn = db.get_connection()
    rows = _select_rows(conn, tbl, skip_if_staged, limit, listing_id)
    logger.info(f"[{tbl}] classifying {len(rows)} listings "
                f"(workers={workers}, skip_if_staged={skip_if_staged})")
    if not rows:
        conn.close()
        return

    client = anthropic.Anthropic()
    db_lock = threading.Lock()
    t0 = time.time()
    done = 0
    failed = 0
    class_counts: dict = {}

    def work(row):
        image_urls = _parse_images(row["images"])
        if not image_urls:
            return row["id"], None, "no_images"
        result = classify_listing(
            image_urls=image_urls,
            description=row["description"],
            client=client,
        )
        return row["id"], result, result.error

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, r) for r in rows]
        for fut in as_completed(futures):
            try:
                lid, result, err = fut.result()
            except Exception as e:
                failed += 1
                logger.warning(f"[{tbl}] worker raised: {e}")
                continue
            done += 1
            if err:
                failed += 1
                logger.debug(f"[{tbl}] id={lid} error: {err}")
            elif result is not None:
                class_counts[result.renovation_class] = (
                    class_counts.get(result.renovation_class, 0) + 1
                )
                with db_lock:
                    save_result(conn, lid, result, table=tbl)
            if done % 50 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                logger.info(f"[{tbl}] {done}/{len(rows)} "
                            f"({rate:.1f}/s, {failed} failed)")

    conn.close()
    elapsed = time.time() - t0
    logger.info(f"[{tbl}] done: {done}/{len(rows)} in {elapsed:.0f}s "
                f"({failed} failed); classes: {class_counts}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listing-type", choices=["sales", "rentals"], default=None)
    parser.add_argument("--listing-id", type=int, default=None,
                        help="Classify a single listing id (for debugging)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4,
                        help="Concurrent API calls (default: 4)")
    parser.add_argument("--skip-if-staged", action="store_true",
                        help="Skip listings whose building_stage is already "
                             "confidently set from text — saves API cost.")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY not set")
        sys.exit(1)

    tables = [args.listing_type] if args.listing_type else ["sales", "rentals"]
    for tbl in tables:
        _process_table(
            tbl,
            workers=args.workers,
            skip_if_staged=args.skip_if_staged,
            limit=args.limit,
            listing_id=args.listing_id,
        )


if __name__ == "__main__":
    main()
