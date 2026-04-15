"""
Renovation classifier.

Given a listing's photos (and optionally its description), classify the
property into one of three renovation-condition buckets using Claude vision:

    turnkey          — livable as-is; no renovation needed
    cosmetic         — cheap refresh (paint, fixtures); ~€200–500/m²
    full_renovation  — expensive rebuild (structure/systems); ~€1000–2500/m²

Returns a structured dict with class, confidence, evidence, and a rough
cost-per-m² estimate. Designed to be called once per listing and cached.

Usage:
    from scorers.renovation_classifier import classify_listing
    result = classify_listing(image_urls, description="...", price_per_sqm=4200)
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
import requests

# ---------- Configuration ------------------------------------------------

MODEL = "claude-haiku-4-5-20251001"   # Cheap + fast; good enough for this task
MAX_IMAGES = 6                         # Sample up to N images per listing
IMAGE_TIMEOUT = 15                     # Per-image download timeout (s)

VALID_CLASSES = {"turnkey", "cosmetic", "full_renovation"}

VALID_NEED_ITEMS = {
    "kitchen", "bathroom", "flooring", "walls_paint", "windows",
    "electrical", "plumbing", "roof", "structure", "facade",
    "ceiling", "doors",
}
VALID_NEED_SEVERITIES = {"minor", "cosmetic", "full"}


def _sanitize_needs(raw) -> List[dict]:
    """Filter model-returned needs to the controlled vocabulary."""
    out: List[dict] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item = str(entry.get("item", "")).lower().strip()
        sev = str(entry.get("severity", "")).lower().strip()
        if item in VALID_NEED_ITEMS and sev in VALID_NEED_SEVERITIES:
            out.append({"item": item, "severity": sev})
    return out

# Keywords in idealista descriptions (Portuguese) that strongly signal class
_HEAVY_RENO_KEYWORDS = [
    "para recuperar", "a recuperar", "necessita de obras", "para obras",
    "para restauro", "requer obras", "devoluto",
    "prédio devoluto", "total remodelação",
]
_TURNKEY_KEYWORDS = [
    "pronto a habitar", "remodelado", "totalmente remodelado",
    "novo", "recém renovado", "renovado",
]

# ---------- Result type --------------------------------------------------


@dataclass
class RenovationResult:
    renovation_class: str                          # 'turnkey' | 'cosmetic' | 'full_renovation'
    confidence: float                              # 0.0–1.0
    cost_estimate_eur_per_sqm: Optional[int]       # rough midpoint estimate
    evidence: List[str] = field(default_factory=list)   # bullets of what the model saw
    needs: List[dict] = field(default_factory=list)     # [{item, severity}, ...]
    text_hint: Optional[str] = None                # keyword match from description, if any
    images_used: int = 0
    model: str = MODEL
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- Text heuristic ----------------------------------------------


def detect_text_hint(description: Optional[str]) -> Optional[str]:
    """Look for PT keywords in the listing description."""
    if not description:
        return None
    text = description.lower()
    for kw in _HEAVY_RENO_KEYWORDS:
        if kw in text:
            return f"heavy_reno_keyword:{kw}"
    for kw in _TURNKEY_KEYWORDS:
        if kw in text:
            return f"turnkey_keyword:{kw}"
    return None


# ---------- Image download ----------------------------------------------


def _download_image(url: str) -> Optional[tuple[str, bytes]]:
    """Download image; return (media_type, bytes) or None on failure."""
    try:
        resp = requests.get(url, timeout=IMAGE_TIMEOUT)
        resp.raise_for_status()
    except Exception:
        return None

    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        # Guess from URL extension
        m = re.search(r"\.(jpe?g|png|webp|gif)(?:$|\?)", url, re.IGNORECASE)
        if not m:
            return None
        ext = m.group(1).lower().replace("jpg", "jpeg")
        ctype = f"image/{ext}"

    return ctype, resp.content


def _select_images(image_urls: List[str]) -> List[str]:
    """Pick up to MAX_IMAGES spread across the album (first photo is usually
    the hero shot; later ones tend to be bathrooms/kitchens)."""
    if len(image_urls) <= MAX_IMAGES:
        return image_urls
    # Evenly spaced sample, always include first and last
    step = len(image_urls) / MAX_IMAGES
    indices = sorted({int(i * step) for i in range(MAX_IMAGES)} | {0, len(image_urls) - 1})
    return [image_urls[i] for i in indices[:MAX_IMAGES]]


# ---------- Prompt ------------------------------------------------------


SYSTEM_PROMPT = """You are a real estate condition assessor specialising in Lisbon, Portugal residential properties.

Your job is to classify a property into ONE of three renovation buckets based on its photos:

1. "turnkey" — Livable as-is. May be old or dated, but kitchen and bathroom are functional, no visible structural damage, no exposed systems. A buyer could move in tomorrow. Estimated renovation cost: €0–150/m².

2. "cosmetic" — Needs a cheap refresh: paint, light fixtures, maybe a tile or appliance. Floors and walls are intact. Kitchen/bathroom are dated but functional. No structural issues. Estimated renovation cost: €200–500/m².

3. "full_renovation" — Needs a major rebuild. Look for: exposed brick/stone from fallen plaster, missing kitchen or bathroom, water damage, mould, collapsed ceilings, rotten wood, exposed wiring/pipes, empty shell, or listing described as "para recuperar" / "devoluto". Estimated renovation cost: €1000–2500/m².

