"""
Test the renovation classifier on a handful of real listings.

Picks a diverse sample from the sales table (spread across price/m² buckets)
and runs classify_listing() on each. Prints results — does NOT write to DB.

Usage:
    python3 backend/scorers/test_renovation_classifier.py
    python3 backend/scorers/test_renovation_classifier.py --ids 281 1714 1202
    python3 backend/scorers/test_renovation_classifier.py --n 9
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# Allow `python3 backend/scorers/test_renovation_classifier.py` to work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scorers.renovation_classifier import classify_listing, save_result  # noqa: E402

import anthropic  # noqa: E402


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lisboa_realestate.db"


RENOVATION_COLUMNS = [
    ("renovation_class", "TEXT"),
    ("renovation_confidence", "REAL"),
    ("renovation_cost_estimate_eur_per_sqm", "INTEGER"),
    ("renovation_evidence", "TEXT"),
    ("renovation_needs", "TEXT"),
    ("renovation_classified_at", "TEXT"),
    ("renovation_model", "TEXT"),
]


def _ensure_renovation_columns(conn: sqlite3.Connection) -> None:
    """Idempotently add renovation_* columns to the sales table if missing."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(sales)").fetchall()}
    added = []
    for col, typ in RENOVATION_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE sales ADD COLUMN {col} {typ}")
            added.append(col)
    if added:
        conn.commit()
        print(f"[migration] Added columns to sales: {', '.join(added)}")


def _fetch_bottom_decile_per_locality(
    conn: sqlite3.Connection,
    min_listings_per_locality: int = 20,
    min_size_sqm: float = 35.0,
    min_price_per_sqm: float = 500.0,
) -> list[sqlite3.Row]:
    """Return the cheapest ~10% of listings within each locality.

    "Locality" = parish if populated, else neighborhood. In practice the current
    DB only has neighborhood populated for almost all listings, so grouping
    falls back to that. Only localities with >= min_listings_per_locality
    viable listings are considered (decile rankings on a handful of listings
    are meaningless). We also filter out obvious data-entry errors.
    """
    conn.row_factory = sqlite3.Row
    # Refinements:
    #   - property_type = 'apartment'        (ignore houses/land/etc. for fair comparison)
    #   - exclude generic 'Lisboa' bucket    (not a real neighborhood)
    #   - dedupe by hash_cross               (keep only one copy of repeated listings)
    #   - locality count threshold           (NTILE(10) is only meaningful on >=20 rows)
    sql = """
        WITH viable AS (
            SELECT id, source, url, price_amount, price_per_sqm, size_sqm,
                   neighborhood, parish, condition, images, description, hash_cross,
                   COALESCE(NULLIF(parish, ''), NULLIF(neighborhood, '')) AS locality
            FROM sales
            WHERE property_type = 'apartment'
              AND COALESCE(NULLIF(parish, ''), NULLIF(neighborhood, '')) IS NOT NULL
              AND COALESCE(NULLIF(parish, ''), NULLIF(neighborhood, '')) != 'Lisboa'
              AND COALESCE(NULLIF(parish, ''), NULLIF(neighborhood, '')) NOT LIKE 'Lisboa,%'
              AND price_per_sqm IS NOT NULL
              AND price_per_sqm > ?
              AND size_sqm IS NOT NULL AND size_sqm > ?
              AND images IS NOT NULL AND length(images) > 100
              AND status = 'active'
        ),
        deduped AS (
            -- Keep the single cheapest copy of each hash_cross group
            SELECT *
            FROM viable
            WHERE id = (
                SELECT id FROM viable v2
                WHERE (v2.hash_cross = viable.hash_cross AND viable.hash_cross IS NOT NULL AND viable.hash_cross != '')
                   OR (v2.id = viable.id)
                ORDER BY price_per_sqm ASC, id ASC
                LIMIT 1
            )
        ),
        counted AS (
            SELECT *, COUNT(*) OVER (PARTITION BY locality) AS locality_count
            FROM deduped
        ),
        ranked AS (
            SELECT *, NTILE(10) OVER (PARTITION BY locality ORDER BY price_per_sqm) AS tile
            FROM counted
            WHERE locality_count >= ?
        )
        SELECT id, source, url, price_amount, price_per_sqm, size_sqm,
               neighborhood, parish, condition, images, description, locality
        FROM ranked
        WHERE tile = 1
        ORDER BY locality, price_per_sqm
    """
    return list(conn.execute(sql, (min_price_per_sqm, min_size_sqm, min_listings_per_locality)).fetchall())


