"""
imovirtual_playwright.py — Standalone imovirtual.com scraper using Python Playwright.

Setup (one-time):
    pip install playwright
    playwright install chromium

Run:
    python scrapers/imovirtual_playwright.py                  # Headless scrape
    python scrapers/imovirtual_playwright.py --max-pages 5    # Quick test run
    python scrapers/imovirtual_playwright.py --type rent      # Scrape rentals

The scraper is API-first: it pulls every listing from the embedded Next.js
`__NEXT_DATA__` JSON (and the GraphQL XHR as a safety-net) on each search
results page, so it never visits detail pages at all. This is ~40× fewer
requests than the old detail-page flow, which kept tripping CloudFront WAF.

Flow:
    1. Launch a fresh (non-persistent) headless Chrome context. CloudFront
       cookies pinned to a flagged profile were the main cause of the WAF
       403s in the old flow.
    2. For each search page 1..N: navigate, read `__NEXT_DATA__`, iterate
       `pageProps.data.searchAds.items` — each item has price, area, rooms,
       location hierarchy, images, tags, etc.
    3. Build a `Listing` per item, dedupe by source_id, upsert into SQLite.
    4. Rebuild neighborhood stats when done.

Tradeoff: per-listing lat/lon is not present in the search feed, so
imovirtual listings won't show exact pins on the map (they still carry
parish/neighborhood names for the sidebar and can be positioned at the
parish centroid downstream). Everything else — price, size, rooms, floor,
images, tags — comes through intact.
"""

import re
import sys
import json
import time
import random
import hashlib
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional

# Ensure backend root is on path when running directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from models import Listing, ScrapeRun

try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    Page = object
    BrowserContext = object
    PWTimeout = Exception

try:
    from playwright_stealth import Stealth
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://www.imovirtual.com"
DEFAULT_SEARCH = "https://www.imovirtual.com/pt/resultados/comprar/apartamento/lisboa/"
RENTAL_SEARCH = "https://www.imovirtual.com/pt/resultados/arrendar/apartamento/lisboa/"
DEFAULT_MAX_PAGES = 60
DEFAULT_MAX_ITEMS = 1500
MAX_IMAGES = 15
BLOCKED_RETRIES = 3
BLOCKED_WAIT = 8

# Delay between search-page visits (seconds). API-first mode issues one
# request per ~36 listings, so even generous delays finish the full run
# in a few minutes — we can afford to be polite.
MIN_DELAY = 3.0
MAX_DELAY = 6.0

PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile_imovirtual"

# ── Utilities ─────────────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    """Parse Portuguese-formatted number: '250.000' -> 250000.0
    Handles thousands separators (dot) vs decimal separators (comma).
    Only treats dot as thousands separator when the number has the pattern
    of grouped digits (e.g. '250.000' or '1.234.567'), not standalone decimals.
    """
    if v is None:
        return None
    try:
        s = str(v).strip()
        # Strip currency symbols and whitespace but keep digits, dots, commas, minus
        s = re.sub(r"[^\d.,\-]", "", s)
        if not s:
            return None
        # Portuguese thousands separator: dot used with groups of 3 digits
        # e.g. "250.000" or "1.234.567" — must have digits before the dot too
        if re.match(r'^\d{1,3}(\.\d{3})+([,]\d+)?$', s):
            # Definite thousands-separated format: remove dots, comma becomes decimal
            s = s.replace(".", "").replace(",", ".")
        else:
            # Standard: comma is decimal separator
            s = s.replace(",", ".")
        n = float(s)
        return n if n != 0 else None
    except (ValueError, TypeError):
        return None


def _coord(v) -> Optional[float]:
    if v is None:
        return None
    try:
        n = float(v)
        return n if -180 <= n <= 180 else None
    except (ValueError, TypeError):
        return None


def _int(v) -> Optional[int]:
    if v is None:
        return None
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


def _stringify(v) -> str:
    """Coerce a value that may be a dict/list/None/etc. into a safe string
    for hashing. Dicts/lists are treated as empty — hashing falls back to
    the other fields (price/size/city) which is the right thing to do when
    the address is an opaque object we can't parse."""
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return ""
    return str(v)


def _hash(address, city, price, size, source="imovirtual") -> str:
    addr = _stringify(address).lower().strip()
    price_r = str(round(price / 1000) * 1000) if price else ""
    size_r = str(round(size)) if size else ""
    raw = f"{addr}|{_stringify(city).lower()}|{price_r}|{size_r}|{source}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _hash_cross(address, city, price, size) -> str:
    """Source-agnostic hash for cross-site matching."""
    addr = _stringify(address).lower().strip()
    price_r = str(round(price / 1000) * 1000) if price else ""
    size_r = str(round(size)) if size else ""
    raw = f"{addr}|{_stringify(city).lower()}|{price_r}|{size_r}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _polite_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ── Enum decoders for the imovirtual search schema ───────────────────────────
#
# The GraphQL search feed returns rooms/floor/estate as string enums rather
# than numeric values. These mappings match the internal taxonomy used by
# olxgroup-verticalsre-atlas (same codebase powers OLX Group real-estate
# sites across EU). Values outside the map fall back to None and the
# downstream code handles that gracefully.

_ROOMS_ENUM = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
    "MORE": 11,
}

_FLOOR_ENUM = {
    "CELLAR": "-1",
    "GROUND": "0",
    "FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4",
    "FIFTH": "5", "SIXTH": "6", "SEVENTH": "7", "EIGHTH": "8",
    "NINTH": "9", "TENTH": "10",
    "ABOVE_TENTH": "10+",
    "GARRET": "attic",
}