Grade by the WORST room you see. One trashed bathroom in an otherwise nice flat means "cosmetic" or worse.

Return ONLY valid JSON matching this schema:
{
  "renovation_class": "turnkey" | "cosmetic" | "full_renovation",
  "confidence": 0.0 to 1.0,
  "cost_estimate_eur_per_sqm": integer,
  "evidence": ["short phrase describing what you saw", ...],
  "needs": [{"item": "<item>", "severity": "<severity>"}, ...]
}

Give 2-5 evidence bullets. Be specific about rooms and conditions you observe.

For "needs", list ONLY things that need attention — do NOT include things that are fine.
Use these controlled values:
  item:     "kitchen" | "bathroom" | "flooring" | "walls_paint" | "windows" |
            "electrical" | "plumbing" | "roof" | "structure" | "facade" |
            "ceiling" | "doors"
  severity: "minor"     — cheap cosmetic fix (paint, single tile, fixture)
          | "cosmetic"  — replacement of that element is recommended but not urgent
          | "full"      — that element must be fully replaced/rebuilt

Example: a livable flat with a dated kitchen might have:
  "needs": [{"item": "kitchen", "severity": "cosmetic"}, {"item": "walls_paint", "severity": "minor"}]

Do not include any text outside the JSON."""


# ---------- Main classifier ---------------------------------------------


def classify_listing(
    image_urls: List[str],
    description: Optional[str] = None,
    price_per_sqm: Optional[float] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> RenovationResult:
    """Classify a single listing.

    image_urls:       list of photo URLs (from listing.images JSON array)
    description:      optional listing description (used for text-based sanity check)
    price_per_sqm:    optional; not used by model but included in result for analysis
    client:           optional reusable Anthropic client
    """
    text_hint = detect_text_hint(description)

    if not image_urls:
        return RenovationResult(
            renovation_class="turnkey",
            confidence=0.0,
            cost_estimate_eur_per_sqm=None,
            evidence=[],
            text_hint=text_hint,
            images_used=0,
            error="no_images",
        )

    selected = _select_images(image_urls)
    downloaded: list[tuple[str, bytes]] = []
    for url in selected:
        d = _download_image(url)
        if d is not None:
            downloaded.append(d)

    if not downloaded:
        return RenovationResult(
            renovation_class="turnkey",
            confidence=0.0,
            cost_estimate_eur_per_sqm=None,
            evidence=[],
            text_hint=text_hint,
            images_used=0,
            error="all_image_downloads_failed",
        )

    # Build the vision message
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
    user_text = "Classify this property based on the photos above. Return only JSON."
    if description:
        snippet = description[:600].replace("\n", " ")
        user_text += f"\n\nFor context, the listing description says: \"{snippet}\""
    content.append({"type": "text", "text": user_text})

    if client is None:
        client = anthropic.Anthropic()

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        return RenovationResult(
            renovation_class="turnkey",
            confidence=0.0,
            cost_estimate_eur_per_sqm=None,
            evidence=[],
            text_hint=text_hint,
            images_used=len(downloaded),
            error=f"api_error: {e}",
        )

    raw = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()

    # Strip code fences if present
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return RenovationResult(
            renovation_class="turnkey",
            confidence=0.0,
            cost_estimate_eur_per_sqm=None,
            evidence=[],
            text_hint=text_hint,
            images_used=len(downloaded),
            error=f"json_parse_failed: {raw[:200]}",
        )

    rc = str(parsed.get("renovation_class", "")).lower()
    if rc not in VALID_CLASSES:
        return RenovationResult(
            renovation_class="turnkey",
            confidence=0.0,
            cost_estimate_eur_per_sqm=None,
            evidence=[],
            text_hint=text_hint,
            images_used=len(downloaded),
            error=f"invalid_class: {rc}",
        )

    return RenovationResult(
        renovation_class=rc,
        confidence=float(parsed.get("confidence", 0.0)),
        cost_estimate_eur_per_sqm=parsed.get("cost_estimate_eur_per_sqm"),
        evidence=list(parsed.get("evidence", []))[:5],
        needs=_sanitize_needs(parsed.get("needs")),
        text_hint=text_hint,
        images_used=len(downloaded),
    )


# ---------- DB persistence ----------------------------------------------


def save_result(
    conn: sqlite3.Connection,
    listing_id: int,
    result: RenovationResult,
    table: str = "sales",
) -> None:
    """Write a classification result to the DB. Skips if the result is an error
    (we don't want to poison the DB with 'turnkey' fallbacks from API failures)."""
    if result.error is not None:
        return
    if table not in {"sales", "rentals"}:
        raise ValueError(f"invalid table {table!r}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        f"""
        UPDATE {table}
        SET renovation_class = ?,
            renovation_confidence = ?,
            renovation_cost_estimate_eur_per_sqm = ?,
            renovation_evidence = ?,
            renovation_needs = ?,
            renovation_classified_at = ?,
            renovation_model = ?
        WHERE id = ?
        """,
        (
            result.renovation_class,
            result.confidence,
            result.cost_estimate_eur_per_sqm,
            json.dumps(result.evidence, ensure_ascii=False),
            json.dumps(result.needs, ensure_ascii=False),
            now,
            result.model,
            listing_id,
        ),
    )
    conn.commit()
