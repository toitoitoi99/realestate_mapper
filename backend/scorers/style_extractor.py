"""
Style-tag extraction for a USER-PROVIDED reference image (e.g. a Pinterest
mood board, a photo from a magazine). Returns the same controlled-vocabulary
tags the listing photo_tagger uses, so they can be used as preferences.

Skips renovation classification — that vocabulary only makes sense for
listings (kitchen/bathroom condition etc.), not lifestyle inspiration.

Usage:
    from scorers.style_extractor import extract_style_tags
    result = extract_style_tags(image_bytes, media_type="image/jpeg")
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, asdict, field
from typing import List, Optional

import anthropic

from scorers.photo_tagger import (  # reuse the controlled vocabulary
    VALID_STYLES, VALID_LIGHT, VALID_PALETTE, VALID_OUTDOOR,
    VALID_FLOOR, _coerce, _coerce_features,
)
from scorers.renovation_classifier import MODEL


SYSTEM_PROMPT = """You are an interior-style classifier for a real-estate matching app.

Look at the reference image (which may be a real-estate photo, a magazine clip,
a Pinterest pin, or a mood board) and extract structured style preferences. Tag
ONLY what you can see clearly.

Use these CONTROLLED values:

- style_primary: the dominant aesthetic. ONE of:
    "modern" | "traditional" | "minimalist" | "industrial" |
    "scandinavian" | "classic" | "rustic" | "eclectic"

- style_secondary: optional secondary influence (same vocabulary), or null.

- light_level: "bright" | "average" | "dim"

- color_palette: "light" | "dark" | "warm" | "cool" | "mixed"

- outdoor_type: type of outdoor space VISIBLE in the image (or "none"):
    "none" | "balcony" | "terrace" | "garden" | "rooftop"

- floor_material: "wood" | "tile" | "stone" | "laminate" | "mixed" | "unknown"

- standout_features: array (0-6) of notable features VISIBLE. Use ONLY:
    "sea_view" | "river_view" | "city_view" | "garden_view" |
    "fireplace" | "exposed_beams" | "high_ceilings" | "open_kitchen" |
    "walk_in_closet" | "ensuite" | "double_height" | "sky_light" |
    "pool" | "parking" | "elevator"

- summary: a one-sentence plain-English description of the vibe, max 80 chars.

Return ONLY valid JSON in this shape, nothing else:

{
  "style_primary": "modern" | ...,
  "style_secondary": "modern" | ... | null,
  "light_level": "bright" | "average" | "dim",
  "color_palette": "light" | "dark" | "warm" | "cool" | "mixed",
  "outdoor_type": "none" | "balcony" | "terrace" | "garden" | "rooftop",
  "floor_material": "wood" | "tile" | "stone" | "laminate" | "mixed" | "unknown",
  "standout_features": ["sea_view", ...],
  "summary": "..."
}"""


@dataclass
class StyleResult:
    style_primary: Optional[str] = None
    style_secondary: Optional[str] = None
    light_level: Optional[str] = None
    color_palette: Optional[str] = None
    outdoor_type: Optional[str] = None
    floor_material: Optional[str] = None
    standout_features: List[str] = field(default_factory=list)
    summary: Optional[str] = None
    model: str = MODEL
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def extract_style_tags(
    image_bytes: bytes,
    media_type: str = "image/jpeg",
    client: Optional[anthropic.Anthropic] = None,
) -> StyleResult:
    """One Haiku vision call → structured style tags.

    image_bytes:  raw image data
    media_type:   image/jpeg | image/png | image/webp | image/gif
    """
    if not image_bytes:
        return StyleResult(error="empty_image")
    if media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        return StyleResult(error=f"unsupported_media_type: {media_type}")

    if client is None:
        client = anthropic.Anthropic()

    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(image_bytes).decode("ascii"),
            },
        },
        {"type": "text", "text": "Tag this reference image. Return only JSON."},
    ]

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        return StyleResult(error=f"api_error: {e}")

    raw = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return StyleResult(error=f"json_parse_failed: {raw[:200]}")

    summary = parsed.get("summary")
    if isinstance(summary, str):
        summary = summary[:120]
    else:
        summary = None

    return StyleResult(
        style_primary=_coerce(parsed.get("style_primary"), VALID_STYLES),
        style_secondary=_coerce(parsed.get("style_secondary"), VALID_STYLES),
        light_level=_coerce(parsed.get("light_level"), VALID_LIGHT),
        color_palette=_coerce(parsed.get("color_palette"), VALID_PALETTE),
        outdoor_type=_coerce(parsed.get("outdoor_type"), VALID_OUTDOOR),
        floor_material=_coerce(parsed.get("floor_material"), VALID_FLOOR),
        standout_features=_coerce_features(parsed.get("standout_features")),
        summary=summary,
    )
