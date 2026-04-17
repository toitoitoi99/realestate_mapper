"""
Photo tagger — single Claude Haiku vision call that returns BOTH renovation
condition (turnkey/cosmetic/full_renovation, cost, evidence, needs) AND style
tags (style, light, color palette, outdoor space, floor material, standout
features). Designed for the persona feature so the UI can filter by style and
match user-provided reference images.

One call per listing. ~$0.01 per listing with claude-haiku-4-5.

Usage:
    from scorers.photo_tagger import tag_listing, save_tags
    result = tag_listing(image_urls, description="...")
    save_tags(conn, listing_id, result, table="sales")
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import List, Optional

import anthropic

# Reuse the proven helpers from the renovation classifier — same image
# selection, same download logic, same model. We're just expanding the prompt
# and the schema.
from scorers.renovation_classifier import (  # noqa: E402
    MODEL,
    VALID_CLASSES,
    VALID_NEED_ITEMS,
    VALID_NEED_SEVERITIES,
    _download_image,
    _select_images,
    _sanitize_needs,
    detect_text_hint,
)

# ---------- Style vocabulary (controlled values) -----------------------------

VALID_STYLES = {
    "modern", "traditional", "minimalist", "industrial",
    "scandinavian", "classic", "rustic", "eclectic",
}
VALID_LIGHT = {"bright", "average", "dim"}
VALID_PALETTE = {"light", "dark", "warm", "cool", "mixed"}
VALID_OUTDOOR = {"none", "balcony", "terrace", "garden", "rooftop"}
VALID_FLOOR = {"wood", "tile", "stone", "laminate", "mixed", "unknown"}
VALID_FEATURES = {
    "sea_view", "river_view", "city_view", "garden_view",
    "fireplace", "exposed_beams", "high_ceilings", "open_kitchen",
    "walk_in_closet", "ensuite", "double_height", "sky_light",
    "pool", "parking", "elevator",
}


def _coerce(value, allowed: set, default: Optional[str] = None) -> Optional[str]:
    """Lower-case + strip; return value if in allowed set, else default."""
    if value is None:
        return default
    v = str(value).lower().strip()
    return v if v in allowed else default


def _coerce_features(raw) -> List[str]:
    """Filter features to controlled vocab, dedupe, cap at 6."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        v = str(entry).lower().strip()
        if v in VALID_FEATURES and v not in seen:
            out.append(v)
            seen.add(v)
        if len(out) >= 6:
            break
    return out


# ---------- Result type ------------------------------------------------------


@dataclass
class TagResult:
    # Renovation block
    renovation_class: str
    renovation_confidence: float
    renovation_cost_estimate_eur_per_sqm: Optional[int]
    renovation_evidence: List[str] = field(default_factory=list)
    renovation_needs: List[dict] = field(default_factory=list)

    # Style block
    style_primary: Optional[str] = None
    style_secondary: Optional[str] = None
    light_level: Optional[str] = None
    color_palette: Optional[str] = None
    outdoor_type: Optional[str] = None
    floor_material: Optional[str] = None
    standout_features: List[str] = field(default_factory=list)

    # Meta
    text_hint: Optional[str] = None
    images_used: int = 0
    model: str = MODEL
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- Prompt -----------------------------------------------------------


SYSTEM_PROMPT = """You are a real estate condition + style assessor for Lisbon, Portugal residential properties.

Looking at the listing photos, return a single JSON object with TWO blocks:

== RENOVATION (condition assessment) ==

Classify into ONE of:
- "turnkey" — Livable as-is. Functional kitchen and bathroom, no visible structural damage. Renovation €0–150/m².
- "cosmetic" — Needs paint, fixtures, dated kitchen/bathroom but functional. Renovation €200–500/m².
- "full_renovation" — Major rebuild. Exposed brick from fallen plaster, missing kitchen/bathroom, water damage, mould, collapsed ceiling, rotten wood, exposed wiring/pipes, empty shell, or "para recuperar" / "devoluto". Renovation €1000–2500/m².

Grade by the WORST room you see.

== STYLE (interior aesthetic) ==

Tag the property using these CONTROLLED values:

- style_primary: the dominant interior style. ONE of:
    "modern" | "traditional" | "minimalist" | "industrial" |
    "scandinavian" | "classic" | "rustic" | "eclectic"

- style_secondary: optional secondary influence (same vocabulary), or null.

- light_level: how bright the interior looks across the photos:
    "bright" | "average" | "dim"

- color_palette: dominant color feel:
    "light" | "dark" | "warm" | "cool" | "mixed"

- outdoor_type: type of outdoor space VISIBLE in the photos:
    "none" | "balcony" | "terrace" | "garden" | "rooftop"

- floor_material: dominant floor type:
    "wood" | "tile" | "stone" | "laminate" | "mixed" | "unknown"

- standout_features: array (0 to 6) of notable features VISIBLE in the photos. Use ONLY these values:
    "sea_view" | "river_view" | "city_view" | "garden_view" |
    "fireplace" | "exposed_beams" | "high_ceilings" | "open_kitchen" |
    "walk_in_closet" | "ensuite" | "double_height" | "sky_light" |
    "pool" | "parking" | "elevator"

Only include features you can clearly see — do not guess.

== OUTPUT ==

Return ONLY this JSON shape, nothing else:

{
  "renovation_class": "turnkey" | "cosmetic" | "full_renovation",
  "renovation_confidence": 0.0 to 1.0,
  "renovation_cost_estimate_eur_per_sqm": integer,
  "renovation_evidence": ["short phrase", ...],
  "renovation_needs": [{"item": "kitchen|bathroom|flooring|walls_paint|windows|electrical|plumbing|roof|structure|facade|ceiling|doors", "severity": "minor|cosmetic|full"}, ...],
  "style_primary": "modern" | ...,
  "style_secondary": "modern" | ... | null,
  "light_level": "bright" | "average" | "dim",
  "color_palette": "light" | "dark" | "warm" | "cool" | "mixed",
  "outdoor_type": "none" | "balcony" | "terrace" | "garden" | "rooftop",
  "floor_material": "wood" | "tile" | "stone" | "laminate" | "mixed" | "unknown",
  "standout_features": ["sea_view", ...]
}

Give 2-5 evidence bullets. For "renovation_needs" list ONLY things needing attention. No prose outside the JSON."""


