"""
Natural-language → structured preferences extractor for the onboarding wizard.

User types something like:
    "modern 2-bed under 450k, balcony, willing to renovate"

This module asks Claude Haiku to fill the same preference shape the wizard
chips produce. Returns updated prefs (merged on top of any existing ones)
plus a one-sentence echo of what was understood, so the user can confirm.

Vocabulary stays in lockstep with frontend/src/lib/preferences.js.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from typing import Optional

import anthropic

from scorers.renovation_classifier import MODEL


VALID_STYLES = {
    "modern", "traditional", "minimalist", "industrial",
    "scandinavian", "classic", "rustic", "eclectic",
}
VALID_RENO = {"turnkey", "cosmetic", "full_renovation"}
VALID_PERSONA = {"rental_investor", "flipper", "home_buyer", "home_renter"}


SYSTEM_PROMPT = """You convert a real-estate seeker's free-text wish into a structured preference object for a Lisbon listings app.

Return ONLY valid JSON in this exact shape (omit a key if the user did not mention it — do NOT invent values):

{
  "persona":      "rental_investor" | "flipper" | "home_buyer" | "home_renter",
  "budget":       { "min": <eur int>, "max": <eur int> },
  "size":         { "min": <m² int>,  "max": <m² int> },
  "bedrooms_min": 0..4,
  "style":        "modern" | "traditional" | "minimalist" | "industrial" | "scandinavian" | "classic" | "rustic" | "eclectic",
  "outdoor_required": true | false,
  "max_renovation": "turnkey" | "cosmetic" | "full_renovation",
  "summary":      "one-sentence plain-English echo of what you understood"
}

Rules:
- Persona inference: "buy to let / rental yield" → rental_investor; "flip / renovate and sell" → flipper; "home for me to live in" → home_buyer; "renting / lease" → home_renter.
- Budget is in EUR. "450k" or "€450 000" both mean 450000. If only one bound, set just min OR max.
- Size is in m² (living area).
- "1 bedroom or more" → bedrooms_min: 1; "studio" → 0; "at least 3 bed" → 3.
- "willing to renovate / open to fixer-upper" → max_renovation: "full_renovation". "needs paint / cosmetic only" → "cosmetic". "move-in ready / no work" → "turnkey".
- "balcony / terrace / garden / rooftop / outdoor space" → outdoor_required: true.
- Style: pick the closest from the controlled list. "scandi/scandinavian" → "scandinavian". If unclear, omit.
- The "summary" field is REQUIRED — always include a short echo, even if other fields are sparse.
- No prose outside the JSON. No markdown fences."""


@dataclass
class ChatExtractResult:
    persona: Optional[str] = None
    budget: Optional[dict] = None        # {min?, max?}
    size: Optional[dict] = None
    bedrooms_min: Optional[int] = None
    style: Optional[str] = None
    outdoor_required: Optional[bool] = None
    max_renovation: Optional[str] = None
    summary: Optional[str] = None
    model: str = MODEL
    error: Optional[str] = None

    def to_dict(self) -> dict:
        # Only include set fields so the frontend can do a shallow merge.
        d = asdict(self)
        d.pop("model", None)
        d.pop("error", None)
        return {k: v for k, v in d.items() if v is not None}


def _clean(s: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip(), flags=re.MULTILINE).strip()


def _coerce_int(x):
    try:
        v = int(x)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def _coerce_range(raw):
    if not isinstance(raw, dict):
        return None
    out = {}
    if "min" in raw and raw["min"] is not None:
        v = _coerce_int(raw["min"])
        if v is not None:
            out["min"] = v
    if "max" in raw and raw["max"] is not None:
        v = _coerce_int(raw["max"])
        if v is not None:
            out["max"] = v
    return out or None


def extract_preferences(
    message: str,
    current_prefs: Optional[dict] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> ChatExtractResult:
    """One Haiku call → preference fields the user mentioned."""
    if not (message or "").strip():
        return ChatExtractResult(error="empty_message")

    if client is None:
        client = anthropic.Anthropic()

    user_text = message.strip()
    if current_prefs:
        # Give the model context so refinements work ("actually 3 bed, not 2").
        user_text += f"\n\n(Current preferences for context: {json.dumps(current_prefs)})"

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
    except Exception as e:
        return ChatExtractResult(error=f"api_error: {e}")

    raw = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )
    raw = _clean(raw)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ChatExtractResult(error=f"json_parse_failed: {raw[:200]}")

    persona = parsed.get("persona")
    if persona not in VALID_PERSONA:
        persona = None

    style = parsed.get("style")
    if style not in VALID_STYLES:
        style = None

    reno = parsed.get("max_renovation")
    if reno not in VALID_RENO:
        reno = None

    bedrooms = parsed.get("bedrooms_min")
    bedrooms = _coerce_int(bedrooms) if bedrooms is not None else None
    if bedrooms is not None and bedrooms > 4:
        bedrooms = 4  # vocab caps at 4+

    outdoor = parsed.get("outdoor_required")
    outdoor = bool(outdoor) if isinstance(outdoor, bool) else None

    summary = parsed.get("summary")
    if isinstance(summary, str):
        summary = summary[:160]
    else:
        summary = None

    return ChatExtractResult(
        persona=persona,
        budget=_coerce_range(parsed.get("budget")),
        size=_coerce_range(parsed.get("size")),
        bedrooms_min=bedrooms,
        style=style,
        outdoor_required=outdoor,
        max_renovation=reno,
        summary=summary,
    )
