"""Load the manually-curated + signal-derived deals dataset.

Schema (data/labeled_deals.json):
{
  "flip_labels": [
    {"source": "idealista", "source_id": "33123456",
     "label": "good", "source_of_label": "derived",
     "notes": "...", "labeled_at": "2026-04-15"}
  ],
  "rent_labels": [ ... same structure ... ]
}

CRITICAL: flip_labels grade SALES only; rent_labels grade RENTALS only.
They are loaded into disjoint lookup tables so a variant can never
accidentally use rent outcomes to grade flip or vice versa.

Labels are optional. A missing file or an empty list yields an empty
lookup — the evaluator silently skips label-correlation for that side
and notes the handicap in the REPORT.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

LabelKind = Literal["flip", "rent"]

LABEL_MAP = {"good": 1.0, "neutral": 0.0, "bad": -1.0}


def _load_root(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    with path.open() as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def load_labels(path: Path, kind: LabelKind) -> list[dict]:
    """Load flip_labels or rent_labels. Rejects the legacy flat schema."""
    root = _load_root(path)
    if kind not in ("flip", "rent"):
        raise ValueError(f"kind must be 'flip' or 'rent', got {kind!r}")
    key = f"{kind}_labels"
    if "labels" in root and key not in root:
        # Fail loud on the legacy schema rather than silently mis-grading.
        raise ValueError(
            f"{path} uses the legacy flat 'labels' schema. "
            f"Split into 'flip_labels' and 'rent_labels' before loading."
        )
    return root.get(key, []) or []


def label_lookup(labels: list[dict]) -> dict[tuple, float]:
    """Return {(source, source_id): numeric_label}.

    Tuple is just (source, source_id) — the kind is already fixed at
    load time. Collisions within the same file keep the last write.
    """
    out: dict[tuple, float] = {}
    for row in labels:
        src = row.get("source")
        sid = row.get("source_id")
        if src is None or sid is None:
            continue
        val = LABEL_MAP.get(str(row.get("label", "")).lower())
        if val is not None:
            out[(src, str(sid))] = val
    return out


def default_path() -> Path:
    """Repo-root-relative default path (walks up from this file)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "data").is_dir() and (parent / "backend").is_dir():
            return parent / "data" / "labeled_deals.json"
    return Path("data/labeled_deals.json")


def summary(path: Path) -> dict:
    """Quick count by kind × label, used by the manual-label CLI."""
    out: dict[str, dict[str, int]] = {"flip": {}, "rent": {}}
    for kind in ("flip", "rent"):
        for row in load_labels(path, kind):
            lab = str(row.get("label", "")).lower()
            out[kind][lab] = out[kind].get(lab, 0) + 1
    return out


if __name__ == "__main__":
    p = default_path()
    try:
        print(f"{p}")
        print(f"  flip: {len(load_labels(p, 'flip'))} labels")
        print(f"  rent: {len(load_labels(p, 'rent'))} labels")
        print(f"  breakdown: {summary(p)}")
    except ValueError as e:
        print(f"ERROR: {e}")
