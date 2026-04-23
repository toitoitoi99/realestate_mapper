"""
Compare two listings and generate per-listing pros/cons plus a recommendation.

Called by POST /api/compare-listings.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import anthropic

from scorers.renovation_classifier import MODEL


SYSTEM_PROMPT = """You are a Lisbon real-estate advisor. A user wants to compare two properties.

Given structured data for Listing A and Listing B (plus the user's persona/goals), respond with ONLY valid JSON in this exact shape:

{
  "a": {
    "positives": ["<short point>", ...],
    "negatives": ["<short point>", ...]
  },
  "b": {
    "positives": ["<short point>", ...],
    "negatives": ["<short point>", ...]
  },
  "recommendation": "<2-4 sentence plain-English recommendation naming which listing to prioritise and why, tailored to the user's persona>"
}

Rules:
- 2-4 bullet points per pros/cons list. Be specific and data-driven (cite prices, sizes, location).
- Tailor pros/cons to the user's persona (rental_investor cares about yield; flipper about margin; home_buyer about livability; home_renter about value/location).
- recommendation must name a winner (or explain why it's a tie) and reference the persona.
- No prose outside the JSON. No markdown fences."""


def _fmt_listing(label: str, l: dict) -> str:
    parts = [f"=== Listing {label} ==="]
    parts.append(f"ID: {l.get('id')} ({l.get('listing_type', 'sale')})")
    parts.append(f"Title: {l.get('title', '—')}")
    parts.append(f"Neighborhood: {l.get('neighborhood', '—')}, Parish: {l.get('parish', '—')}")
    parts.append(f"Price: €{l.get('price_amount', '—')} (€{l.get('price_per_sqm', '—')}/m²)")
    parts.append(f"Size: {l.get('size_sqm', '—')} m², Rooms: T{l.get('rooms', '—')}, Bedrooms: {l.get('bedrooms', '—')}, Bathrooms: {l.get('bathrooms', '—')}")
    parts.append(f"Floor: {l.get('floor', '—')}, Condition: {l.get('condition', '—')}, Type: {l.get('property_type', '—')}")
    if l.get('flip_score') is not None:
        parts.append(f"Flip score: {round(l['flip_score'])}/100")
    if l.get('rent_score') is not None:
        parts.append(f"Rental investor score: {round(l['rent_score'])}/100")
    if l.get('rarity_score') is not None:
        parts.append(f"Rarity score: {round(l['rarity_score'])}/100")
    chips = l.get('feature_chips')
    if chips:
        try:
            parsed = json.loads(chips) if isinstance(chips, str) else chips
            if parsed:
                parts.append(f"Features: {', '.join(str(c) for c in parsed[:8])}")
        except Exception:
            pass
    desc = l.get('description', '')
    if desc:
        parts.append(f"Description (excerpt): {desc[:400]}")
    return "\n".join(parts)


def _clean(s: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip(), flags=re.MULTILINE).strip()


def compare_listings(
    listing_a: dict,
    listing_b: dict,
    persona: Optional[str] = None,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    """Call Claude to compare two listings. Returns parsed result dict or raises."""
    if client is None:
        client = anthropic.Anthropic()

    user_text = _fmt_listing("A", listing_a) + "\n\n" + _fmt_listing("B", listing_b)
    if persona:
        user_text += f"\n\nUser persona: {persona}"

    resp = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )

    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    raw = _clean(raw)
    return json.loads(raw)
