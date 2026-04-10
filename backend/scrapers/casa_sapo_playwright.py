"""
casa_sapo_playwright.py — Standalone casa.sapo.pt scraper using Python Playwright.

Setup (one-time):
    pip install playwright
    playwright install chromium

Run:
    python scrapers/casa_sapo_playwright.py --setup          # One-time: open Chrome, solve any challenge
    python scrapers/casa_sapo_playwright.py                  # Headless scrape (after --setup)
    python scrapers/casa_sapo_playwright.py --max-pages 5    # Quick test run
    python scrapers/casa_sapo_playwright.py --type rent      # Scrape rentals

The scraper will:
    1. Open a headless Chrome browser with persistent profile
    2. Paginate through casa.sapo.pt search results for Lisboa
    3. Visit each listing's detail page
    4. Extract data from JSON-LD schemas + DOM selectors fallback
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

BASE_URL = "https://casa.sapo.pt"
DEFAULT_SEARCH = "https://casa.sapo.pt/comprar-apartamentos/lisboa/"
RENTAL_SEARCH = "https://casa.sapo.pt/arrendar-apartamentos/lisboa/"
DEFAULT_MAX_PAGES = 60
DEFAULT_MAX_ITEMS = 1500
MAX_IMAGES = 15
BLOCKED_RETRIES = 3
BLOCKED_WAIT = 8

# Delay between page visits (seconds)
MIN_DELAY = 2.5
MAX_DELAY = 5.5

PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile_casa_sapo"

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


def _hash(address, city, price, size, source="casa_sapo") -> str:
    addr = (address or "").lower().strip()
    price_r = str(round(price / 1000) * 1000) if price else ""
    size_r = str(round(size)) if size else ""
    raw = f"{addr}|{(city or '').lower()}|{price_r}|{size_r}|{source}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _polite_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


# ── Blocked page detection ────────────────────────────────────────────────────

def _wait_for_interstitial(page: "Page") -> bool:
    """If the page is a 'Site Offline' interstitial with meta-refresh, wait for
    the real page to load.  Returns True if the interstitial was detected and
    resolved, False if it wasn't an interstitial."""
    try:
        content = page.content()
        if "Site Offline" in content and 'http-equiv="refresh"' in content:
            log.info("  Interstitial 'Site Offline' page — waiting for meta-refresh...")
            page.wait_for_timeout(12000)
            return True
    except Exception:
        pass
    return False


