"""
remax_playwright.py — Standalone remax.pt scraper using Python Playwright.

Setup (one-time):
    pip install playwright
    playwright install chromium

Run:
    python scrapers/remax_playwright.py --setup          # One-time: solve any challenge
    python scrapers/remax_playwright.py                  # Headless scrape (after --setup)
    python scrapers/remax_playwright.py --max-pages 5    # Quick test run
    python scrapers/remax_playwright.py --type rent      # Scrape rentals

The scraper will:
    1. Open a headless Chrome browser with persistent profile
    2. Paginate through RE/MAX search results for Lisboa
    3. Visit each listing's detail page
    4. Extract data from __NEXT_DATA__ JSON + DOM selectors fallback
    5. Save each listing to SQLite and rebuild neighborhood stats when done
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

# -- Config --------------------------------------------------------------------

BASE_URL = "https://www.remax.pt"
DEFAULT_SEARCH = "https://www.remax.pt/comprar?searchQueryState=%7B%22regionName%22%3A%22Lisboa%22%2C%22businessType%22%3A1%2C%22mediaTypes%22%3A%5B1%5D%7D"
RENTAL_SEARCH = "https://www.remax.pt/arrendar?searchQueryState=%7B%22regionName%22%3A%22Lisboa%22%2C%22businessType%22%3A2%2C%22mediaTypes%22%3A%5B1%5D%7D"
DEFAULT_MAX_PAGES = 60
DEFAULT_MAX_ITEMS = 1500
MAX_IMAGES = 15
BLOCKED_RETRIES = 3
BLOCKED_WAIT = 8

# Delay between page visits (seconds)
MIN_DELAY = 2.5
MAX_DELAY = 5.5

PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile_remax"

# -- Utilities -----------------------------------------------------------------

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


def _hash(address, city, price, size, source="remax") -> str:
    addr = (address or "").lower().strip()
    price_r = str(round(price / 1000) * 1000) if price else ""
    size_r = str(round(size)) if size else ""
    raw = f"{addr}|{(city or '').lower()}|{price_r}|{size_r}|{source}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _polite_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# -- Blocked page detection ----------------------------------------------------

def _is_blocked(page: "Page") -> bool:
    """Check if we got a 403/WAF challenge page."""
    try:
        content = page.content()
        if len(content) < 5000:
            return True
        # Cloudflare / generic WAF challenge indicators
        if "cf-challenge" in content or "Just a moment" in content:
            return True
        # Access denied pages
        if "Access Denied" in content and len(content) < 10000:
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
    """Load source_id -> price_amount for all remax listings in the DB.
    Used to skip unchanged listings and detect price changes."""
    table = "rentals" if listing_type == "rent" else "sales"
    try:
        conn = db.get_connection()
        rows = conn.execute(
            f"SELECT source_id, price_amount FROM {table} WHERE source='remax'"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


# -- Cookie banner -------------------------------------------------------------

def accept_cookies(page: Page):
    """Dismiss the cookie/GDPR banner if present."""
    selectors = [
        "button#onetrust-accept-btn-handler",
        "button:has-text('Aceitar')",
        "button:has-text('Aceitar tudo')",
        "button:has-text('Aceitar Todos')",
        "button:has-text('Accept')",
        "button:has-text('Accept All')",
        "button:has-text('Agree')",
        "[data-cy='cookie-consent-accept']",
        "button.accept-cookies",
        "#cookie-accept",
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


# -- __NEXT_DATA__ extraction --------------------------------------------------

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


# -- Search results: collect detail URLs ---------------------------------------

def extract_search_listings(page: Page) -> list:
    """Extract listing URLs and preview prices from a search results page.
    Returns list of dicts: {"url": str, "price": float|None}."""

    # First try __NEXT_DATA__ for structured listing data
    next_data = extract_next_data(page)
    if next_data:
        page_props = find_deep(next_data, ["props", "pageProps"], ["pageProps"]) or {}
        # RE/MAX may store search results under various keys
        listings_data = (
            find_deep(page_props, ["listings"]) or
            find_deep(page_props, ["searchResults", "listings"]) or
            find_deep(page_props, ["results"]) or
            find_deep(page_props, ["properties"]) or
            find_deep(page_props, ["items"]) or
            []
        )
        if isinstance(listings_data, list) and listings_data:
            items = []
            for item in listings_data:
                if not isinstance(item, dict):
                    continue
                # Try to build URL from listing data
                url = (
                    item.get("url") or
                    item.get("detailUrl") or
                    item.get("link") or
                    item.get("href")
                )
                listing_id = (
                    item.get("id") or
                    item.get("listingId") or
                    item.get("propertyId")
                )
                if not url and listing_id:
                    url = f"{BASE_URL}/imoveis/{listing_id}"
                if url and not url.startswith("http"):
                    url = BASE_URL + url

                price_val = _num(
                    item.get("price") or
                    find_deep(item, ["price", "value"]) or
                    item.get("askingPrice")
                )
                if url:
                    items.append({"url": url, "price": price_val})
            if items:
                return items

    # Fallback: DOM extraction of listing cards
    try:
        items = page.evaluate("""
            () => {
                const results = [];
                // RE/MAX listing cards — try multiple selector patterns
                const cards = document.querySelectorAll(
                    'a[href*="/imoveis/"], ' +
                    'a[href*="/imovel/"], ' +
                    'a[href*="/comprar-"], ' +
                    'a[href*="/arrendar-"], ' +
                    'article a[href*="remax.pt"], ' +
                    '[data-testid="property-card"] a, ' +
                    '.property-card a, ' +
                    '.listing-card a'
                );
                const seen = new Set();
                for (const a of cards) {
                    const href = a.href;
                    // Filter for detail page links (contain numeric ID or property slug)
                    if (seen.has(href)) continue;
                    if (!href.includes('/imoveis/') && !href.includes('/imovel/') &&
                        !href.includes('/comprar-') && !href.includes('/arrendar-'))
                        continue;
                    // Skip pagination and filter links
                    if (href.includes('page=') && !href.includes('/imoveis/'))
                        continue;
                    seen.add(href);

                    // Try to grab the price from the card
                    let priceText = null;
                    const card = a.closest('article, li, div[class*="card"], div[class*="listing"]') || a;
                    const priceEl = card.querySelector(
                        '[class*="price"], [class*="Price"], ' +
                        'span[class*="value"], strong[class*="price"], ' +
                        '[data-testid="price"], .listing-price'
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
    """Extract the RE/MAX listing ID from URL.
    URLs may look like:
      /imoveis/comprar-apartamento-t2-lisboa/12345678
      /imovel/12345678
      /listing/12345678
    """
    # Try to find a numeric ID (typically 6-10 digits) at the end of the URL path
    m = re.search(r'/(\d{6,10})(?:\?|$|/)', url)
    if m:
        return m.group(1)
    # Try alphanumeric ID after last slash
    m = re.search(r'/([a-zA-Z0-9\-]{8,})(?:\?|$)', url)
    if m:
        return m.group(1)
    # Fallback: hash the URL
    return hashlib.sha1(url.encode()).hexdigest()[:16]


# -- Detail page extraction from __NEXT_DATA__ --------------------------------

def extract_listing_from_next_data(page_props: dict) -> dict:
    """Extract listing fields from __NEXT_DATA__ pageProps."""
    ad = (
        find_deep(page_props, ["listing"]) or
        find_deep(page_props, ["property"]) or
        find_deep(page_props, ["ad"]) or
        find_deep(page_props, ["adDetail"]) or
        find_deep(page_props, ["propertyDetail"]) or
        page_props
    )

    # Price
    price = _num(
        find_deep(ad, ["price", "value"]) or
        find_deep(ad, ["price", "amount"]) or
        ad.get("price") or
        ad.get("askingPrice") or
        find_deep(ad, ["characteristics", "price"])
    )

    # Size
    size = _num(
        ad.get("usableArea") or
        ad.get("livingArea") or
        find_deep(ad, ["areas", "usable"]) or
        find_deep(ad, ["areas", "living"]) or
        find_deep(ad, ["characteristics", "area"]) or
        ad.get("area") or ad.get("m2")
    )
    gross_area = _num(
        ad.get("grossArea") or
        ad.get("totalArea") or
        find_deep(ad, ["areas", "gross"]) or
        find_deep(ad, ["areas", "total"])
    )
    if gross_area and size and gross_area == size:
        gross_area = None

    # Rooms (typology)
    rooms_raw = (
        ad.get("rooms") or
        ad.get("typology") or
        ad.get("rooms_num") or
        find_deep(ad, ["characteristics", "rooms"]) or
        find_deep(ad, ["characteristics", "typology"])
    )
    rooms = _int(rooms_raw)

    bedrooms = _int(
        ad.get("bedrooms") or
        ad.get("noOfBedrooms") or
        find_deep(ad, ["characteristics", "bedrooms"])
    ) or rooms

    bathrooms = _int(
        ad.get("bathrooms") or
        ad.get("noOfBathrooms") or
        find_deep(ad, ["characteristics", "bathrooms"])
    )

    floor_raw = (
        ad.get("floor") or
        ad.get("floorNumber") or
        find_deep(ad, ["characteristics", "floor"])
    )
    floor = str(floor_raw) if floor_raw is not None else None

    condition = _normalize_condition(
        ad.get("condition") or
        ad.get("status") or
        find_deep(ad, ["characteristics", "condition"])
    )
    property_type = _normalize_property_type(
        ad.get("propertyType") or
        ad.get("type") or
        ad.get("typeName") or
        find_deep(ad, ["characteristics", "propertyType"])
    )

    # Location
    title = ad.get("title") or ad.get("heading") or ad.get("subject")
    description = ad.get("description")
    if description:
        description = str(description)[:1000]

    location = find_deep(ad, ["location"]) or {}
    address_obj = find_deep(ad, ["location", "address"]) or {}

    address = (
        address_obj.get("street") or
        address_obj.get("name") or
        ad.get("address") or
        ad.get("fullAddress")
    )

    # Navigate location hierarchy
    city_val = (
        find_deep(location, ["address", "city"]) or
        find_deep(location, ["city"]) or
        ad.get("city") or
        ad.get("municipality")
    )
    if isinstance(city_val, dict):
        city_val = city_val.get("name")
    city = str(city_val) if city_val else None

    neighborhood_val = (
        find_deep(location, ["address", "neighborhood"]) or
        find_deep(location, ["neighborhood"]) or
        ad.get("neighborhood") or
        ad.get("zone")
    )
    if isinstance(neighborhood_val, dict):
        neighborhood_val = neighborhood_val.get("name")
    neighborhood = str(neighborhood_val) if neighborhood_val else None

    parish_val = (
        find_deep(location, ["address", "parish"]) or
        find_deep(location, ["parish"]) or
        ad.get("parish") or
        ad.get("freguesia")
    )
    if isinstance(parish_val, dict):
        parish_val = parish_val.get("name")
    parish = str(parish_val) if parish_val else None

    district_val = (
        find_deep(location, ["address", "district"]) or
        find_deep(location, ["district"]) or
        ad.get("district") or
        ad.get("region")
    )
    if isinstance(district_val, dict):
        district_val = district_val.get("name")
    district = str(district_val) if district_val else None

    postal_code = (
        address_obj.get("postalCode") or
        ad.get("postalCode") or
        ad.get("zipCode")
    )

    # Coordinates
    coords = find_deep(ad, ["location", "coordinates"]) or find_deep(ad, ["coordinates"]) or {}
    lat = _coord(coords.get("latitude") or ad.get("latitude") or ad.get("lat"))
    lon = _coord(coords.get("longitude") or ad.get("longitude") or ad.get("lon") or ad.get("lng"))

    # Try map data
    if not lat:
        map_data = find_deep(ad, ["location", "mapDetails"]) or find_deep(ad, ["map"]) or {}
        lat = _coord(map_data.get("latitude") or map_data.get("lat"))
        lon = _coord(map_data.get("longitude") or map_data.get("lon") or map_data.get("lng"))

    # Images
    images = []
    imgs_raw = ad.get("images") or ad.get("photos") or ad.get("gallery") or ad.get("media") or []
    if isinstance(imgs_raw, list):
        for img in imgs_raw:
            if isinstance(img, str):
                images.append(img)
            elif isinstance(img, dict):
                url = (
                    img.get("large") or img.get("medium") or
                    img.get("url") or img.get("src") or
                    img.get("link") or img.get("original") or
                    img.get("imageUrl")
                )
                if url:
                    images.append(url)
    images = [u for u in images if u and u.startswith("http")][:MAX_IMAGES]

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
        "parish": parish,
        "city": city,
        "district": district,
        "lat": lat,
        "lon": lon,
        "images": images,
        "description": description,
    }


# -- DOM-based extraction (fallback) ------------------------------------------

def extract_from_dom(page: Page) -> dict:
    """Extract listing data from DOM elements using common selectors."""
    result = {}

    try:
        data = page.evaluate(r"""
            () => {
                const out = {};

                // Title
                const titleEl = document.querySelector(
                    'h1[class*="title"], h1[data-testid="listing-title"], h1'
                );
                if (titleEl) out.title = titleEl.textContent.trim();

                // Price — RE/MAX typically shows price in a prominent element
                const priceEl = document.querySelector(
                    '[class*="price" i] strong, ' +
                    '[class*="price" i] span, ' +
                    '[class*="Price"] strong, ' +
                    '[class*="Price"] span, ' +
                    '[data-testid="price"], ' +
                    '.property-price, ' +
                    '.listing-price'
                );
                if (priceEl) out.priceText = priceEl.textContent.trim();

                // Features / characteristics — look for labeled value pairs
                const featureItems = document.querySelectorAll(
                    '[class*="feature"] li, ' +
                    '[class*="characteristic"] li, ' +
                    '[class*="detail"] li, ' +
                    '.property-features li, ' +
                    '.listing-features li'
                );
                out.features = Array.from(featureItems).map(li => li.textContent.trim());

                // Area
                const areaEl = document.querySelector(
                    '[class*="area" i], [data-testid="area"]'
                );
                if (areaEl) out.area = areaEl.textContent.trim();

                // Rooms/Typology — look for T0-T9+ pattern
                const typoEl = document.querySelector(
                    '[class*="typology" i], [class*="rooms" i], [data-testid="typology"]'
                );
                if (typoEl) out.typology = typoEl.textContent.trim();

                // Bathrooms
                const bathEl = document.querySelector(
                    '[class*="bathroom" i], [data-testid="bathrooms"]'
                );
                if (bathEl) out.bathrooms = bathEl.textContent.trim();

                // Condition
                const condEl = document.querySelector(
                    '[class*="condition" i], [class*="estado" i], [data-testid="condition"]'
                );
                if (condEl) out.condition = condEl.textContent.trim();

                // Floor
                const floorEl = document.querySelector(
                    '[class*="floor" i], [class*="andar" i], [data-testid="floor"]'
                );
                if (floorEl) out.floor = floorEl.textContent.trim();

                // Address / Location
                const addrEl = document.querySelector(
                    '[class*="address" i], [class*="location" i] span, ' +
                    '[data-testid="address"], .property-location'
                );
                if (addrEl) out.address = addrEl.textContent.trim();

                // Breadcrumbs for location hierarchy
                const breadcrumbs = document.querySelectorAll(
                    'nav[aria-label="breadcrumb"] a, ' +
                    '[class*="breadcrumb"] a, ' +
                    'ol[class*="breadcrumb"] a'
                );
                out.breadcrumbs = Array.from(breadcrumbs).map(a => a.textContent.trim());

                // Description
                const descEl = document.querySelector(
                    '[class*="description" i] p, ' +
                    '[class*="description" i] div, ' +
                    '[data-testid="description"], ' +
                    '.property-description p'
                );
                if (descEl) out.description = descEl.textContent.trim().slice(0, 1000);

                // Images
                const imgs = document.querySelectorAll(
                    'picture img[src*="remax"], ' +
                    'img[class*="gallery"], ' +
                    'img[class*="photo"], ' +
                    '[class*="gallery"] img, ' +
                    '[class*="carousel"] img, ' +
                    '[data-testid="gallery"] img'
                );
                out.images = Array.from(imgs)
                    .map(i => i.src || i.dataset.src || '')
                    .filter(s => s.startsWith('http'));

                // Coordinates from map element or data attributes
                const mapEl = document.querySelector(
                    '[class*="map"], [data-testid="map"], ' +
                    '#map, .property-map'
                );
                if (mapEl) {
                    const lat = mapEl.dataset.lat || mapEl.getAttribute('data-lat') ||
                                mapEl.dataset.latitude || mapEl.getAttribute('data-latitude');
                    const lon = mapEl.dataset.lon || mapEl.getAttribute('data-lon') ||
                                mapEl.dataset.lng || mapEl.getAttribute('data-lng') ||
                                mapEl.dataset.longitude || mapEl.getAttribute('data-longitude');
                    if (lat) out.lat = lat;
                    if (lon) out.lon = lon;
                }

                // Try Google Maps static image URL for coordinates
                if (!out.lat) {
                    const staticMap = document.querySelector(
                        'img[src*="maps.googleapis"], img[src*="maps.google"]'
                    );
                    if (staticMap) {
                        const src = staticMap.getAttribute('src') || '';
                        const coordMatch = src.match(/center=([\d.-]+)[,%20]+([\d.-]+)/);
                        if (coordMatch) {
                            out.lat = coordMatch[1];
                            out.lon = coordMatch[2];
                        }
                    }
                }

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
        m = re.search(r"([\d.,]+)\s*m", data["area"])
        if m:
            result["size"] = _num(m.group(1))

    if data.get("typology"):
        rooms = _int(data["typology"])
        if rooms is not None:
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

    # Parse features for additional data
    for feat in (data.get("features") or []):
        feat_lower = feat.lower()
        if ("m2" in feat_lower or "m\u00b2" in feat_lower) and not result.get("size"):
            m = re.search(r"([\d.,]+)\s*m", feat)
            if m:
                val = _num(m.group(1))
                if "brut" in feat_lower:
                    result["gross_area"] = val
                    if not result.get("size"):
                        result["size"] = val
                elif "util" in feat_lower or "\u00fatil" in feat_lower:
                    result["size"] = val
                else:
                    if not result.get("size"):
                        result["size"] = val
        elif re.match(r"T\d", feat.strip()) and not result.get("rooms"):
            rooms = _int(feat)
            result["rooms"] = rooms
            result["bedrooms"] = rooms
        elif ("andar" in feat_lower or "piso" in feat_lower) and not result.get("floor"):
            m = re.search(r"(\d+)", feat)
            if m:
                result["floor"] = m.group(1)
            elif "r\u00e9s" in feat_lower or "r/c" in feat_lower:
                result["floor"] = "0"

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

    # Property type from title
    title = (result.get("title") or "").lower()
    for pt in ["apartamento", "moradia", "vivenda", "loja", "terreno",
               "escrit\u00f3rio", "garagem", "armaz\u00e9m", "pr\u00e9dio"]:
        if pt in title:
            result["property_type"] = _normalize_property_type(pt)
            break

    return result


# -- Detail page scraper -------------------------------------------------------

def scrape_detail_page(page: Page, url: str, listing_type: str = 'sale') -> Optional[Listing]:
    """Visit a listing detail page and return a Listing object."""
    from models import detect_listing_type
    listing_type = detect_listing_type(url, fallback=listing_type)

    source_id = _extract_source_id(url)

    # Pull __NEXT_DATA__
    next_data = extract_next_data(page)
    page_props = find_deep(next_data, ["props", "pageProps"], ["pageProps"]) or {}

    nd = extract_listing_from_next_data(page_props)

    # Always run DOM extraction so merge logic can fill fields __NEXT_DATA__ doesn't provide
    dom = extract_from_dom(page)

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

    return Listing(
        source="remax",
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
        description=nd.get("description") or dom.get("description"),
        scraped_at=datetime.utcnow(),
    )


# -- Normalisation helpers -----------------------------------------------------

def _normalize_property_type(raw) -> str:
    if not raw:
        return "apartment"
    s = str(raw).lower()
    if any(w in s for w in ["moradia", "vivenda", "house", "villa"]):  return "house"
    if any(w in s for w in ["est\u00fadio", "studio", "t0"]):              return "studio"
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
    if any(w in s for w in ["em constru\u00e7\u00e3o", "under_construction"]):    return "new"
    return "used"


# -- Pagination ----------------------------------------------------------------

def build_page_url(base_url: str, page_num: int) -> str:
    if page_num == 1:
        return base_url
    # Use query parameter for pagination
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}page={page_num}"


# -- Main scraper --------------------------------------------------------------

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
    run_id = db.start_scrape_run("remax")

    new_count = updated_count = error_count = 0
    skipped_known = 0
    total_pushed = 0

    # Load already-scraped source_ids with prices so we can skip unchanged ones
    known_listings = _load_known_listings(listing_type)
    log.info(f"Loaded {len(known_listings)} known listings from DB")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="pt-PT",
            timezone_id="Europe/Lisbon",
        )
        context.set_extra_http_headers({
            "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        })

        page = context.new_page()

        if _STEALTH_AVAILABLE:
            Stealth().use_sync(page)
            log.info("Stealth mode active")
        else:
            log.warning("playwright-stealth not installed -- bot detection may block scraping")

        # Warm up: visit homepage to pick up cookies
        log.info("Warming up session on remax.pt...")
        page.goto(BASE_URL, timeout=30000)
        accept_cookies(page)
        _polite_delay()

        # -- Collect listings from search pages --------------------------------
        # Each item has {"url": ..., "price": ...} so we can compare with DB
        all_search_items = []  # type: list
        seen_urls = set()  # type: set
        blocked_count = 0

        for page_num in range(1, max_pages + 1):
            page_url = build_page_url(search_url, page_num)
            log.info(f"Search page {page_num}: {page_url}")

            if not _goto_with_retry(page, page_url):
                blocked_count += 1
                if blocked_count >= 3:
                    log.warning("Blocked 3 search pages -- session may be expired. Run --setup.")
                break

            search_items = extract_search_listings(page)
            log.info(f"  Found {len(search_items)} listings on page {page_num}")

            if not search_items:
                break  # end of results

            for item in search_items:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    all_search_items.append(item)

            if len(all_search_items) >= max_items * 2:
                log.info(f"Collected enough URLs ({len(all_search_items)}). Moving to detail scraping.")
                break

            _polite_delay()

        log.info(f"Total listings collected from search: {len(all_search_items)}")

        # -- Filter: skip known listings whose price hasn't changed ------------
        urls_to_scrape = []
        seen_source_ids = set()
        price_changed = 0
        for item in all_search_items:
            sid = _extract_source_id(item["url"])
            if sid:
                seen_source_ids.add(sid)
            if sid in known_listings:
                old_price = known_listings[sid]
                new_price = item.get("price")
                # Re-scrape if price changed (or if we couldn't read the preview price)
                if not new_price or not old_price or abs(new_price - old_price) > 1:
                    if new_price and old_price:
                        log.info(f"  Price changed for {sid}: {old_price} -> {new_price}")
                    urls_to_scrape.append(item["url"])
                    price_changed += 1
                else:
                    skipped_known += 1
            else:
                urls_to_scrape.append(item["url"])

        log.info(
            f"Skipping {skipped_known} unchanged listings, "
            f"{price_changed} price changes to update, "
            f"{len(urls_to_scrape) - price_changed} new to scrape"
        )

        # -- Visit each detail page --------------------------------------------
        for i, detail_url in enumerate(urls_to_scrape):
            if total_pushed >= max_items:
                log.info(f"Reached max_items={max_items}. Stopping.")
                break

            log.info(f"[{i+1}/{len(urls_to_scrape)}] {detail_url}")

            try:
                if not _goto_with_retry(page, detail_url):
                    log.warning(f"  Blocked on detail page -- skipping")
                    error_count += 1
                    continue

                # Wait for content
                try:
                    page.wait_for_selector(
                        "#__NEXT_DATA__, h1, [class*='price']",
                        timeout=8000
                    )
                except PWTimeout:
                    pass

                listing = scrape_detail_page(page, detail_url, listing_type=listing_type)

                if listing is None or listing.price_amount is None:
                    log.warning(f"  No price extracted -- skipping")
                    error_count += 1
                else:
                    _, is_new = db.upsert_listing(listing)
                    if is_new:
                        new_count += 1
                    else:
                        updated_count += 1
                    total_pushed += 1
                    log.info(
                        f"  OK {listing.neighborhood or 'unknown'} "
                        f"T{listing.rooms} "
                        f"EUR{listing.price_amount:,.0f} "
                        f"({listing.size_sqm}m2)"
                    )

            except PWTimeout:
                log.warning(f"  Timeout on detail page -- skipping")
                error_count += 1
            except Exception as e:
                log.warning(f"  Error: {e}")
                error_count += 1

            _polite_delay()

        context.close()

    # -- Detect missing listings -----------------------------------------------
    if len(known_listings) > 0 and len(seen_source_ids) < len(known_listings) * 0.5:
        log.warning(f"Only saw {len(seen_source_ids)}/{len(known_listings)} listings — skipping missing detection (possible block)")
    else:
        missing_result = db.process_missing_listings(
            source="remax", listing_type=listing_type,
            seen_source_ids=seen_source_ids, run_id=run_id,
        )
        log.info(f"Missing detection: {missing_result}")

    # -- Rebuild stats and close run -------------------------------------------
    db.rebuild_neighborhoods()

    run = ScrapeRun(
        source="remax",
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


# -- CLI -----------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="remax.pt scraper")
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
            print("Browser is open. Solve any challenge on remax.pt,")
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
