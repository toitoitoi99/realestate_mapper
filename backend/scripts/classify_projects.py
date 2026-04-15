"""
Backfill the construction_projects.classification column using
signals.classify_project.

Run: python3 scripts/classify_projects.py [--limit N] [--force]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database as db  # noqa: E402
from signals import classify_project  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true",
                        help="Reclassify rows that already have classification set")
    args = parser.parse_args()

    db.init_db()
    conn = db.get_connection()
    where = "" if args.force else "WHERE classification IS NULL"
    q = f"""SELECT id, operation, subject, procedure, typology, permit_type
            FROM construction_projects {where}"""
    if args.limit:
        q += f" LIMIT {int(args.limit)}"

    rows = conn.execute(q).fetchall()
    print(f"Classifying {len(rows)} projects...")

    counts = {}
    for r in rows:
        # Build a text surface for the classifier: operation + subject + procedure.
        synthetic = {
            "operation":  r["operation"],
            "description": " ".join(filter(None, [r["subject"], r["procedure"], r["typology"], r["permit_type"]])),
        }
        cls = classify_project(synthetic)
        counts[cls] = counts.get(cls, 0) + 1
        conn.execute(
            "UPDATE construction_projects SET classification=? WHERE id=?",
            (cls, r["id"]),
        )
    conn.commit()
    conn.close()

    print("Classification counts:")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:15s} {v}")


if __name__ == "__main__":
    main()