_ESTATE_ENUM = {
    "FLAT": "apartment",
    "APARTMENT": "apartment",
    "HOUSE": "house",
    "VILLA": "house",
    "TERRAIN": "land",
    "OFFICE": "office",
    "COMMERCIAL_PROPERTY": "commercial",
    "GARAGE": "garage",
    "ROOM": "room",
}


def _parse_rooms(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) or None
    s = str(v).strip().upper()
    if s in _ROOMS_ENUM:
        return _ROOMS_ENUM[s]
    return _int(s)


def _parse_floor(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return _FLOOR_ENUM.get(s.upper(), s) or None


def _parse_estate(v) -> str:
    if v is None:
        return "apartment"
    s = str(v).strip().upper()
    return _ESTATE_ENUM.get(s) or "apartment"


# ── Search-item → Listing builder ─────────────────────────────────────────────

def _parse_location_hierarchy(location: dict) -> dict:
    """Pull district / council / parish / neighborhood names out of the
    `location.reverseGeocoding.locations` list, which is ordered from
    coarsest (district) to finest (neighborhood)."""
    out = {"district": None, "city": None, "parish": None, "neighborhood": None}
    if not isinstance(location, dict):
        return out

    rg = location.get("reverseGeocoding") or {}
    levels = rg.get("locations") or []
    for lvl in levels:
        if not isinstance(lvl, dict):
            continue
        name = lvl.get("name")
        kind = (lvl.get("locationLevel") or "").lower()
        if not name:
            continue
        if kind == "district":
            out["district"] = name
        elif kind == "council":
            out["city"] = name
        elif kind == "parish":
            out["parish"] = name
        elif kind == "neighborhood":
            out["neighborhood"] = name
    return out


def _build_listing_from_search_item(item: dict) -> Optional[Listing]:
    """Build a Listing directly from a searchAds.items entry in the Next.js
    page data. Returns None if the row is too broken to salvage."""
    if not isinstance(item, dict):
        return None

    slug = item.get("slug") or ""
    if not slug:
        return None
    url = f"{BASE_URL}/pt/anuncio/{slug}"
    source_id = _extract_source_id(url) or str(item.get("id") or "")
    if not source_id:
        return None

    # Transaction drives sale vs rent — trust the API over the caller hint.
    transaction = (item.get("transaction") or "").upper()
    if transaction == "RENT":
        listing_type = "rent"
        price_obj = item.get("rentPrice") or item.get("totalPrice") or {}
    else:
        listing_type = "sale"
        price_obj = item.get("totalPrice") or {}
    price = _num((price_obj or {}).get("value"))

    pps_obj = item.get("pricePerSquareMeter") or {}
    price_per_sqm = _num((pps_obj or {}).get("value"))
    size = _num(item.get("areaInSquareMeters"))
    # Re-derive pps if the API omitted it but we have both price + size
    if price and size and size > 0 and not price_per_sqm:
        price_per_sqm = round(price / size, 2)

    # terrainAreaInSquareMeters is land size for houses — keep it as "gross"
    # so the existing downstream plumbing has something to display.
    gross = _num(item.get("terrainAreaInSquareMeters"))
    if gross and size and gross == size:
        gross = None

    rooms = _parse_rooms(item.get("roomsNumber"))
    floor = _parse_floor(item.get("floorNumber"))
    property_type = _parse_estate(item.get("estate"))

    # Location hierarchy from reverseGeocoding. `location.address.city` is
    # actually the parish (imovirtual's taxonomy is inconsistent with
    # Portuguese administrative terms) — we override it from the
    # reverseGeocoding list below.
    location = item.get("location") or {}
    hier = _parse_location_hierarchy(location)

    address = None
    addr_parts = (location.get("address") or {}) if isinstance(location, dict) else {}
    street = addr_parts.get("street")
    if isinstance(street, dict):
        street_name = street.get("name")
        street_num = street.get("number")
        if street_name:
            address = f"{street_name} {street_num}".strip() if street_num else street_name

    city = hier.get("city") or "Lisboa"
    district = hier.get("district") or "Lisboa"
    parish = hier.get("parish")
    neighborhood = hier.get("neighborhood") or hier.get("parish")

    # Images: prefer large, fall back to medium
    images = []
    for img in (item.get("images") or [])[:MAX_IMAGES]:
        if not isinstance(img, dict):
            continue
        u = img.get("large") or img.get("medium") or img.get("url")
        if u and isinstance(u, str) and u.startswith("http"):
            images.append(u)

    # Feature chips from tags[].value (e.g. AIR_CONDITIONING, SEPARATE_KITCHEN)
    chips = []
    for t in (item.get("tags") or []):
        if isinstance(t, dict):
            val = t.get("value")
            if val:
                chips.append(str(val).replace("_", " ").title())

    description = item.get("shortDescription")
    if description:
        description = str(description)[:1000]

    title = item.get("title") or None

    status = "active"

    return Listing(
        source="imovirtual",
        source_id=source_id,
        url=url,
        listing_type=listing_type,
        status=status,
        price_amount=price,
        price_per_sqm=price_per_sqm,
        size_sqm=size,
        gross_area_sqm=gross,
        rooms=rooms,
        bedrooms=rooms,  # imovirtual search feed doesn't split them
        bathrooms=None,
        floor=floor,
        property_type=property_type,
        condition=None,
        title=title,
        address=address,
        postal_code=None,
        neighborhood=neighborhood,
        parish=parish,
        district=district,
        city=city,
        lat=None,
        lon=None,
        images=json.dumps(images) if images else None,
        hash_dedupe=_hash(address, city, price, size),
        hash_cross=_hash_cross(address, city, price, size),
        description=description,
        feature_chips=json.dumps(chips) if chips else None,
        scraped_at=datetime.utcnow(),
    )


# ── Blocked page detection ────────────────────────────────────────────────────

def _is_blocked(page: "Page") -> bool:
    """Check if we got a 403/Cloudflare challenge page."""
    try:
        content = page.content()
        if len(content) < 5000:
            return True
        # Cloudflare challenge indicators
        if "cf-challenge" in content or "Just a moment" in content:
            return True
        return False
    except Exception:
        return False


def _goto_with_retry(page: "Page", url: str, retries: int = BLOCKED_RETRIES) -> bool:
    """Navigate to URL, retrying if blocked. Returns True if page loaded."""
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
        except PWTimeout:
            log.warning(f"  Timeout loading {url} (attempt {attempt}/{retries})")
            if attempt < retries:
                time.sleep(BLOCKED_WAIT)
                continue
            return False

        if not _is_blocked(page):
            return True

        if attempt < retries:
            log.info(f"  Blocked (attempt {attempt}/{retries}), waiting {BLOCKED_WAIT}s...")
            time.sleep(BLOCKED_WAIT)
            try:
                page.reload(timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                if not _is_blocked(page):
                    return True
            except PWTimeout:
                pass
        else:
            log.warning(f"  Blocked after {retries} attempts: {url}")
    return False


def _load_known_listings(listing_type: str = 'sale') -> dict:
    """Load source_id -> price_amount for all imovirtual listings in the DB.
    Used to skip unchanged listings and detect price changes."""
    table = "rentals" if listing_type == "rent" else "sales"
    try:
        conn = db.get_connection()
        rows = conn.execute(
            f"SELECT source_id, price_amount FROM {table} WHERE source='imovirtual'"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


# ── Cookie banner ─────────────────────────────────────────────────────────────

def accept_cookies(page: Page):
    """Dismiss the cookie/GDPR banner if present."""
    selectors = [
        "button#onetrust-accept-btn-handler",
        "button:has-text('Aceitar')",
        "button:has-text('Aceitar tudo')",
        "button:has-text('Accept')",
        "button:has-text('Agree')",
        "[data-cy='cookie-consent-accept']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click(timeout=2000)
                page.wait_for_timeout(600)
                log.info("Cookie banner dismissed")
                return
        except PWTimeout:
            continue


# ── __NEXT_DATA__ extraction ──────────────────────────────────────────────────

def extract_next_data(page: Page) -> dict:
    """Pull the embedded Next.js JSON payload from the page."""
    try:
        raw = page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }
        """)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def find_deep(obj, *paths):
    """Try multiple key paths in a nested dict; return first hit."""
    for path in paths:
        cur = obj
        for key in path:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(key)
        if cur is not None:
            return cur
    return None


# ── Search results: collect detail URLs ───────────────────────────────────────

def extract_search_listings(page: Page) -> list:
    """Extract listing URLs and preview prices from a search results page.
    Returns list of dicts: {"url": str, "price": float|None}."""
    try:
        items = page.evaluate("""
            () => {
                const results = [];
                // Each listing card — try multiple selector patterns
                const cards = document.querySelectorAll(
                    'a[data-cy="listing-item-link"], ' +
                    'article a[href*="/anuncio/"], ' +
                    'li a[href*="/anuncio/"], ' +
                    'a[href*="/pt/anuncio/"]'
                );
                const seen = new Set();
                for (const a of cards) {
                    const href = a.href;
                    if (!href.includes('/anuncio/') || seen.has(href)) continue;
                    seen.add(href);

                    // Try to grab the price from the card
                    let priceText = null;
                    // Look within the card (or its parent article) for a price element
                    const card = a.closest('article, li, [data-cy="search.listing"]') || a;
                    const priceEl = card.querySelector(
                        '[data-cy="listing-item-price"], ' +
                        'span[class*="Price"], ' +
                        'strong[class*="price"], ' +
                        'p[data-testid="ad-price"]'
                    );
                    if (priceEl) priceText = priceEl.textContent.trim();

                    results.push({ url: href, priceText: priceText });
                }
                return results;
            }
        """)
        # Parse prices
        for item in items:
            item["price"] = _num(item.pop("priceText", None))
        return items
    except Exception:
        return []


def _extract_source_id(url: str) -> str:
    """Extract the imovirtual listing ID from URL.
    URLs look like: /pt/anuncio/description-text-ID1gkrg
    """
    m = re.search(r'-ID([a-zA-Z0-9]+)(?:\?|$|/)', url)
    if m:
        return m.group(1)
    # Fallback: hash the URL
    return hashlib.sha1(url.encode()).hexdigest()[:16]


# ── Detail page extraction from __NEXT_DATA__ ────────────────────────────────

def extract_listing_from_next_data(page_props: dict) -> dict:
    """Extract listing fields from __NEXT_DATA__ pageProps."""
    ad = (
        find_deep(page_props, ["ad"], ["adDetail"], ["listing"]) or
        page_props
    )

    # Price
    price = _num(
        find_deep(ad, ["target", "Price"], ["price", "value"]) or
        ad.get("price") or
        find_deep(ad, ["characteristics", "price"])
    )

    # Size
    size = _num(
        find_deep(ad, ["target", "Area"]) or
        find_deep(ad, ["characteristics", "m"]) or
        ad.get("area") or ad.get("m")
    )
    gross_area = _num(
        find_deep(ad, ["target", "GrossArea"]) or
        ad.get("grossArea")
    )
    if gross_area and size and gross_area == size:
        gross_area = None

    # Rooms (typology)
    rooms_raw = (
        find_deep(ad, ["target", "Rooms_num"]) or
        find_deep(ad, ["characteristics", "rooms_num"]) or
        ad.get("rooms_num")
    )
    rooms = _int(rooms_raw)

    bedrooms = _int(
        find_deep(ad, ["target", "Bedrooms_num"]) or
        ad.get("bedrooms")
    ) or rooms

    bathrooms = _int(
        find_deep(ad, ["target", "Bathrooms_num"]) or
        ad.get("bathrooms")
    )

    floor_raw = (
        find_deep(ad, ["target", "Floor_no"]) or
        ad.get("floor_no")
    )
    floor = str(floor_raw) if floor_raw is not None else None

    condition = _normalize_condition(
        find_deep(ad, ["target", "Building_condition"]) or
        ad.get("condition")
    )
    property_type = _normalize_property_type(
        find_deep(ad, ["target", "ProperType"]) or
        find_deep(ad, ["target", "Type"]) or
        ad.get("propertyType")
    )

    # Location
    title = ad.get("title") or ad.get("subject")
    description = ad.get("description")
    if description:
        description = str(description)[:1000]

    location = find_deep(ad, ["location"]) or {}
    address_parts = find_deep(ad, ["location", "address"]) or {}

    def _addr_text(v):
        """Unwrap an address candidate that may be a dict (imovirtual sometimes
        wraps street/name as {"name": "...", ...}) into a plain string."""
        if v is None:
            return None
        if isinstance(v, dict):
            return v.get("name") or v.get("street") or v.get("value") or None
        if isinstance(v, list):
            for item in v:
                t = _addr_text(item)
                if t:
                    return t
            return None
        s = str(v).strip()
        return s or None

    # Build address from location hierarchy
    address = (
        _addr_text(address_parts.get("street")) or
        _addr_text(address_parts.get("name")) or
        _addr_text(ad.get("address"))
    )

    # Navigate location hierarchy: country > region > subregion > city > district
    city_obj = find_deep(location, ["address", "city"]) or {}
    district_obj = find_deep(location, ["address", "county"]) or {}
    region_obj = find_deep(location, ["address", "province"]) or {}

    city = city_obj.get("name") if isinstance(city_obj, dict) else str(city_obj) if city_obj else None
    neighborhood = district_obj.get("name") if isinstance(district_obj, dict) else str(district_obj) if district_obj else None
    district = region_obj.get("name") if isinstance(region_obj, dict) else str(region_obj) if region_obj else None

    # Try flat location fields
    if not city:
        city = ad.get("city") or ad.get("municipality")
    if not district:
        district = ad.get("region") or ad.get("district")

    postal_code = address_parts.get("postalCode") or ad.get("postalCode")

    # Coordinates
    coords = find_deep(ad, ["location", "coordinates"]) or find_deep(ad, ["coordinates"]) or {}
    lat = _coord(coords.get("latitude") or ad.get("latitude") or ad.get("lat"))
    lon = _coord(coords.get("longitude") or ad.get("longitude") or ad.get("lon"))

    # Try map data
    if not lat:
        map_data = find_deep(ad, ["location", "mapDetails"]) or {}
        lat = _coord(map_data.get("latitude"))
        lon = _coord(map_data.get("longitude"))

    # Images
    images = []
    imgs_raw = ad.get("images") or ad.get("photos") or ad.get("gallery") or []
    if isinstance(imgs_raw, list):
        for img in imgs_raw:
            if isinstance(img, str):
                images.append(img)
            elif isinstance(img, dict):
                # Try various image URL keys
                url = (
                    img.get("large") or img.get("medium") or
                    img.get("url") or img.get("src") or
                    img.get("link") or img.get("original")
                )
                if url:
                    images.append(url)
    images = [u for u in images if u and u.startswith("http")][:MAX_IMAGES]

    # Structured feature chips from __NEXT_DATA__
    chips: list = []

    def _append_chip(v):
        if v is None:
            return
        if isinstance(v, bool):
            return  # booleans collapse without context — skip
        if isinstance(v, (str, int, float)):
            s = str(v).strip()
            if s:
                chips.append(s)

    # `target` is a dict of feature keys → value (e.g. Elevator: "y", Air_conditioning: "y")
    target = find_deep(ad, ["target"]) or {}
    if isinstance(target, dict):
        for k, v in target.items():
            # Positive string flags like "y" / "yes" → emit the key as a chip
            if isinstance(v, str) and v.lower() in ("y", "yes", "sim", "true", "1"):
                chips.append(str(k).replace("_", " "))
            elif isinstance(v, list):
                for item in v:
                    _append_chip(item)
    # Some imovirtual responses put amenities in `features`, `extras`, or `tags`
    for key in ("features", "extras", "amenities", "tags", "characteristics"):
        raw = ad.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    _append_chip(item)
                elif isinstance(item, dict):
                    label = item.get("label") or item.get("name") or item.get("text") or item.get("value")
                    _append_chip(label)

    return {
        "price": price,
        "size": size,
        "gross_area": gross_area,
        "rooms": rooms,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "floor": floor,
        "condition": condition,
        "property_type": property_type,
        "title": title,
        "address": address,
        "postal_code": postal_code,
        "neighborhood": neighborhood,
        "parish": None,
        "city": city,
        "district": district,
        "lat": lat,
        "lon": lon,
        "images": images,
        "description": description,
        "feature_chips": [c for c in chips if c] or None,
    }


# ── DOM-based extraction (fallback) ──────────────────────────────────────────

def extract_from_dom(page: Page) -> dict:
    """Extract listing data from DOM elements using data-cy and aria-label selectors."""
    result = {}

    try:
        data = page.evaluate(r"""
            () => {
                const out = {};

                // Title
                const titleEl = document.querySelector(
                    'h1[data-cy="adPageAdTitle"], h1.css-1wnihf6, h1'
                );
                if (titleEl) out.title = titleEl.textContent.trim();

                // Price
                const priceEl = document.querySelector(
                    'strong[data-cy="adPageHeaderPrice"], ' +
                    '[aria-label="Preco"] strong, ' +
                    '[data-testid="ad-price"]'
                );
                if (priceEl) out.priceText = priceEl.textContent.trim();

                // Price per sqm
                const ppsEl = document.querySelector(
                    "div[aria-label='Preco por metro quadrado'], " +
                    "[data-testid='price-per-m']"
                );
                if (ppsEl) out.pricePerSqm = ppsEl.textContent.trim();

                // Area
                const areaEl = document.querySelector(
                    "div[aria-label='Area util (m2)'] > div:nth-of-type(2), " +
                    "div[aria-label='Area util'] > div:nth-of-type(2)"
                );
                if (areaEl) out.area = areaEl.textContent.trim();

                // Gross area
                const grossEl = document.querySelector(
                    "div[aria-label='Area bruta (m2)'] > div:nth-of-type(2), " +
                    "div[aria-label='Area bruta'] > div:nth-of-type(2)"
                );
                if (grossEl) out.grossArea = grossEl.textContent.trim();

                // Rooms/Typology
                const roomsEl = document.querySelector(
                    "div[aria-label='Tipologia'] > div:nth-of-type(2)"
                );
                if (roomsEl) out.rooms = roomsEl.textContent.trim();

                // Bathrooms
                const bathEl = document.querySelector(
                    "div[aria-label='Casas de Banho'] > div:nth-of-type(2), " +
                    "div[aria-label='Casas de banho'] > div:nth-of-type(2)"
                );
                if (bathEl) out.bathrooms = bathEl.textContent.trim();

                // Condition
                const condEl = document.querySelector(
                    "div[aria-label='Condicao'] > div:nth-of-type(2), " +
                    "div[aria-label='Estado'] > div:nth-of-type(2)"
                );
                if (condEl) out.condition = condEl.textContent.trim();

                // Floor
                const floorEl = document.querySelector(
                    "div[aria-label='Andar'] > div:nth-of-type(2), " +
                    "div[aria-label='Piso'] > div:nth-of-type(2)"
                );
                if (floorEl) out.floor = floorEl.textContent.trim();

                // Address
                const addrEl = document.querySelector(
                    "div[aria-label='Endereco'] a, " +
                    "a[aria-label='Endereco'], " +
                    "[data-cy='adPageAdLocation']"
                );
                if (addrEl) out.address = addrEl.textContent.trim();

                // Location breadcrumb
                const breadcrumbs = document.querySelectorAll(
                    'nav[aria-label="breadcrumb"] a, [data-cy="breadcrumb"] a'
                );
                out.breadcrumbs = Array.from(breadcrumbs).map(a => a.textContent.trim());

                // Description
                const descEl = document.querySelector(
                    '[data-cy="adPageAdDescription"], ' +
                    'div[data-testid="content-text"]'
                );
                if (descEl) out.description = descEl.textContent.trim().slice(0, 1000);

                // Images
                const imgs = document.querySelectorAll(
                    'picture img[src*="img.otodomcdn"], ' +
                    'picture img[src*="ireland.apollo"], ' +
                    'img[data-cy="gallery-picture"], ' +
                    '[data-cy="adPageGallery"] img'
                );
                out.images = Array.from(imgs)
                    .map(i => i.src || i.dataset.src || '')
                    .filter(s => s.startsWith('http'));

                // Coordinates from map iframe or data attributes
                const mapEl = document.querySelector(
                    '[data-cy="adPageMap"], [data-testid="map"]'
                );
                if (mapEl) {
                    const lat = mapEl.dataset.lat || mapEl.getAttribute('data-lat');
                    const lon = mapEl.dataset.lon || mapEl.getAttribute('data-lon') ||
                                mapEl.dataset.lng || mapEl.getAttribute('data-lng');
                    if (lat) out.lat = lat;
                    if (lon) out.lon = lon;
                }

                // Structured characteristic chips — every div with an aria-label
                // and a sibling value div (imovirtual's "Características" section).
                // Also pick up extras/media chips that render as list items.
                const chipSet = new Set();
                const chips = [];
                const pushChip = (s) => {
                    if (!s) return;
                    const v = s.replace(/\s+/g, ' ').trim();
                    if (!v || v.length > 120) return;
                    const key = v.toLowerCase();
                    if (chipSet.has(key)) return;
                    chipSet.add(key);
                    chips.push(v);
                };
                document.querySelectorAll('div[aria-label]').forEach(el => {
                    const label = (el.getAttribute('aria-label') || '').trim();
                    if (!label) return;
                    const valueEl = el.querySelector(':scope > div:nth-of-type(2)');
                    const value = valueEl ? valueEl.textContent.trim() : '';
                    if (value) pushChip(label + ': ' + value);
                });
                document.querySelectorAll(
                    '[data-cy="adPageAdFeaturesListItem"], ' +
                    '[data-testid="ad-features-list-item"], ' +
                    '[class*="Features"] li, ' +
                    '[class*="features"] li, ' +
                    '[data-cy*="feature"] li, ' +
                    '[data-testid*="feature"] li'
                ).forEach(el => pushChip(el.textContent));
                out.chips = chips;

                return out;
            }
        """)
    except Exception as e:
        log.warning(f"  DOM extraction JS failed: {e}")
        return result

    # Parse extracted data
    if data.get("priceText"):
        result["price"] = _num(data["priceText"])

    if data.get("area"):
        result["size"] = _num(data["area"])
    if data.get("grossArea"):
        result["gross_area"] = _num(data["grossArea"])

    if data.get("rooms"):
        rooms = _int(data["rooms"])
        result["rooms"] = rooms
        result["bedrooms"] = rooms

    if data.get("bathrooms"):
        result["bathrooms"] = _int(data["bathrooms"])

    if data.get("floor"):
        f = data["floor"]
        if f.lower() in ("r/c", "rés-do-chão", "res do chao"):
            result["floor"] = "0"
        else:
            result["floor"] = str(_int(f)) if _int(f) is not None else f

    if data.get("condition"):
        result["condition"] = _normalize_condition(data["condition"])

    result["title"] = data.get("title")
    result["description"] = data.get("description")
    result["address"] = data.get("address")

    # Location from breadcrumbs: typically [Home, District, City, Neighborhood]
    breadcrumbs = data.get("breadcrumbs") or []
    if len(breadcrumbs) >= 3:
        result["district"] = breadcrumbs[1] if len(breadcrumbs) > 1 else None
        result["city"] = breadcrumbs[2] if len(breadcrumbs) > 2 else None
        result["neighborhood"] = breadcrumbs[3] if len(breadcrumbs) > 3 else None

    if data.get("lat"):
        result["lat"] = _coord(data["lat"])
    if data.get("lon"):
        result["lon"] = _coord(data["lon"])

    if data.get("images"):
        result["images"] = data["images"][:MAX_IMAGES]

    raw_chips = [str(c).strip() for c in (data.get("chips") or []) if c and str(c).strip()]
    if raw_chips:
        result["feature_chips"] = raw_chips

    # Property type from title
    title = (result.get("title") or "").lower()
    for pt in ["apartamento", "moradia", "vivenda", "loja", "terreno",
               "escritório", "garagem", "armazém", "prédio"]:
        if pt in title:
            result["property_type"] = _normalize_property_type(pt)
            break

    return result


# ── Detail page scraper ───────────────────────────────────────────────────────

def scrape_detail_page(page: Page, url: str, listing_type: str = 'sale') -> Optional[Listing]:
    """Visit a listing detail page and return a Listing object."""
    from models import detect_listing_type
    listing_type = detect_listing_type(url, fallback=listing_type)

    source_id = _extract_source_id(url)

    # Pull __NEXT_DATA__
    next_data = extract_next_data(page)
    page_props = find_deep(next_data, ["props", "pageProps"], ["pageProps"]) or {}

    nd = extract_listing_from_next_data(page_props)

    # DOM fallback if __NEXT_DATA__ didn't yield a price
    dom = extract_from_dom(page) if not nd.get("price") else {}

    price      = nd.get("price")      or dom.get("price")
    size       = nd.get("size")       or dom.get("size")
    gross_area = nd.get("gross_area") or dom.get("gross_area")
    lat        = nd.get("lat")        or dom.get("lat")
    lon        = nd.get("lon")        or dom.get("lon")
    address    = nd.get("address")    or dom.get("address")
    postal     = nd.get("postal_code")
    city       = nd.get("city")       or dom.get("city") or "Lisboa"
    district   = nd.get("district")   or dom.get("district") or "Lisboa"
    bedrooms   = nd.get("bedrooms")   or dom.get("bedrooms")
    floor      = nd.get("floor")      or dom.get("floor")
    images     = nd.get("images")     or dom.get("images") or []

    price_per_sqm = round(price / size, 2) if price and size and size > 0 else None

    # Merge chips from __NEXT_DATA__ and DOM, dedupe case-insensitively
    merged_chips = []
    seen_chip_keys = set()
    for source_chips in (nd.get("feature_chips") or [], dom.get("feature_chips") or []):
        for c in source_chips:
            if not c:
                continue
            k = str(c).strip().lower()
            if not k or k in seen_chip_keys:
                continue
            seen_chip_keys.add(k)
            merged_chips.append(str(c).strip())

    return Listing(
        source="imovirtual",
        source_id=source_id,
        url=url,
        listing_type=listing_type,
        status='active',
        price_amount=price,
        price_per_sqm=price_per_sqm,
        size_sqm=size,
        gross_area_sqm=gross_area,
        rooms=nd.get("rooms") or dom.get("rooms"),
        bedrooms=bedrooms,
        bathrooms=nd.get("bathrooms") or dom.get("bathrooms"),
        floor=floor,
        property_type=nd.get("property_type") or dom.get("property_type"),
        condition=nd.get("condition") or dom.get("condition"),
        title=nd.get("title") or dom.get("title"),
        address=address,
        postal_code=postal,
        neighborhood=nd.get("neighborhood") or dom.get("neighborhood"),
        parish=nd.get("parish"),
        district=district,
        city=city,
        lat=lat,
        lon=lon,
        images=json.dumps(images) if images else None,
        hash_dedupe=_hash(address, city, price, size),
        hash_cross=_hash_cross(address, city, price, size),
        description=nd.get("description") or dom.get("description"),
        feature_chips=json.dumps(merged_chips) if merged_chips else None,
        scraped_at=datetime.utcnow(),
    )


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _normalize_property_type(raw) -> str:
    if not raw:
        return "apartment"
    s = str(raw).lower()
    if any(w in s for w in ["moradia", "vivenda", "house", "villa"]):  return "house"
    if any(w in s for w in ["estúdio", "studio", "t0"]):              return "studio"
    if "loft" in s:                                                     return "loft"
    if "duplex" in s:                                                   return "duplex"
    if any(w in s for w in ["penthouse", "cobertura"]):                return "penthouse"
    return "apartment"


def _normalize_condition(raw) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).lower()
    if any(w in s for w in ["new", "novo", "new_development"]):        return "new"
    if any(w in s for w in ["renov", "remodel", "rehabilit"]):         return "renovated"
    if any(w in s for w in ["bom estado", "good", "usado", "used"]):   return "used"
    if any(w in s for w in ["para recuperar", "to_renovate", "recover"]): return "to_renovate"
    if any(w in s for w in ["em construção", "under_construction"]):    return "new"
    return "used"


# ── Pagination ────────────────────────────────────────────────────────────────

def build_page_url(base_url: str, page_num: int) -> str:
    if page_num == 1:
        return base_url
    # Use query parameter for pagination
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}page={page_num}"


# ── Main scraper ──────────────────────────────────────────────────────────────

def _extract_items_from_next_data(page: "Page") -> tuple:
    """Return (items_list, total_pages) from the current page's
    `__NEXT_DATA__`. items_list may be empty if the feed changed shape."""
    try:
        raw = page.evaluate(
            "() => { const el = document.getElementById('__NEXT_DATA__'); "
            "return el ? el.textContent : null; }"
        )
    except Exception:
        return [], None
    if not raw:
        return [], None
    try:
        data = json.loads(raw)
    except Exception:
        return [], None

    pp = (
        data.get("props", {}).get("pageProps", {})
        if isinstance(data, dict) else {}
    )
    search_ads = ((pp.get("data") or {}).get("searchAds")) or {}
    items = search_ads.get("items") or []
    pagination = search_ads.get("pagination") or {}
    total_pages = pagination.get("totalPages")
    return items, total_pages


def run_scraper(
    search_url: str = DEFAULT_SEARCH,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_items: int = DEFAULT_MAX_ITEMS,
    headless: bool = True,
    listing_type: str = 'sale',
):
    if not _PLAYWRIGHT_AVAILABLE:
        print("Playwright is not installed.")
        print("Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    db.init_db()
    run_id = db.start_scrape_run("imovirtual")

    new_count = updated_count = error_count = 0
    skipped_known = 0
    total_pushed = 0

    # Load already-scraped source_ids with prices so we can skip unchanged ones
    known_listings = _load_known_listings(listing_type)
    log.info(f"Loaded {len(known_listings)} known listings from DB")

    # API-first collection: every listing comes from the embedded Next.js
    # `__NEXT_DATA__` on each search page. No detail-page round-trip.
    all_listings = []      # type: list[Listing]
    seen_source_ids = set()  # type: set[str]

    with sync_playwright() as pw:
        # CRITICAL: fresh (non-persistent) context. The old flow used a
        # persistent Chrome profile, which baked a CloudFront "flagged"
        # cookie after the first block and propagated it to every
        # subsequent run — 100% 403. A fresh context starts clean.
        browser = pw.chromium.launch(
            channel="chrome",
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="pt-PT",
            timezone_id="Europe/Lisbon",
            extra_http_headers={
                "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        page = context.new_page()

        if _STEALTH_AVAILABLE:
            Stealth().use_sync(page)
            log.info("Stealth mode active")
        else:
            log.warning("playwright-stealth not installed — bot detection may block scraping")

        # Safety-net XHR capture: imovirtual's GraphQL endpoint for the
        # search feed. If pagination ever starts hydrating client-side
        # and `__NEXT_DATA__` stops containing the items list, we can
        # still pick them up from the response body. The same technique
        # is used in era_playwright.py.
        xhr_items = []  # list[dict]

        def _on_response(resp):
            try:
                url = resp.url
                if "/graphql" not in url and "searchAds" not in url:
                    return
                if resp.request.method not in ("POST", "GET"):
                    return
                body = resp.json()
            except Exception:
                return
            try:
                def walk(o):
                    # Find any list containing dicts with estate/transaction
                    if isinstance(o, dict):
                        if o.get("__typename") == "AdvertListItem":
                            xhr_items.append(o)
                            return
                        for v in o.values():
                            walk(v)
                    elif isinstance(o, list):
                        for v in o:
                            walk(v)
                walk(body)
            except Exception as e:
                log.debug(f"  XHR parse error: {e}")

        page.on("response", _on_response)

        # Warm-up visit to the homepage to pick up cookies before hitting
        # the search URL. Helps stay out of the aggressive "direct-to-deep"
        # heuristic in CloudFront.
        log.info("Warming up session on imovirtual.com...")
        try:
            page.goto(BASE_URL, timeout=30000, wait_until="domcontentloaded")
            accept_cookies(page)
            _polite_delay()
        except Exception as e:
            log.warning(f"  Warm-up visit failed: {e}")

        blocked_count = 0
        total_pages_api = None

        for page_num in range(1, max_pages + 1):
            if len(all_listings) >= max_items:
                log.info(
                    f"Collected enough listings ({len(all_listings)} >= "
                    f"max_items={max_items}). Stopping pagination."
                )
                break

            page_url = build_page_url(search_url, page_num)
            log.info(f"Search page {page_num}: {page_url}")

            if not _goto_with_retry(page, page_url):
                blocked_count += 1
                if blocked_count >= 3:
                    log.warning(
                        "Blocked on 3 search pages in a row — giving up "
                        "to avoid wasting time. Try again later."
                    )
                    break
                continue
            blocked_count = 0

            # Give the page a moment for any client-side hydration.
            page.wait_for_timeout(800)

            items, total_pages_new = _extract_items_from_next_data(page)
            if total_pages_new and total_pages_api is None:
                total_pages_api = total_pages_new
                log.info(
                    f"  API reports {total_pages_api} total pages "
                    f"(~{total_pages_api * 36} listings)"
                )

            # Merge any XHR-captured items discovered during this navigation
            # that weren't already in __NEXT_DATA__.
            if xhr_items:
                seen_ids_in_batch = {it.get("id") for it in items if isinstance(it, dict)}
                for xhr in xhr_items:
                    if xhr.get("id") not in seen_ids_in_batch:
                        items.append(xhr)
                xhr_items.clear()

            added = 0
            for raw_item in items:
                listing = _build_listing_from_search_item(raw_item)
                if listing is None or not listing.source_id:
                    continue
                if listing.source_id in seen_source_ids:
                    continue
                seen_source_ids.add(listing.source_id)
                all_listings.append(listing)
                added += 1

            log.info(
                f"  Page {page_num}: +{added} listings "
                f"(total {len(all_listings)})"
            )

            if added == 0:
                log.info(
                    "  No new items — likely end of results. Stopping pagination."
                )
                break

            # Stop at the API-reported total page count
            if total_pages_api and page_num >= total_pages_api:
                log.info("  Reached last page reported by API.")
                break

            _polite_delay()

        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass

        context.close()
        browser.close()

    log.info(f"Total listings collected from search feed: {len(all_listings)}")

    # ── Filter by requested listing_type and upsert ─────────────────────────
    filtered = [li for li in all_listings if li.listing_type == listing_type]
    # Scope seen_source_ids to the requested type so missing-detection
    # doesn't get confused when a sale search accidentally harvests a
    # rental or vice-versa.
    seen_source_ids = {li.source_id for li in filtered if li.source_id}
    log.info(
        f"Upserting {len(filtered)} {listing_type} listings "
        f"(skipped {len(all_listings) - len(filtered)} of the other type)"
    )

    for i, listing in enumerate(filtered):
        if total_pushed >= max_items:
            log.info(f"Reached max_items={max_items}. Stopping.")
            break
        sid = listing.source_id
        if listing.price_amount is None:
            log.warning(f"  [{i+1}/{len(filtered)}] {sid}: no price — skipping")
            error_count += 1
            continue
        # Price-unchanged skip for known listings
        if sid in known_listings:
            old_price = known_listings[sid]
            new_price = listing.price_amount
            if old_price and new_price and abs(new_price - old_price) < 1:
                skipped_known += 1
                # We still count it as "seen" so missing-detection works
                continue
        try:
            _, is_new = db.upsert_listing(listing)
            if is_new:
                new_count += 1
            else:
                updated_count += 1
            total_pushed += 1
            if (i + 1) % 25 == 0 or (i + 1) == len(filtered):
                log.info(
                    f"  [{i+1}/{len(filtered)}] OK "
                    f"{listing.neighborhood or listing.parish or 'unknown'} "
                    f"T{listing.rooms} "
                    f"EUR{listing.price_amount:,.0f} "
                    f"({listing.size_sqm}m2)"
                )
        except Exception as e:
            log.warning(f"  [{i+1}/{len(filtered)}] {sid}: upsert error: {e}")
            error_count += 1

    # ── Detect missing listings ──────────────────────────────────────────────
    if len(known_listings) > 0 and len(seen_source_ids) < len(known_listings) * 0.5:
        log.warning(f"Only saw {len(seen_source_ids)}/{len(known_listings)} listings — skipping missing detection (possible block)")
    else:
        missing_result = db.process_missing_listings(
            source="imovirtual", listing_type=listing_type,
            seen_source_ids=seen_source_ids, run_id=run_id,
        )
        log.info(f"Missing detection: {missing_result}")

    # ── Rebuild stats and close run ───────────────────────────────────────────
    db.rebuild_neighborhoods()

    run = ScrapeRun(
        source="imovirtual",
        listings_found=total_pushed,
        listings_new=new_count,
        listings_updated=updated_count,
        errors=error_count,
        status="completed",
    )
    db.finish_scrape_run(run_id, run)

    print(f"\nScrape complete")
    print(f"  Listings scraped : {total_pushed}")
    print(f"  New              : {new_count}")
    print(f"  Updated          : {updated_count}")
    print(f"  Skipped (known)  : {skipped_known}")
    print(f"  Errors           : {error_count}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="imovirtual.com scraper")
    parser.add_argument("--url", default=None,
                        help="Search URL to start from (overrides --type)")
    parser.add_argument("--type", choices=["sale", "rent"], default="sale",
                        help="Listing type to scrape: 'sale' (default) or 'rent'")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Max search result pages (default: {DEFAULT_MAX_PAGES})")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS,
                        help=f"Max listings to scrape (default: {DEFAULT_MAX_ITEMS})")
    parser.add_argument("--no-headless", action="store_true",
                        help="Show the browser window (useful for debugging)")
    parser.add_argument("--setup", action="store_true",
                        help="Open browser for manual challenge solving, then exit")
    args = parser.parse_args()

    if args.setup:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                channel="chrome",
                headless=False,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 900},
                locale="pt-PT",
                timezone_id="Europe/Lisbon",
            )
            page = context.new_page()
            if _STEALTH_AVAILABLE:
                Stealth().use_sync(page)
            page.goto(DEFAULT_SEARCH, timeout=60000)
            print("\n" + "=" * 60)
            print("Browser is open. Solve any challenge on imovirtual.com,")
            print("verify listings are visible, then press ENTER here.")
            print("=" * 60 + "\n")
            input()
            context.close()
            print("Session saved. You can now run the scraper normally.")
    else:
        listing_type = args.type
        if args.url:
            search_url = args.url
        elif listing_type == "rent":
            search_url = RENTAL_SEARCH
        else:
            search_url = DEFAULT_SEARCH
        run_scraper(
            search_url=search_url,
            max_pages=args.max_pages,
            max_items=args.max_items,
            headless=not args.no_headless,
            listing_type=listing_type,
        )