def _fetch_listings(
    conn: sqlite3.Connection,
    ids: list[int] | None,
    n: int,
    bottom_decile: bool,
) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    if ids:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""
            SELECT id, source, url, price_amount, price_per_sqm, size_sqm,
                   neighborhood, parish, condition, images, description
            FROM sales
            WHERE id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        return list(rows)

    if bottom_decile:
        return _fetch_bottom_decile_per_locality(conn)

    # Default: pull a spread across price/m² terciles so we see variety
    per_bucket = max(1, n // 3)
    buckets_sql = [
        # Lowest price/m² — likeliest to need renovation
        """SELECT id, source, url, price_amount, price_per_sqm, size_sqm,
                  neighborhood, parish, condition, images, description
           FROM sales
           WHERE images IS NOT NULL AND length(images) > 100
             AND size_sqm > 35
             AND price_per_sqm IS NOT NULL
             AND price_per_sqm < 3500
           ORDER BY RANDOM() LIMIT ?""",
        # Mid-range — mixed bag
        """SELECT id, source, url, price_amount, price_per_sqm, size_sqm,
                  neighborhood, parish, condition, images, description
           FROM sales
           WHERE images IS NOT NULL AND length(images) > 100
             AND size_sqm > 35
             AND price_per_sqm BETWEEN 4500 AND 6500
           ORDER BY RANDOM() LIMIT ?""",
        # High end — likeliest turnkey/new
        """SELECT id, source, url, price_amount, price_per_sqm, size_sqm,
                  neighborhood, parish, condition, images, description
           FROM sales
           WHERE images IS NOT NULL AND length(images) > 100
             AND size_sqm > 35
             AND price_per_sqm > 7500
           ORDER BY RANDOM() LIMIT ?""",
    ]

    rows: list[sqlite3.Row] = []
    for sql in buckets_sql:
        rows.extend(conn.execute(sql, (per_bucket,)).fetchall())
    return rows


def _format_price(amount: float | None) -> str:
    if amount is None:
        return "-"
    return f"€{int(amount):,}".replace(",", ".")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the renovation classifier")
    parser.add_argument("--n", type=int, default=6, help="Number of listings to classify (default: 6)")
    parser.add_argument("--ids", type=int, nargs="*", help="Specific listing IDs to test")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to SQLite DB")
    parser.add_argument(
        "--bottom-decile",
        action="store_true",
        help="Select the cheapest 10%% of listings per parish (parishes with >=10 listings)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap how many of the selected listings to actually classify (for testing on a subset)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selected listings but do not call the API",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Write results to the sales table (renovation_* columns). Requires migrated schema.",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: DB not found at {args.db}", file=sys.stderr)
        return 1

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY env var not set", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(args.db))
    rows = _fetch_listings(conn, args.ids, args.n, args.bottom_decile)

    # If --save, ensure the renovation_* columns exist on the target DB (idempotent)
    if args.save:
        _ensure_renovation_columns(conn)

    if not rows:
        print("No matching listings found.", file=sys.stderr)
        return 1

    total_selected = len(rows)
    if args.limit is not None and len(rows) > args.limit:
        rows = rows[:args.limit]
        print(f"Selected {total_selected} listings; capping to first {args.limit} for this run.")

    if args.bottom_decile:
        def _loc(r):
            return (r["parish"] or r["neighborhood"] or "?")
        localities = sorted({_loc(r) for r in rows})
        print(f"Bottom-decile-per-locality selection: "
              f"{len(rows)} listings across {len(localities)} localities")
        print(f"Estimated API cost: ~${len(rows) * 0.012:.2f} (at ~$0.012/listing)")

    if args.dry_run:
        print("\n[DRY RUN — no API calls]\n")
        current_loc = None
        for row in rows:
            loc = (row["parish"] or row["neighborhood"] or "?")
            if loc != current_loc:
                print(f"\n  -- {loc} --")
                current_loc = loc
            psqm = f"€{int(row['price_per_sqm']):,}/m²".replace(",", ".") if row['price_per_sqm'] else "-"
            print(f"  {row['id']:>6}  "
                  f"{_format_price(row['price_amount']):>12}  "
                  f"{row['size_sqm']:>5}m²  {psqm:>12}  "
                  f"cond={row['condition'] or '-'}")
        conn.close()
        return 0

    print(f"\nClassifying {len(rows)} listings with Claude vision...\n")
    print("=" * 80)

    client = anthropic.Anthropic()
    summary: list[dict] = []

    for row in rows:
        try:
            image_urls = json.loads(row["images"]) if row["images"] else []
        except json.JSONDecodeError:
            image_urls = []

        print(f"\nID {row['id']}  [{row['source']}]  {row['neighborhood'] or '-'}")
        print(f"  {_format_price(row['price_amount'])}  "
              f"{row['size_sqm']}m²  "
              f"€{int(row['price_per_sqm']):,}/m²".replace(",", ".") if row['price_per_sqm'] else "")
        print(f"  condition (from site): {row['condition'] or '-'}")
        print(f"  {len(image_urls)} images available")
        print(f"  URL: {row['url']}")

        result = classify_listing(
            image_urls=image_urls,
            description=row["description"],
            price_per_sqm=row["price_per_sqm"],
            client=client,
        )

        status = "OK" if result.error is None else f"ERROR ({result.error[:60]})"
        saved_tag = ""
        if args.save and result.error is None:
            save_result(conn, row["id"], result, table="sales")
            saved_tag = " [saved]"
        print(f"  -> {result.renovation_class.upper():<16}  "
              f"conf={result.confidence:.2f}  "
              f"€{result.cost_estimate_eur_per_sqm or 0}/m² reno  "
              f"[{status}]{saved_tag}")
        if result.text_hint:
            print(f"     text_hint: {result.text_hint}")
        for ev in result.evidence:
            print(f"     • {ev}")
        if result.needs:
            needs_str = ", ".join(f"{n['item']}({n['severity']})" for n in result.needs)
            print(f"     needs: {needs_str}")

        summary.append({
            "id": row["id"],
            "price_per_sqm": row["price_per_sqm"],
            "site_condition": row["condition"],
            "class": result.renovation_class,
            "confidence": result.confidence,
            "reno_cost": result.cost_estimate_eur_per_sqm,
            "error": result.error,
        })

    print("\n" + "=" * 80)
    print("\nSummary:")
    by_class: dict[str, int] = {}
    for s in summary:
        by_class[s["class"]] = by_class.get(s["class"], 0) + 1
    for cls, count in sorted(by_class.items()):
        print(f"  {cls}: {count}")

    errors = [s for s in summary if s["error"]]
    if errors:
        print(f"\n  errors: {len(errors)}")
        for e in errors:
            print(f"    id={e['id']}: {e['error'][:80]}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
