"""
olx_playwright.py — Standalone OLX.pt scraper using Python Playwright.

OLX Portugal is a generalist classifieds site with unique FSBO (for sale by
owner) listings that don't appear on agency portals like Idealista or Imovirtual.

Setup (one-time):
    pip install playwright
    playwright install chromium

Run:
    python scrapers/olx_playwright.py --setup          # One-time: solve any challenge
    python scrapers/olx_playwright.py                  # Headless scrape (after --setup)
    python scrapers/olx_playwright.py --max-pages 5    # Quick test run
    python scrapers/olx_playwright.py --type rent      # Scrape rentals

The scraper will:
    1. Open a headless Chrome browser with persistent profile
    2. Paginate through OLX search results for Lisboa
    3. Visit each listing's detail page
    4. Extract data from JSON-LD structured data + DOM selectors fallback
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

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://www.olx.pt"
DEFAULT_SEARCH = "https://www.olx.pt/imoveis/lisboa/q-venda/"
RENTAL_SEARCH = "https://www.olx.pt/imoveis/lisboa/q-arrendar/"
DEFAULT_MAX_PAGES = 60
DEFAULT_MAX_ITEMS = 1500
MAX_IMAGES = 15
BLOCKED_RETRIES = 3
BLOCKED_WAIT = 8

# Delay between page visits (seconds)
MIN_DELAY = 2.5
MAX_DELAY = 5.5

PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile_olx"

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


def _hash(address, city, price, size, source="olx") -> str:
    addr = (address or "").lower().strip()
    price_r = str(round(price / 1000) * 1000) if price else ""
    size_r = str(round(size)) if size else ""
    raw = f"{addr}|{(city or '').lower()}|{price_r}|{size_r}|{source}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _polite_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ── Blocked page detection ────────────────────────────────────────────────────

def _is_blocked(page: "Page") -> bool:
    """Check if we got a 403/challenge page."""
    try:
        content = page.content()
        if len(content) < 5000:
            return True
        # Common challenge/block indicators
        if "cf-challenge" in content or "Just a moment" in content:
            return True
        if "Access Denied" in content or "403 Forbidden" in content:
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
    """Load source_id -> price_amount for all OLX listings in the DB.
    Used to skip unchanged listings and detect price changes."""
    table = "rentals" if listing_type == "rent" else "sales"
    try:
        conn = db.get_connection()
        rows = conn.execute(
            f"SELECT source_id, price_amount FROM {table} WHERE source='olx'"
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
        "button:has-text('Aceito')",
        "[data-testid='accept-cookie']",
        "[id*='accept'][role='button']",
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


# ── JSON-LD extraction ────────────────────────────────────────────────────────

def extract_json_ld(page: Page) -> list:
    """Extract all JSON-LD objects from the page."""
    try:
        scripts = page.evaluate("""
            () => Array.from(
                document.querySelectorAll('script[type="application/ld+json"]')
            ).map(s => s.textContent)
        """)
    except Exception:
        return []

    results = []
    for raw in (scripts or []):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
        except (json.JSONDecodeError, TypeError):
            continue
    return results


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
    Returns list of dicts: {"url": str, "price": float|None}.

    Tries JSON-LD ItemList first, then falls back to DOM extraction.
    """
    # Try JSON-LD ItemList first
    try:
        ld_items = extract_json_ld(page)
        urls_from_ld = []
        for item in ld_items:
            if item.get("@type") == "ItemList":
                for el in item.get("itemListElement") or []:
                    u = el.get("url")
                    if not u:
                        inner = el.get("item")
                        if isinstance(inner, dict):
                            u = inner.get("url")
                        elif isinstance(inner, str):
                            u = inner
                    if u:
                        urls_from_ld.append({"url": u, "price": None})
        if urls_from_ld:
            log.info(f"  Extracted {len(urls_from_ld)} URLs from JSON-LD ItemList")
            return urls_from_ld
    except Exception:
        pass

    # Fallback: DOM extraction
    try:
        items = page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();
                // OLX listing card links — try multiple selector patterns
                const cards = document.querySelectorAll(
                    'a[href*="/d/anuncio/"], ' +
                    'a[href*="/anuncio/"], ' +
                    'a[href*="-ID"]'
                );
                for (const a of cards) {
                    const href = a.href;
                    // Must contain ID pattern and not be a duplicate
                    if (seen.has(href)) continue;
                    if (!href.match(/ID[a-zA-Z0-9]+\\.html/)) continue;
                    seen.add(href);

                    // Try to grab the price from the card
                    let priceText = null;
                    const card = a.closest('div[data-cy="l-card"], article, li, [data-testid="listing-card"]') || a;
                    const priceEl = card.querySelector(
                        '[data-testid="ad-price"], ' +
                        'p[data-testid="ad-price"], ' +
                        'span[class*="price"], ' +
                        'h6, strong'
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
    """Extract the OLX listing ID from URL.
    OLX URLs contain an ID suffix: -IDxxxxxx.html
    e.g. /d/anuncio/apartamento-t2-lisboa-IDabc123.html
    """
    m = re.search(r'ID([a-zA-Z0-9]+)\.html', url)
    if m:
        return m.group(1)
    # Fallback: hash the URL
    return hashlib.sha1(url.encode()).hexdigest()[:16]


# ── Detail page extraction from JSON-LD ───────────────────────────────────────

def extract_listing_from_json_ld(ld_items: list) -> dict:
    """Extract listing fields from JSON-LD structured data on the detail page.
    OLX uses Product, WebPage, BreadcrumbList, and Offer schemas."""
    result = {}  # type: dict

    for item in ld_items:
        item_type = item.get("@type", "")

        # Product schema — main listing data
        if item_type == "Product" or (isinstance(item_type, list) and "Product" in item_type):
            result["title"] = item.get("name")
            result["description"] = str(item.get("description") or "")[:1000] or None

            # Images
            images_raw = item.get("image") or []
            if isinstance(images_raw, str):
                images_raw = [images_raw]
            images = []
            for img in images_raw:
                if isinstance(img, str) and img.startswith("http"):
                    images.append(img)
                elif isinstance(img, dict):
                    u = img.get("url") or img.get("contentUrl")
                    if u and u.startswith("http"):
                        images.append(u)
            if images:
                result["images"] = images[:MAX_IMAGES]

            # Price from Offer
            offers = item.get("offers")
            if isinstance(offers, dict):
                price = _num(offers.get("price"))
                if price:
                    result["price"] = price
            elif isinstance(offers, list):
                for offer in offers:
                    price = _num(offer.get("price"))
                    if price:
                        result["price"] = price
                        break

        # GeoCoordinates — may be nested in Product or standalone
        if item_type == "Place" or item_type == "GeoCoordinates":
            geo = item if item_type == "GeoCoordinates" else item.get("geo", {})
            lat = _coord(geo.get("latitude"))
            lon = _coord(geo.get("longitude"))
            if lat and lon:
                result["lat"] = lat
                result["lon"] = lon

        # BreadcrumbList — location hierarchy
        if item_type == "BreadcrumbList":
            elements = item.get("itemListElement") or []
            # Breadcrumbs typically: Home > Imoveis > District > City > Neighborhood
            crumbs = []
            for el in sorted(elements, key=lambda x: x.get("position", 0)):
                name = None
                crumb_item = el.get("item")
                if isinstance(crumb_item, dict):
                    name = crumb_item.get("name") or el.get("name")
                elif isinstance(el, dict):
                    name = el.get("name")
                if name:
                    crumbs.append(name)
            if crumbs:
                result["breadcrumbs"] = crumbs

    # Also check for nested geo in Product
    for item in ld_items:
        if item.get("@type") == "Product" or (isinstance(item.get("@type"), list) and "Product" in item.get("@type", [])):
            # Check for geo within additionalProperty or nested structures
            geo = find_deep(item, ["geo"], ["location", "geo"])
            if isinstance(geo, dict):
                lat = _coord(geo.get("latitude"))
                lon = _coord(geo.get("longitude"))
                if lat and lon and "lat" not in result:
                    result["lat"] = lat
                    result["lon"] = lon

    return result


# ── DOM-based extraction (fallback) ──────────────────────────────────────────

def extract_from_dom(page: Page) -> dict:
    """Extract listing data from DOM elements as fallback when JSON-LD is incomplete."""
    result = {}

    try:
        data = page.evaluate(r"""
            () => {
                const out = {};

                // Title
                const titleEl = document.querySelector(
                    'h1[data-cy="ad_title"], h1[data-testid="ad-title"], h1'
                );
                if (titleEl) out.title = titleEl.textContent.trim();

                // Price
                const priceEl = document.querySelector(
                    '[data-testid="ad-price-container"] h3, ' +
                    '[data-cy="ad_price"] h3, ' +
                    'div[data-testid="ad-price"] h3, ' +
                    '[data-testid="ad-price"]'
                );
                if (priceEl) out.priceText = priceEl.textContent.trim();

                // Description
                const descEl = document.querySelector(
                    '[data-cy="ad_description"] div, ' +
                    '[data-testid="ad-description"], ' +
                    'div[data-testid="content-text"]'
                );
                if (descEl) out.description = descEl.textContent.trim().slice(0, 1000);

                // Location text
                const locEl = document.querySelector(
                    '[data-testid="map-link-text"], ' +
                    'p[data-testid="ad-location"], ' +
                    '[data-cy="ad_location"]'
                );
                if (locEl) out.location = locEl.textContent.trim();

                // Parameters/features section (area, rooms, etc.)
                // OLX shows params as key-value pairs
                const params = document.querySelectorAll(
                    'li[data-testid="ad-params-item"], ' +
                    'ul[data-testid="ad-params"] li, ' +
                    '[data-cy="ad_params"] li, ' +
                    'div.parametersHandler li, ' +
                    'ul.css-sfcl1s li'
                );
                out.params = Array.from(params).map(li => {
                    const label = li.querySelector('span, p:first-child');
                    const value = li.querySelector('a, p:last-child, span:last-child');
                    return {
                        label: label ? label.textContent.trim() : '',
                        value: value ? value.textContent.trim() : li.textContent.trim()
                    };
                });

                // Images
                const imgs = document.querySelectorAll(
                    'img[data-testid="swiper-image"], ' +
                    '[data-cy="ad_photo"] img, ' +
                    'div[data-testid="gallery"] img, ' +
                    'div.swiper-slide img'
                );
                out.images = Array.from(imgs)
                    .map(i => i.src || i.dataset.src || '')
                    .filter(s => s.startsWith('http'));

                // Breadcrumbs
                const breadcrumbs = document.querySelectorAll(
                    'nav[aria-label="breadcrumb"] a, ' +
                    '[data-testid="breadcrumbs"] a, ' +
                    'ol.breadcrumb a, ' +
                    'ul[data-testid="breadcrumbs"] a'
                );
                out.breadcrumbs = Array.from(breadcrumbs).map(a => a.textContent.trim());

                // Try to find coordinates from map or inline scripts
                const scripts = document.querySelectorAll('script:not([src])');
                for (const s of scripts) {
                    const t = s.textContent || '';
                    // Look for lat/lng patterns in inline JS
                    const latMatch = t.match(/"lat(?:itude)?":\s*([\d.-]+)/);
                    const lonMatch = t.match(/"(?:lon|lng|longitude)":\s*([\d.-]+)/);
                    if (latMatch && lonMatch) {
                        out.lat = latMatch[1];
                        out.lon = lonMatch[1];
                        break;
                    }
                    // Alternative coordinate pattern
                    const coordMatch = t.match(/coordinates.*?([\d]{1,2}\.[\d]{4,})[,\s]+([-]?[\d]{1,3}\.[\d]{4,})/);
                    if (coordMatch) {
                        out.lat = coordMatch[1];
                        out.lon = coordMatch[2];
                        break;
                    }
                }

                // Map element data attributes
                const mapEl = document.querySelector(
                    '[data-testid="map"], [data-cy="map"], [id*="map"]'
                );
                if (mapEl) {
                    const lat = mapEl.dataset.lat || mapEl.getAttribute('data-lat');
                    const lon = mapEl.dataset.lon || mapEl.getAttribute('data-lon') ||
                                mapEl.dataset.lng || mapEl.getAttribute('data-lng');
                    if (lat) out.lat = lat;
                    if (lon) out.lon = lon;
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

    result["title"] = data.get("title")
    result["description"] = data.get("description")

    # Parse params (area, rooms, condition, etc.)
    for param in (data.get("params") or []):
        label = (param.get("label") or "").lower()
        value = param.get("value") or ""

        # Area / size
        if any(w in label for w in ["area", "m2", "m²", "tamanho"]):
            val = _num(value)
            if val:
                vl = label.lower()
                if "brut" in vl:
                    result["gross_area"] = val
                elif "útil" in vl or "util" in vl:
                    result["size"] = val
                else:
                    if not result.get("size"):
                        result["size"] = val

        # Rooms / typology
        elif any(w in label for w in ["quarto", "tipologia", "assoalhada", "room"]):
            rooms = _int(value)
            if rooms:
                result["rooms"] = rooms
                result["bedrooms"] = rooms
        # Detect T0, T1, T2 etc. in the value
        elif re.match(r"T\d", value.strip()):
            rooms = _int(value)
            if rooms is not None:
                result["rooms"] = rooms
                result["bedrooms"] = rooms

        # Bathrooms
        elif any(w in label for w in ["casa de banho", "wc", "bathroom"]):
            result["bathrooms"] = _int(value)

        # Floor
        elif any(w in label for w in ["andar", "piso", "floor"]):
            if "r/c" in value.lower() or "rés" in value.lower():
                result["floor"] = "0"
            else:
                f = _int(value)
                result["floor"] = str(f) if f is not None else value

        # Condition
        elif any(w in label for w in ["estado", "condição", "condition"]):
            result["condition"] = _normalize_condition(value)

        # Property type
        elif any(w in label for w in ["tipo", "type", "categoria"]):
            result["property_type"] = _normalize_property_type(value)

    # Location from text
    if data.get("location"):
        loc_parts = data["location"].split(",")
        loc_parts = [p.strip() for p in loc_parts if p.strip()]
        if len(loc_parts) >= 1:
            result["neighborhood"] = loc_parts[0]
        if len(loc_parts) >= 2:
            result["city"] = loc_parts[1]
        if len(loc_parts) >= 3:
            result["district"] = loc_parts[2]

    # Location from breadcrumbs: typically [Home, Imoveis, District, City, Neighborhood]
    breadcrumbs = data.get("breadcrumbs") or []
    if breadcrumbs and not result.get("city"):
        # Skip generic crumbs like "Home", "Imoveis"
        loc_crumbs = [c for c in breadcrumbs if c.lower() not in ("olx", "home", "imoveis", "imóveis", "início")]
        if len(loc_crumbs) >= 1 and not result.get("district"):
            result["district"] = loc_crumbs[0]
        if len(loc_crumbs) >= 2 and not result.get("city"):
            result["city"] = loc_crumbs[1]
        if len(loc_crumbs) >= 3 and not result.get("neighborhood"):
            result["neighborhood"] = loc_crumbs[2]

    # Coordinates
    if data.get("lat"):
        result["lat"] = _coord(data["lat"])
    if data.get("lon"):
        result["lon"] = _coord(data["lon"])

    # Images
    if data.get("images"):
        result["images"] = data["images"][:MAX_IMAGES]

    # Property type from title if not found in params
    if not result.get("property_type"):
        title = (result.get("title") or "").lower()
        for pt in ["apartamento", "moradia", "vivenda", "loja", "terreno",
                    "escritório", "garagem", "armazém", "prédio"]:
            if pt in title:
                result["property_type"] = _normalize_property_type(pt)
                break

    # Try to extract typology from title (e.g. "Apartamento T2 Lisboa")
    if not result.get("rooms"):
        title = (result.get("title") or data.get("title") or "")
        t_match = re.search(r'\bT(\d+)\b', title, re.IGNORECASE)
        if t_match:
            rooms = int(t_match.group(1))
            result["rooms"] = rooms
            result["bedrooms"] = rooms

    return result


# ── Detail page scraper ───────────────────────────────────────────────────────

def scrape_detail_page(page: Page, url: str, listing_type: str = 'sale') -> Optional[Listing]:
    """Visit a listing detail page and return a Listing object."""
    from models import detect_listing_type
    listing_type = detect_listing_type(url, fallback=listing_type)

    source_id = _extract_source_id(url)

    # Pull JSON-LD structured data (OLX has extensive JSON-LD)
    ld_items = extract_json_ld(page)
    ld = extract_listing_from_json_ld(ld_items)

    # Always run DOM extraction so merge logic can fill fields JSON-LD doesn't provide
    dom = extract_from_dom(page)

    price      = ld.get("price")      or dom.get("price")
    size       = ld.get("size")       or dom.get("size")
    gross_area = ld.get("gross_area") or dom.get("gross_area")
    lat        = ld.get("lat")        or dom.get("lat")
    lon        = ld.get("lon")        or dom.get("lon")
    address    = ld.get("address")    or dom.get("address")
    postal     = ld.get("postal_code") or dom.get("postal_code")
    city       = ld.get("city")       or dom.get("city") or "Lisboa"
    district   = ld.get("district")   or dom.get("district") or "Lisboa"
    bedrooms   = ld.get("bedrooms")   or dom.get("bedrooms")
    floor      = ld.get("floor")      or dom.get("floor")
    images     = ld.get("images")     or dom.get("images") or []

    # Location from breadcrumbs (JSON-LD or DOM)
    breadcrumbs = ld.get("breadcrumbs") or dom.get("breadcrumbs") or []
    loc_crumbs = [c for c in breadcrumbs if c.lower() not in ("olx", "home", "imoveis", "imóveis", "início")]
    if loc_crumbs:
        if district == "Lisboa" and len(loc_crumbs) >= 1:
            district = loc_crumbs[0]
        if city == "Lisboa" and len(loc_crumbs) >= 2:
            city = loc_crumbs[1]

    neighborhood = ld.get("neighborhood") or dom.get("neighborhood")

    price_per_sqm = round(price / size, 2) if price and size and size > 0 else None

    return Listing(
        source="olx",
        source_id=source_id,
        url=url,
        listing_type=listing_type,
        status='active',
        price_amount=price,
        price_per_sqm=price_per_sqm,
        size_sqm=size,
        gross_area_sqm=gross_area,
        rooms=ld.get("rooms") or dom.get("rooms"),
        bedrooms=bedrooms,
        bathrooms=ld.get("bathrooms") or dom.get("bathrooms"),
        floor=floor,
        property_type=ld.get("property_type") or dom.get("property_type"),
        condition=ld.get("condition") or dom.get("condition"),
        title=ld.get("title") or dom.get("title"),
        address=address,
        postal_code=postal,
        neighborhood=neighborhood,
        parish=None,
        district=district,
        city=city,
        lat=lat,
        lon=lon,
        images=json.dumps(images) if images else None,
        hash_dedupe=_hash(address, city, price, size),
        description=ld.get("description") or dom.get("description"),
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
    # OLX uses ?page=N for pagination
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}page={page_num}"


# ── Main scraper ──────────────────────────────────────────────────────────────

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
    run_id = db.start_scrape_run("olx")

    new_count = updated_count = error_count = 0
    skipped_known = 0
    total_pushed = 0

    # Load already-scraped source_ids with prices so we can skip unchanged ones
    known_listings = _load_known_listings(listing_type)
    log.info(f"Loaded {len(known_listings)} known OLX listings from DB")

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
            log.warning("playwright-stealth not installed — bot detection may block scraping")

        # Warm up: visit homepage to pick up cookies
        log.info("Warming up session on olx.pt...")
        page.goto(BASE_URL, timeout=30000)
        accept_cookies(page)
        _polite_delay()

        # ── Collect listings from search pages ─────────────────────────────
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
                    log.warning("Blocked 3 search pages — session may be expired. Run --setup.")
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

        # ── Filter: skip known listings whose price hasn't changed ────────────
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

        # ── Visit each detail page ────────────────────────────────────────────
        for i, detail_url in enumerate(urls_to_scrape):
            if total_pushed >= max_items:
                log.info(f"Reached max_items={max_items}. Stopping.")
                break

            log.info(f"[{i+1}/{len(urls_to_scrape)}] {detail_url}")

            try:
                if not _goto_with_retry(page, detail_url):
                    log.warning(f"  Blocked on detail page — skipping")
                    error_count += 1
                    continue

                # Wait for content to load
                try:
                    page.wait_for_selector(
                        "script[type='application/ld+json'], h1[data-cy='ad_title'], h1",
                        timeout=8000
                    )
                except PWTimeout:
                    pass

                listing = scrape_detail_page(page, detail_url, listing_type=listing_type)

                if listing is None or listing.price_amount is None:
                    log.warning(f"  No price extracted — skipping")
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
                log.warning(f"  Timeout on detail page — skipping")
                error_count += 1
            except Exception as e:
                log.warning(f"  Error: {e}")
                error_count += 1

            _polite_delay()

        context.close()

    # ── Detect missing listings ──────────────────────────────────────────────
    if len(known_listings) > 0 and len(seen_source_ids) < len(known_listings) * 0.5:
        log.warning(f"Only saw {len(seen_source_ids)}/{len(known_listings)} listings — skipping missing detection (possible block)")
    else:
        missing_result = db.process_missing_listings(
            source="olx", listing_type=listing_type,
            seen_source_ids=seen_source_ids, run_id=run_id,
        )
        log.info(f"Missing detection: {missing_result}")

    # ── Rebuild stats and close run ───────────────────────────────────────────
    db.rebuild_neighborhoods()

    run = ScrapeRun(
        source="olx",
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
    parser = argparse.ArgumentParser(description="OLX.pt real estate scraper")
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
            print("Browser is open. Solve any challenge on olx.pt,")
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