# ---------- Main entrypoint --------------------------------------------------


def tag_listing(
    image_urls: List[str],
    description: Optional[str] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> TagResult:
    """One Haiku call → renovation classification + style tags."""
    text_hint = detect_text_hint(description)

    if not image_urls:
        return TagResult(
            renovation_class="turnkey", renovation_confidence=0.0,
            renovation_cost_estimate_eur_per_sqm=None,
            text_hint=text_hint, images_used=0, error="no_images",
        )

    selected = _select_images(image_urls)
    downloaded: list[tuple[str, bytes]] = []
    for url in selected:
        d = _download_image(url)
        if d is not None:
            downloaded.append(d)

    if not downloaded:
        return TagResult(
            renovation_class="turnkey", renovation_confidence=0.0,
            renovation_cost_estimate_eur_per_sqm=None,
            text_hint=text_hint, images_used=0,
            error="all_image_downloads_failed",
        )

    content: list[dict] = []
    for ctype, blob in downloaded:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": ctype,
                "data": base64.standard_b64encode(blob).decode("ascii"),
            },
        })
    user_text = "Tag this property based on the photos above. Return only JSON."
    if description:
        snippet = description[:600].replace("\n", " ")
        user_text += f"\n\nFor context, the listing description says: \"{snippet}\""
    content.append({"type": "text", "text": user_text})

    if client is None:
        client = anthropic.Anthropic()

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=900,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        return TagResult(
            renovation_class="turnkey", renovation_confidence=0.0,
            renovation_cost_estimate_eur_per_sqm=None,
            text_hint=text_hint, images_used=len(downloaded),
            error=f"api_error: {e}",
        )

    raw = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return TagResult(
            renovation_class="turnkey", renovation_confidence=0.0,
            renovation_cost_estimate_eur_per_sqm=None,
            text_hint=text_hint, images_used=len(downloaded),
            error=f"json_parse_failed: {raw[:200]}",
        )

    rc = str(parsed.get("renovation_class", "")).lower()
    if rc not in VALID_CLASSES:
        return TagResult(
            renovation_class="turnkey", renovation_confidence=0.0,
            renovation_cost_estimate_eur_per_sqm=None,
            text_hint=text_hint, images_used=len(downloaded),
            error=f"invalid_class: {rc}",
        )

    return TagResult(
        renovation_class=rc,
        renovation_confidence=float(parsed.get("renovation_confidence", 0.0)),
        renovation_cost_estimate_eur_per_sqm=parsed.get("renovation_cost_estimate_eur_per_sqm"),
        renovation_evidence=list(parsed.get("renovation_evidence", []))[:5],
        renovation_needs=_sanitize_needs(parsed.get("renovation_needs")),
        style_primary=_coerce(parsed.get("style_primary"), VALID_STYLES),
        style_secondary=_coerce(parsed.get("style_secondary"), VALID_STYLES),
        light_level=_coerce(parsed.get("light_level"), VALID_LIGHT),
        color_palette=_coerce(parsed.get("color_palette"), VALID_PALETTE),
        outdoor_type=_coerce(parsed.get("outdoor_type"), VALID_OUTDOOR),
        floor_material=_coerce(parsed.get("floor_material"), VALID_FLOOR),
        standout_features=_coerce_features(parsed.get("standout_features")),
        text_hint=text_hint,
        images_used=len(downloaded),
    )


# ---------- DB persistence ---------------------------------------------------


def save_tags(
    conn: sqlite3.Connection,
    listing_id: int,
    result: TagResult,
    table: str = "sales",
) -> None:
    """Persist both renovation + style tag fields. Skips on error."""
    if result.error is not None:
        return
    if table not in {"sales", "rentals"}:
        raise ValueError(f"invalid table {table!r}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        f"""
        UPDATE {table} SET
            renovation_class = ?,
            renovation_confidence = ?,
            renovation_cost_estimate_eur_per_sqm = ?,
            renovation_evidence = ?,
            renovation_needs = ?,
            renovation_classified_at = ?,
            renovation_model = ?,
            style_primary = ?,
            style_secondary = ?,
            light_level = ?,
            color_palette = ?,
            outdoor_type = ?,
            floor_material = ?,
            standout_features = ?
        WHERE id = ?
        """,
        (
            result.renovation_class,
            result.renovation_confidence,
            result.renovation_cost_estimate_eur_per_sqm,
            json.dumps(result.renovation_evidence, ensure_ascii=False),
            json.dumps(result.renovation_needs, ensure_ascii=False),
            now,
            result.model,
            result.style_primary,
            result.style_secondary,
            result.light_level,
            result.color_palette,
            result.outdoor_type,
            result.floor_material,
            json.dumps(result.standout_features, ensure_ascii=False),
            listing_id,
        ),
    )
    conn.commit()