def _is_blocked(page: "Page") -> bool:
    """Check if we got a blocked/challenge page."""
    try:
        content = page.content()
        if len(content) < 5000:
            # Don't flag the Site Offline interstitial as blocked — it's
            # handled separately by _wait_for_interstitial.
            if "Site Offline" in content:
                return False
            return True
        # Common challenge indicators
        if "cf-challenge" in content or "Just a moment" in content:
            return True
        # CAPTCHA or access denied
        if "Access Denied" in content or "captcha" in content.lower():
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

        # Handle "Site Offline" interstitial (meta-refresh after ~10s)
        if _wait_for_interstitial(page):
            if not _is_blocked(page):
                return True

        if not _is_blocked(page):
            return True

        if attempt < retries:
            log.info(f"  Blocked (attempt {attempt}/{retries}), waiting {BLOCKED_WAIT}s...")
            time.sleep(BLOCKED_WAIT)
            try:
                page.reload(timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                if _wait_for_interstitial(page):
                    if not _is_blocked(page):
                        return True
                if not _is_blocked(page):
                    return True
            except Exception:
                # Catch net::ERR_ABORTED etc. from reload during meta-refresh
                pass
        else:
            log.warning(f"  Blocked after {retries} attempts: {url}")
    return False


def _load_known_listings(listing_type: str = 'sale') -> dict:
    """Load source_id -> price_amount for all casa_sapo listings in the DB.
    Used to skip unchanged listings and detect price changes."""
    table = "rentals" if listing_type == "rent" else "sales"
    try:
        conn = db.get_connection()
        rows = conn.execute(
            f"SELECT source_id, price_amount FROM {table} WHERE source='casa_sapo'"
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
        "button:has-text('Aceitar Todos')",
        "button:has-text('Accept')",
        "button:has-text('Agree')",
        "button:has-text('Concordo')",
        "[data-cy='cookie-consent-accept']",
        ".cmp-btn-accept",
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
    for raw in scripts:
        try:
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            results.extend(items)
        except (json.JSONDecodeError, AttributeError):
            continue
    return results


def extract_json_ld_listing(page: Page) -> dict:
    """Extract listing data from JSON-LD schemas (Offer, Product, RealEstateListing)."""
    items = extract_json_ld(page)
    result = {}

    for item in items:
        t = item.get("@type", "")
        types = t if isinstance(t, list) else [t]

        # RealEstateListing or Product with real estate data
        if any(tp in types for tp in ["RealEstateListing", "Product", "Residence",
                                       "Apartment", "House", "SingleFamilyResidence"]):
            # Price from offers
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = _num(offers.get("price") or item.get("price"))
            if price:
                result["price"] = price

            # Description
            desc = item.get("description")
            if desc:
                result["description"] = str(desc)[:1000]

            # Name / title
            name = item.get("name")
            if name:
                result["title"] = name

            # Images
            images = item.get("image") or []
            if isinstance(images, str):
                images = [images]
            elif isinstance(images, list):
                images = [
                    (i if isinstance(i, str) else i.get("url") or i.get("contentUrl"))
                    for i in images if i
                ]
            result["images"] = [u for u in images if u and u.startswith("http")][:MAX_IMAGES]

            # Floor size
            floor_size = item.get("floorSize") or {}
            if isinstance(floor_size, dict):
                size = _num(floor_size.get("value"))
                if size:
                    result["size"] = size

            # Address
            address_obj = item.get("address") or {}
            if isinstance(address_obj, dict):
                result["address"] = address_obj.get("streetAddress")
                result["postal_code"] = address_obj.get("postalCode")
                result["city"] = address_obj.get("addressLocality")
                result["district"] = address_obj.get("addressRegion")

            # Geo coordinates
            geo = item.get("geo") or {}
            if isinstance(geo, dict):
                result["lat"] = _coord(geo.get("latitude"))
                result["lon"] = _coord(geo.get("longitude"))

            # Number of rooms/bedrooms
            if item.get("numberOfRooms"):
                result["rooms"] = _int(item["numberOfRooms"])
                result["bedrooms"] = result["rooms"]
            if item.get("numberOfBedrooms"):
                result["bedrooms"] = _int(item["numberOfBedrooms"])
            if item.get("numberOfBathroomsTotal"):
                result["bathrooms"] = _int(item["numberOfBathroomsTotal"])

        # Offer schema (standalone)
        if "Offer" in types and not result.get("price"):
            price = _num(item.get("price"))
            if price:
                result["price"] = price

    return result


# ── Search results: collect detail URLs ───────────────────────────────────────

def extract_search_listings(page: Page) -> list:
    """Extract listing URLs and preview prices from a search results page.
    Returns list of dicts: {"url": str, "price": float|None}.

    CASA SAPO uses server-rendered listing cards. We look for anchor links
    that point to individual property pages."""
    try:
        items = page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // CASA SAPO listing cards — try multiple selector patterns
                // The site uses property cards with links to detail pages
                const cards = document.querySelectorAll(
                    '.property-list .property-info a[href], ' +
                    '.searchResultProperty a[href], ' +
                    'a[href*="/comprar-"][href*="-lisboa/"], ' +
                    'a[href*="/arrendar-"][href*="-lisboa/"], ' +
                    '.property a[href], ' +
                    'article a[href], ' +
                    '.result-item a[href]'
                );

                for (const a of cards) {
                    const href = a.href;
                    // Only detail pages — they contain a long numeric/alphanumeric ID
                    // Skip search/category pages
                    if (!href || seen.has(href)) continue;
                    if (href.includes('casa.sapo.pt') && href.match(/\\/[a-z]+-[a-z]+-[^/]+-[^/]+\\/[a-zA-Z0-9-]+$/)) {
                        // Looks like a detail page URL
                    } else if (href.match(/\\/detalhe\\//) || href.match(/[?&]id=/) || href.match(/\\/[0-9a-f]{20,}/)) {
                        // Alternative URL patterns with ID
                    } else {
                        // Try to detect by structure: detail pages have specific path depth
                        const path = new URL(href).pathname;
                        const segments = path.split('/').filter(Boolean);
                        // Detail pages typically have more segments than search pages
                        if (segments.length < 3) continue;
                    }
                    seen.add(href);

                    // Try to grab the price from the card
                    let priceText = null;
                    const card = a.closest(
                        '.property, article, .searchResultProperty, ' +
                        '.result-item, .property-info, [class*="listing"], li'
                    ) || a;
                    const priceEl = card.querySelector(
                        '.property-price, .price, [class*="price"], ' +
                        '[class*="Price"], strong[class*="price"], ' +
                        'span[class*="value"]'
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
    except Exception as e:
        log.warning(f"  extract_search_listings error: {e}")
        return []


def _extract_source_id(url: str) -> str:
    """Extract the CASA SAPO listing ID from URL.

    CASA SAPO URLs look like:
        /comprar-apartamento-t2-lisboa-arroios/XXXXXXXX
        /comprar-apartamento-t3-.../abc123def456

    The last path segment is typically the unique listing ID."""
    try:
        path = url.rstrip("/").split("?")[0]
        segments = path.split("/")
        if segments:
            last = segments[-1]
            # The last segment is the ID (alphanumeric, possibly with hyphens)
            if last and len(last) >= 6:
                return last
    except Exception:
        pass
    # Fallback: hash the URL
    return hashlib.sha1(url.encode()).hexdigest()[:16]


# ── Detail page extraction from DOM ──────────────────────────────────────────

def extract_from_dom(page: Page) -> dict:
    """Extract listing data from DOM elements on a CASA SAPO detail page.

    CASA SAPO is server-rendered (eGO Real Estate platform) with no __NEXT_DATA__.
    Data is in standard HTML elements: price, features list, description, gallery."""
    result = {}

    try:
        data = page.evaluate(r"""
            () => {
                const out = {};

                // Title — typically h1 or prominent heading
                const titleEl = document.querySelector(
                    'h1, .property-title, [class*="title"] h1, ' +
                    '.detail-title, .property-detail h1'
                );
                if (titleEl) out.title = titleEl.textContent.trim();

                // Price — prominent element
                const priceEl = document.querySelector(
                    '.property-price, .detail-price, [class*="price"] .value, ' +
                    '[class*="Price"], .price, [class*="price"]:not(nav *), ' +
                    'span[class*="price"], strong[class*="price"], ' +
                    'h2[class*="price"], .property-value'
                );
                if (priceEl) out.priceText = priceEl.textContent.trim();

                // Features/characteristics — look for structured feature lists
                const featureEls = document.querySelectorAll(
                    '.property-features li, .detail-features li, ' +
                    '.property-info li, [class*="feature"] li, ' +
                    '.characteristics li, [class*="characteristic"] li, ' +
                    '.property-details li, .detail-info li'
                );
                out.features = Array.from(featureEls).map(el => el.textContent.trim());

                // Also look for labeled key-value pairs
                const kvPairs = document.querySelectorAll(
                    'dt, .label, [class*="label"]'
                );
                const kvValues = document.querySelectorAll(
                    'dd, .value, [class*="value"]'
                );
                out.kvLabels = Array.from(kvPairs).map(el => el.textContent.trim());
                out.kvValues = Array.from(kvValues).map(el => el.textContent.trim());

                // Description
                const descEl = document.querySelector(
                    '.property-description, .detail-description, ' +
                    '[class*="description"] p, [class*="description"], ' +
                    '.comment p, [class*="comment"] p'
                );
                if (descEl) out.description = descEl.textContent.trim().slice(0, 1000);

                // Address / location
                const addrEl = document.querySelector(
                    '.property-location, .detail-location, ' +
                    '[class*="location"], .address, [class*="address"]'
                );
                if (addrEl) out.address = addrEl.textContent.trim();

                // Breadcrumbs
                const breadcrumbs = document.querySelectorAll(
                    'nav.breadcrumb a, .breadcrumb a, [class*="breadcrumb"] a, ' +
                    'ol.breadcrumb li a, ul.breadcrumb li a'
                );
                out.breadcrumbs = Array.from(breadcrumbs).map(a => a.textContent.trim());

                // Images — gallery/carousel
                const imgs = document.querySelectorAll(
                    '.property-gallery img, .detail-gallery img, ' +
                    '[class*="gallery"] img, [class*="carousel"] img, ' +
                    '.property-photos img, [class*="slider"] img, ' +
                    '.photo-gallery img, picture img'
                );
                out.images = Array.from(imgs)
                    .map(i => i.src || i.dataset.src || i.getAttribute('data-lazy') || '')
                    .filter(s => s.startsWith('http'));

                // Coordinates — from map iframe, data attributes, or inline scripts
                const mapEl = document.querySelector(
                    '[data-lat], [data-latitude], iframe[src*="maps"], ' +
                    '#map, .property-map, [class*="map"]'
                );
                if (mapEl) {
                    const lat = mapEl.dataset.lat || mapEl.dataset.latitude ||
                                mapEl.getAttribute('data-lat') || mapEl.getAttribute('data-latitude');
                    const lon = mapEl.dataset.lon || mapEl.dataset.lng ||
                                mapEl.dataset.longitude ||
                                mapEl.getAttribute('data-lon') || mapEl.getAttribute('data-lng') ||
                                mapEl.getAttribute('data-longitude');
                    if (lat) out.lat = lat;
                    if (lon) out.lon = lon;
                }

                // Try coordinates from inline scripts
                if (!out.lat) {
                    const scripts = document.querySelectorAll('script:not([src])');
                    for (const s of scripts) {
                        const t = s.textContent || '';
                        // Look for lat/lng patterns in JS
                        const latMatch = t.match(/[Ll]at(?:itude)?\s*[:=]\s*([-]?\d+\.\d+)/);
                        const lonMatch = t.match(/[Ll](?:on|ng|ongitude)\s*[:=]\s*([-]?\d+\.\d+)/);
                        if (latMatch && lonMatch) {
                            out.lat = latMatch[1];
                            out.lon = lonMatch[1];
                            break;
                        }
                        // Google Maps center pattern
                        const centerMatch = t.match(/center=([-\d.]+)%2C([-\d.]+)/);
                        if (centerMatch) {
                            out.lat = centerMatch[1];
                            out.lon = centerMatch[2];
                            break;
                        }
                        // LatLng constructor
                        const latlngMatch = t.match(/LatLng\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)/);
                        if (latlngMatch) {
                            out.lat = latlngMatch[1];
                            out.lon = latlngMatch[2];
                            break;
                        }
                    }
                }

                // Try to find area/rooms/typology from any structured data on the page
                const allText = document.body ? document.body.innerText : '';
                // Look for typology T0-T9+
                const typoMatch = allText.match(/\b(T\d\+?)\b/);
                if (typoMatch) out.typology = typoMatch[1];

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

    # Preserve raw feature chips + key-value pairs for property_score scoring
    raw_chips = []
    for f in (data.get("features") or []):
        if f and str(f).strip():
            raw_chips.append(str(f).strip())
    kv_labels = data.get("kvLabels") or []
    kv_values = data.get("kvValues") or []
    for label, value in zip(kv_labels, kv_values):
        lbl = (label or "").strip()
        val = (value or "").strip()
        if lbl and val:
            raw_chips.append(f"{lbl}: {val}")
        elif lbl:
            raw_chips.append(lbl)
    if raw_chips:
        result["feature_chips"] = raw_chips

    # Parse features for area, rooms, floor, condition
    for feat in (data.get("features") or []):
        fl = feat.lower()
        if "m²" in feat or "m2" in fl:
            m = re.search(r"([\d.,]+)\s*m", feat)
            if m:
                val = _num(m.group(1))
                if "brut" in fl:
                    result["gross_area"] = val
                    if not result.get("size"):
                        result["size"] = val
                elif "útil" in fl or "util" in fl:
                    result["size"] = val
                else:
                    if not result.get("size"):
                        result["size"] = val
        elif re.match(r"T\d", feat.strip()):
            rooms = _int(feat)
            result["rooms"] = rooms
            result["bedrooms"] = rooms
        elif "quarto" in fl:
            rooms = _int(feat)
            if rooms:
                result["bedrooms"] = rooms
                if not result.get("rooms"):
                    result["rooms"] = rooms
        elif "casa de banho" in fl or "wc" in fl:
            result["bathrooms"] = _int(feat)
        elif "andar" in fl or "piso" in fl:
            m = re.search(r"(\d+)", feat)
            if m:
                result["floor"] = m.group(1)
            elif "rés" in fl or "r/c" in fl:
                result["floor"] = "0"
        elif any(w in fl for w in ["renovado", "remodelado", "novo", "bom estado",
                                    "usado", "para recuperar", "em construção"]):
            result["condition"] = _normalize_condition(feat)

    # Parse key-value pairs
    labels = data.get("kvLabels") or []
    values = data.get("kvValues") or []
    for label, value in zip(labels, values):
        ll = label.lower()
        if "área" in ll or "area" in ll:
            if "brut" in ll:
                result["gross_area"] = _num(value)
            elif "útil" in ll or "util" in ll:
                result["size"] = _num(value)
            elif not result.get("size"):
                result["size"] = _num(value)
        elif "tipologia" in ll or "quarto" in ll:
            rooms = _int(value)
            if rooms is not None:
                result["rooms"] = rooms
                result["bedrooms"] = rooms
        elif "casa de banho" in ll or "wc" in ll:
            result["bathrooms"] = _int(value)
        elif "andar" in ll or "piso" in ll:
            m = re.search(r"(\d+)", value)
            if m:
                result["floor"] = m.group(1)
            elif "rés" in value.lower() or "r/c" in value.lower():
                result["floor"] = "0"
        elif "estado" in ll or "condição" in ll or "condicao" in ll:
            result["condition"] = _normalize_condition(value)

    # Typology from page text
    if not result.get("rooms") and data.get("typology"):
        rooms = _int(data["typology"])
        result["rooms"] = rooms
        result["bedrooms"] = rooms

    # Address
    if data.get("address"):
        result["address"] = data["address"]

    # Location from breadcrumbs: typically [Home, District, City/Concelho, Freguesia, ...]
    breadcrumbs = data.get("breadcrumbs") or []
    if len(breadcrumbs) >= 3:
        result["district"] = breadcrumbs[1] if len(breadcrumbs) > 1 else None
        result["city"] = breadcrumbs[2] if len(breadcrumbs) > 2 else None
        result["neighborhood"] = breadcrumbs[3] if len(breadcrumbs) > 3 else None

    # Coordinates
    if data.get("lat"):
        result["lat"] = _coord(data["lat"])
    if data.get("lon"):
        result["lon"] = _coord(data["lon"])

    # Images
    if data.get("images"):
        result["images"] = data["images"][:MAX_IMAGES]

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

    # Try JSON-LD first (CASA SAPO often has Offer/Product schemas)
    ld = extract_json_ld_listing(page)

    # DOM fallback if JSON-LD didn't yield a price
    dom = extract_from_dom(page) if not ld.get("price") else {}

    # If JSON-LD had price but DOM might have more fields, still try DOM
    if ld.get("price") and not ld.get("rooms"):
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

    price_per_sqm = round(price / size, 2) if price and size and size > 0 else None

    return Listing(
        source="casa_sapo",
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
        neighborhood=ld.get("neighborhood") or dom.get("neighborhood"),
        parish=None,
        district=district,
        city=city,
        lat=lat,
        lon=lon,
        images=json.dumps(images) if images else None,
        hash_dedupe=_hash(address, city, price, size),
        description=ld.get("description") or dom.get("description"),
        feature_chips=json.dumps(dom.get("feature_chips")) if dom.get("feature_chips") else None,
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
    # CASA SAPO uses ?pn=2, ?pn=3 etc. for pagination
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}pn={page_num}"


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
    run_id = db.start_scrape_run("casa_sapo")

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
                "Chrome/131.0.0.0 Safari/537.36"
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
        log.info("Warming up session on casa.sapo.pt...")
        page.goto(BASE_URL, timeout=30000)
        accept_cookies(page)
        _polite_delay()

        # ── Collect listings from search pages ─────────────────────────────
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

                # Wait for content to be available
                try:
                    page.wait_for_selector(
                        "script[type='application/ld+json'], h1, .property-price",
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
            source="casa_sapo", listing_type=listing_type,
            seen_source_ids=seen_source_ids, run_id=run_id,
        )
        log.info(f"Missing detection: {missing_result}")

    # ── Rebuild stats and close run ───────────────────────────────────────────
    db.rebuild_neighborhoods()

    run = ScrapeRun(
        source="casa_sapo",
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
    parser = argparse.ArgumentParser(description="casa.sapo.pt scraper")
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
            print("Browser is open. Solve any challenge on casa.sapo.pt,")
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
