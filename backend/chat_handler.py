"""
LLM-powered smart search chat endpoint.
Uses Claude Haiku with tool_use to translate natural language queries
into structured listing filters.
"""

import json
import logging
import os
from typing import Optional

import tornado.web
import anthropic

import database as db

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

NEIGHBORHOODS = [
    "Misericórdia", "Santa Maria Maior", "São Vicente", "Santo António",
    "Belém", "Ajuda", "Alcântara",
    "Parque das Nações", "Avenidas Novas", "Alvalade", "Areeiro",
    "Campo de Ourique", "Estrela", "Campolide", "Arroios", "Penha de França", "Beato",
    "Carnide", "Lumiar", "Santa Clara", "Olivais", "Benfica",
    "São Domingos de Benfica", "Marvila",
]

SYSTEM_PROMPT = f"""You are a helpful real estate search assistant for Lisbon, Portugal (Área Metropolitana de Lisboa).
You help users find property listings by translating their natural language requests into structured search filters.

Available neighborhoods/parishes: {json.dumps(NEIGHBORHOODS)}

When users describe what they're looking for, use the search_listings tool to find matching properties.
Always try to extract as many relevant filters as possible from the user's request.

Guidelines:
- Prices are in EUR. "under 300k" means max_price=300000. "around 500k" means min_price=450000, max_price=550000.
- Rooms use the Portuguese T-system: T0=studio, T1=1 bedroom, T2=2 bedrooms, etc. "2 bedroom" = rooms=2.
- listing_type is either "sale" or "rent". Default to "sale" unless the user mentions renting.
- For rent prices, monthly values. "under 1500/month" means max_price=1500.
- Match neighborhood names fuzzy — "parque nações" → "Parque das Nações", "belem" → "Belém".
- If the user asks about something you can't filter on (like "river view" or "modern"), acknowledge it and search with the filters you can apply, noting what you couldn't filter for.
- Keep responses concise and helpful. Present results clearly.
- When showing results, highlight key details: price, size, rooms, neighborhood, and price per sqm.
- If no results are found, suggest broadening the search criteria.
"""

SEARCH_TOOL = {
    "name": "search_listings",
    "description": "Search for real estate listings in Lisbon with structured filters. Returns matching property listings.",
    "input_schema": {
        "type": "object",
        "properties": {
            "listing_type": {
                "type": "string",
                "enum": ["sale", "rent"],
                "description": "Type of listing: 'sale' for buying, 'rent' for renting",
            },
            "neighborhood": {
                "type": "string",
                "description": "Parish/neighborhood name (must match one of the known neighborhoods)",
            },
            "min_price": {
                "type": "number",
                "description": "Minimum price in EUR",
            },
            "max_price": {
                "type": "number",
                "description": "Maximum price in EUR",
            },
            "min_sqm": {
                "type": "number",
                "description": "Minimum size in square meters",
            },
            "max_sqm": {
                "type": "number",
                "description": "Maximum size in square meters",
            },
            "rooms": {
                "type": "integer",
                "description": "Number of rooms (T-typology: 0=studio, 1=T1, 2=T2, etc.)",
            },
        },
        "required": [],
    },
}


def _execute_search(params: dict) -> dict:
    """Run the search against the database and return results."""
    listings = db.get_listings(
        neighborhood=params.get("neighborhood"),
        min_price=params.get("min_price"),
        max_price=params.get("max_price"),
        min_sqm=params.get("min_sqm"),
        max_sqm=params.get("max_sqm"),
        rooms=params.get("rooms"),
        listing_type=params.get("listing_type"),
        limit=20,
    )
    return {
        "count": len(listings),
        "filters_applied": {k: v for k, v in params.items() if v is not None},
        "listings": listings,
    }


def _get_client() -> Optional[anthropic.Anthropic]:
    """Get Anthropic client, returning None if no API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


class ChatHandler(tornado.web.RequestHandler):
    """POST /api/chat — LLM-powered smart search."""

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")

    def options(self):
        self.set_status(204)
        self.finish()

    def post(self):
        client = _get_client()
        if not client:
            self.set_status(500)
            self.write(json.dumps({
                "error": "ANTHROPIC_API_KEY not set. Export it as an environment variable."
            }))
            return

        try:
            body = json.loads(self.request.body)
        except (json.JSONDecodeError, TypeError):
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid JSON body"}))
            return

        user_message = body.get("message", "").strip()
        conversation_history = body.get("history", [])

        if not user_message:
            self.set_status(400)
            self.write(json.dumps({"error": "Empty message"}))
            return

        # Build messages list from history + new message
        messages = []
        for msg in conversation_history[-10:]:  # Keep last 10 messages
            messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })
        messages.append({"role": "user", "content": user_message})

        try:
            # First call — Claude may use tool or respond directly
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=[SEARCH_TOOL],
                messages=messages,
            )

            # Check if Claude wants to use a tool
            tool_results = None
            assistant_text = ""

            if response.stop_reason == "tool_use":
                # Extract tool call
                tool_block = None
                text_parts = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_block = block
                    elif block.type == "text":
                        text_parts.append(block.text)

                if tool_block and tool_block.name == "search_listings":
                    # Execute the search
                    search_results = _execute_search(tool_block.input)
                    tool_results = search_results

                    # Send results back to Claude for a natural language response
                    messages.append({
                        "role": "assistant",
                        "content": response.content,
                    })
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": json.dumps(search_results, ensure_ascii=False, default=str),
                        }],
                    })

                    followup = client.messages.create(
                        model=MODEL,
                        max_tokens=1024,
                        system=SYSTEM_PROMPT,
                        tools=[SEARCH_TOOL],
                        messages=messages,
                    )

                    for block in followup.content:
                        if block.type == "text":
                            assistant_text += block.text
            else:
                # Direct text response (no tool use)
                for block in response.content:
                    if block.type == "text":
                        assistant_text += block.text

            result = {
                "response": assistant_text,
                "listings": tool_results["listings"] if tool_results else [],
                "filters_applied": tool_results["filters_applied"] if tool_results else {},
                "count": tool_results["count"] if tool_results else 0,
            }
            self.write(json.dumps(result, ensure_ascii=False, default=str))

        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            self.set_status(502)
            self.write(json.dumps({"error": f"AI service error: {str(e)}"}))
        except Exception as e:
            logger.error(f"Chat error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({"error": str(e)}))
