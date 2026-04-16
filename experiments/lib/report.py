"""REPORT.md writers used by variant agents."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def _fmt(x, digits=3):
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def write_report(
    out_path: Path,
    slug: str,
    title: str,
    hypothesis: str,
    implementation_notes: str,
    metrics: dict,
    top_bottom: dict,
    diff_summary: Optional[str] = None,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dist = metrics.get("distribution", {})
    lines: list[str] = []
    lines += [
        f"# {title}",
        "",
        f"**Slug:** `{slug}`  ",
        f"**Table:** `{metrics.get('table')}`  ",
        f"**Score column:** `{metrics.get('score_col')}`  ",
        f"**N listings scored:** {metrics.get('n')}",
        "",
        "## Hypothesis",
        "",
        hypothesis,
        "",
        "## Implementation notes",
        "",
        implementation_notes,
        "",
    ]
    if diff_summary:
        lines += ["## Code diff summary", "", diff_summary, ""]

    lines += [
        "## Rubric",
        "",
        f"- **Composite rubric score:** {_fmt(metrics.get('rubric_score'))}",
        f"- Distribution spread (sd): {_fmt(dist.get('sd'), 2)}  "
        f"(n={dist.get('n')}, skew={_fmt(dist.get('skew'), 2)}, "
        f"excess kurtosis={_fmt(dist.get('excess_kurtosis'), 2)})",
        f"- Spearman ρ vs production `flip_score`: {_fmt(metrics.get('spearman_vs_production'))}",
        f"- Spearman ρ vs labeled deals: {_fmt(metrics.get('spearman_vs_labels'))}  "
        f"(labeled n = {metrics.get('labeled_n', 0)})",
        f"- Neighborhood stability: {_fmt(metrics.get('neighborhood_stability'))}",
        "",
        "### Rubric components (weighted)",
        "",
    ]
    for k, v in (metrics.get("rubric_components") or {}).items():
        lines.append(f"- {k}: {_fmt(v)}")
    lines += [
        "",
        "### Band shares (A ≥ 80, B ≥ 65, C ≥ 50, D < 50)",
        "",
    ]
    for band, share in (metrics.get("band_shares") or {}).items():
        lines.append(f"- {band}: {share*100:.1f}%")

    lines += [
        "",
        "## Distribution histogram",
        "",
        "```",
        metrics.get("histogram_ascii", ""),
        "```",
        "",
        "## Top 20 (variant)",
        "",
        _listing_table(top_bottom.get("top", []), metrics.get("score_col")),
        "",
        "## Bottom 20 (variant)",
        "",
        _listing_table(top_bottom.get("bottom", []), metrics.get("score_col")),
        "",
        "## Comparison to production",
        "",
        _comparison_block(top_bottom, metrics.get("score_col")),
    ]
    out_path.write_text("\n".join(lines))
    return out_path


def _listing_table(rows: list[dict], score_col: str) -> str:
    if not rows:
        return "_no rows_"
    out = [
        "| # | source/id | parish | €/m² | sqm | rooms | variant | prod flip |",
        "|---|-----------|--------|------|-----|-------|---------|-----------|",
    ]
    for i, r in enumerate(rows, 1):
        out.append(
            f"| {i} | {r.get('source')}/{r.get('source_id')} | "
            f"{r.get('parish') or r.get('neighborhood') or ''} | "
            f"{_fmt(r.get('price_per_sqm'), 0)} | {_fmt(r.get('size_sqm'), 0)} | "
            f"{r.get('rooms') or ''} | {_fmt(r.get(score_col), 1)} | "
            f"{_fmt(r.get('flip_score'), 1)} |"
        )
    return "\n".join(out)


def _comparison_block(tb: dict, score_col: str) -> str:
    top_ids = {(r.get("source"), r.get("source_id")) for r in tb.get("top", [])}
    # How many of variant-top-20 are also in prod top by flip_score? Quick overlap
    # signal — aggregator uses it alongside Spearman.
    overlap_note = f"_Variant-top-20 ids: {len(top_ids)}. See Spearman vs production above for overall ordering agreement._"
    return overlap_note
