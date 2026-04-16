"""Scratch-DB helpers for experiment runs.

Each variant agent works on an isolated SQLite copy of the production DB so
score recomputation never leaks into `backend/data/lisboa_realestate.db`.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path


def clone_db(src: Path, dst: Path) -> Path:
    """Copy src DB + WAL sidecars to dst. Dst parent is created.

    Returns dst. If dst exists it is overwritten.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Use sqlite backup API so we get a consistent snapshot even under WAL.
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    return dst


def add_score_columns(db: Path, cols: dict[str, str]) -> None:
    """Idempotently add columns to `sales` and `rentals`.

    cols maps column_name -> sql type (e.g. {"variant_flip_score": "REAL"}).
    Existing columns are left alone.
    """
    with sqlite3.connect(db) as conn:
        for table in ("sales", "rentals"):
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, sqltype in cols.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")
        conn.commit()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Clone prod DB into a scratch path")
    p.add_argument("--src", required=True)
    p.add_argument("--dst", required=True)
    args = p.parse_args()
    out = clone_db(Path(args.src), Path(args.dst))
    print(f"cloned → {out} ({out.stat().st_size/1e6:.1f} MB)")
