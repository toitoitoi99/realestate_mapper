"""
era_playwright.py — Standalone era.pt scraper using Python Playwright.

Setup (one-time):
    pip install playwright
    playwright install chromium

Run:
    python scrapers/era_playwright.py --setup          # One-time: open Chrome, accept cookies
    python scrapers/era_playwright.py                  # Headless scrape (after --setup)
    python scrapers/era_playwright.py --max-pages 5    # Quick test run
    python scrapers/era_playwright.py --type rent      # Scrape rentals

The scraper will:
    1. Open a headless Chrome browser with persistent profile
    2. Paginate through ERA search results for Lisboa
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

BASE_URL = "https://www.era.pt"
DEFAULT_SEARCH = "https://www.era.pt/comprar/apartamentos/lisboa"
RENTAL_SEARCH = "https://www.era.pt/arrendar/apartamentos/lisboa"
DEFAULT_MAX_PAGES = 60
DEFAULT_MAX_ITEMS = 1500
MAX_IMAGES = 15
BLOCKED_RETRIES = 3
BLOCKED_WAIT = 8

# Delay between page visits (seconds)
MIN_DELAY = 2.5
MAX_DELAY = 5.5

PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile_era"

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


def _hash(address, city, price, size, source="era") -> str:
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
        if len(content) < 3000:
            return True
        # Common challenge indicators
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
    """Load source_id -> price_amount for all ERA listings in the DB.
    Used to skip unchanged listings and detect price changes."""
    table = "rentals" if listing_type == "rent" else "sales"
    try:
        conn = db.get_connection()
        rows = conn.execute(
            f"SELECT source_id, price_amount FROM {table} WHERE source='era'"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


# ── Cookie banner ─────────────────────────────────────────────────────────────

def accept_cookies(page: Page):
    """Dismiss the cookie/GDPR banner if present."""
    selectors = [
        "button:has-text('Aceitar')",
        "button:has-text('Aceitar tudo')",
        "button:has-text('Aceito')",
        "button:has-text('Accept')",
        "button:has-text('Concordo')",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "[id*='accept'][role='button']",
        ".cookie-consent-accept",
        "button[data-action='accept']",
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

def extract_json_ld(page: Page) -> dict:
    """Extract RealEstateListing or Product JSON-LD from the page."""
    try:
        scripts = page.evaluate("""
            () => Array.from(
                document.querySelectorAll('script[type="application/ld+json"]')
            ).map(s => s.textContent)
        """)
    except Exception:
        return {}

    for raw in scripts:
        try:
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for item in items:
                t = item.get("@type", "")
                if isinstance(t, list):
                    types = t
                else:
                    types = [t]
                # Accept RealEstateListing, Product, Residence, Apartment, etc.
                for target_type in ["RealEstateListing", "Product", "Residence",
                                    "Apartment", "House", "SingleFamilyResidence"]:
                    if target_type in types:
                        return _parse_json_ld(item)
        except (json.JSONDecodeError, AttributeError):
            continue
    return {}


def _parse_json_ld(ld: dict) -> dict:
    """Parse a JSON-LD object into our standard fields dict."""
    about = ld.get("about") or {}
    address = ld.get("address") or about.get("address") or {}
    geo = ld.get("geo") or about.get("geo") or {}

    # Price from offers or direct
    price = None
    offers = ld.get("offers") or {}
    if isinstance(offers, dict):
        price = _num(offers.get("price"))
        if not price:
            specs = offers.get("priceSpecification") or []
            for p in specs:
                price = _num(p.get("price"))
                if price:
                    break
    if not price:
        price = _num(ld.get("price"))

    # Size
    floor_size = ld.get("floorSize") or about.get("floorSize") or {}
    size = _num(floor_size.get("value") if isinstance(floor_size, dict) else floor_size)

    # Images
    imgs = ld.get("image") or ld.get("photo") or []
    if isinstance(imgs, str):
        imgs = [imgs]
    images = []
    for i in imgs:
        if isinstance(i, str):
            images.append(i)
        elif isinstance(i, dict):
            images.append(i.get("url") or i.get("contentUrl") or "")
    images = [u for u in images if u and u.startswith("http")][:MAX_IMAGES]

    return {
        "price": price,
        "size": size,
        "address": address.get("streetAddress"),
        "postal_code": address.get("postalCode"),
        "city": address.get("addressLocality"),
        "district": address.get("addressRegion"),
        "lat": _coord(geo.get("latitude")),
        "lon": _coord(geo.get("longitude")),
        "bedrooms": _int(about.get("numberOfBedrooms") or ld.get("numberOfBedrooms")),
        "bathrooms": _int(about.get("numberOfBathroomsTotal") or ld.get("numberOfBathroomsTotal")),
        "floor": str(about.get("floorLevel")) if about.get("floorLevel") else None,
        "title": ld.get("name"),
        "description": (str(ld.get("description"))[:1000] if ld.get("description") else None),
        "images": images,
    }


# ── Search results: collect detail URLs ───────────────────────────────────────

def extract_search_listings(page: Page) -> list:
    """Extract listing URLs and preview prices from an ERA search results page.
    Returns list of dicts: {"url": str, "price": float|None}.

    ERA renders search results as property cards with links to detail pages.
    Cards may be rendered client-side via React widgets, so we wait for content.
    """
    try:
        # Wait for property cards to render (ERA uses client-side rendering)
        try:
            page.wait_for_selector(
                'a[href*="/comprar/"], a[href*="/arrendar/"], .property-card, '
                '.listing-card, [class*="PropertyCard"], [class*="property-list"]',
                timeout=10000
            )
        except PWTimeout:
            log.warning("  Timeout waiting for property cards to render")

        items = page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // ERA property card links — match detail page URL patterns
                // ERA detail URLs contain a numeric ID, e.g. /comprar/apartamento/t2/lisboa/era-12345
                // or /imovel/12345 patterns
                const allLinks = document.querySelectorAll('a[href]');
                for (const a of allLinks) {
                    const href = a.href;

                    // Match ERA detail page patterns:
                    // - URLs with property type + location + ID
                    // - /comprar/apartamento/... or /arrendar/apartamento/...
                    // Skip search/listing pages, only get individual property links
                    if (!href.includes('era.pt')) continue;

                    // ERA detail pages typically have a longer path with property details
                    // and end with or contain a numeric/alphanumeric ID
                    const isDetailPage = (
                        // Match paths like /comprar/apartamento/t2/cidade/slug-12345
                        (/\/(comprar|arrendar)\/(apartamento|moradia|vivenda|terreno|loja|garagem|armazem|escritorio|andar|quinta|predio)[\/\-]/.test(href) &&
                         href.split('/').length >= 6) ||
                        // Match /imovel/ pattern
                        /\/imovel\/\d+/.test(href)
                    );

                    if (!isDetailPage) continue;
                    if (seen.has(href)) continue;

                    // Skip if this looks like a search/category page (too few path segments)
                    const path = new URL(href).pathname;
                    const segments = path.split('/').filter(s => s.length > 0);
                    if (segments.length < 4) continue;

                    seen.add(href);

                    // Try to get price from the card context
                    let priceText = null;
                    const card = a.closest(
                        '.property-card, .listing-card, article, li, ' +
                        '[class*="PropertyCard"], [class*="card"], ' +
                        '[class*="property"], [class*="listing"]'
                    ) || a;
                    const priceEl = card.querySelector(
                        '[class*="price"], [class*="Price"], ' +
                        '[class*="valor"], [class*="Valor"], ' +
                        'span.price, .property-price, ' +
                        'strong[class*="price"]'
                    );
                    if (priceEl) priceText = priceEl.textContent.trim();

                    // If no price element found, check the link text itself
                    if (!priceText) {
                        const cardText = card.textContent || '';
                        const priceMatch = cardText.match(/([\d.,]+)\s*€/);
                        if (priceMatch) priceText = priceMatch[0];
                    }

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
        log.warning(f"  Error extracting search listings: {e}")
        return []


def _extract_source_id(url: str) -> str:
    """Extract the ERA listing ID from URL.

    ERA listing URLs may contain numeric IDs in various positions:
    - /comprar/apartamento/t2/lisboa/slug-12345
    - /imovel/12345
    - URL path ending with a numeric segment
    """
    # Try /imovel/ID pattern
    m = re.search(r'/imovel/(\d+)', url)
    if m:
        return m.group(1)

    # Try trailing numeric ID in the URL slug (e.g. era-12345 or just 12345)
    m = re.search(r'[/-](\d{4,})(?:\?|$|/)', url)
    if m:
        return m.group(1)

    # Try any long numeric sequence in the path
    m = re.search(r'/(\d{5,})(?:\?|$|/)', url)
    if m:
        return m.group(1)

    # Fallback: hash the URL path to get a stable ID
    path = re.sub(r'\?.*$', '', url)  # strip query params
    return hashlib.sha1(path.encode()).hexdigest()[:16]


# ── Detail page extraction from DOM ──────────────────────────────────────────

def extract_from_dom(page: Page) -> dict:
    """Extract listing data from DOM elements on an ERA detail page.

    ERA is a jQuery/React hybrid site. Data lives in DOM elements
    with price displays, feature lists, breadcrumbs, and map embeds.
    """
    result = {}

    try:
        data = page.evaluate(r"""
            () => {
                const out = {};

                // Title — typically an h1 or prominent heading
                const titleEl = document.querySelector(
                    'h1, [class*="property-title"], [class*="PropertyTitle"], ' +
                    '[class*="detail-title"], [class*="DetailTitle"]'
                );
                if (titleEl) out.title = titleEl.textContent.trim();

                // Price — prominent price display
                const priceEl = document.querySelector(
                    '[class*="price" i], [class*="Price"], ' +
                    '[class*="valor" i], [class*="Valor"], ' +
                    '.property-price, .detail-price, ' +
                    'span.price, strong.price, .preco'
                );
                if (priceEl) out.priceText = priceEl.textContent.trim();

                // Features / characteristics list
                // ERA typically shows T2, area, bathrooms etc. in a features section
                const featureEls = document.querySelectorAll(
                    '[class*="feature"] li, [class*="Feature"] li, ' +
                    '[class*="characteristic"] li, [class*="Characteristic"] li, ' +
                    '[class*="detail-info"] li, [class*="DetailInfo"] li, ' +
                    '.property-features li, .features li, ' +
                    '[class*="amenities"] li, [class*="specs"] li, ' +
                    '[class*="info-property"] span, [class*="property-info"] span'
                );
                out.features = Array.from(featureEls).map(el => el.textContent.trim());

                // Also grab any key-value pairs in detail sections
                const kvPairs = document.querySelectorAll(
                    '[class*="detail"] dt, [class*="detail"] dd, ' +
                    '[class*="Detail"] dt, [class*="Detail"] dd, ' +
                    'table.property-details td, table.property-details th, ' +
                    '.property-details dt, .property-details dd'
                );
                out.kvTexts = Array.from(kvPairs).map(el => el.textContent.trim());

                // Try to find area/size from any text containing "m²"
                const allText = document.body ? document.body.innerText : '';
                const areaMatches = allText.match(/(\d[\d.,]*)\s*m²/g);
                if (areaMatches) out.areaTexts = areaMatches;

                // Typology — look for T0-T9 pattern
                const typoMatch = allText.match(/\bT(\d)\b/);
                if (typoMatch) out.typology = typoMatch[0];

                // Breadcrumbs for location
                const breadcrumbs = document.querySelectorAll(
                    'nav[aria-label*="breadcrumb"] a, ' +
                    '[class*="breadcrumb"] a, .breadcrumb a, ' +
                    '[class*="Breadcrumb"] a, ol.breadcrumb li a'
                );
                out.breadcrumbs = Array.from(breadcrumbs).map(a => a.textContent.trim());

                // Address section
                const addrEl = document.querySelector(
                    '[class*="address" i], [class*="location" i], ' +
                    '[class*="morada" i], .property-location, .property-address'
                );
                if (addrEl) out.address = addrEl.textContent.trim();

                // Description
                const descEl = document.querySelector(
                    '[class*="description" i], [class*="descricao" i], ' +
                    '.property-description, .detail-description, ' +
                    '[class*="Description"] p, [class*="description"] p'
                );
                if (descEl) out.description = descEl.textContent.trim().slice(0, 1000);

                // Images from gallery/carousel
                const imgs = document.querySelectorAll(
                    '[class*="gallery"] img, [class*="Gallery"] img, ' +
                    '[class*="carousel"] img, [class*="Carousel"] img, ' +
                    '[class*="slider"] img, [class*="Slider"] img, ' +
                    '.property-images img, .detail-photos img, ' +
                    'picture img[src*="era"], img[src*="property"], ' +
                    '[class*="photo"] img'
                );
                out.images = Array.from(imgs)
                    .map(i => i.src || i.dataset.src || i.getAttribute('data-lazy') || '')
                    .filter(s => s.startsWith('http'));

                // Also check for background-image URLs on gallery elements
                if (out.images.length === 0) {
                    const bgEls = document.querySelectorAll(
                        '[class*="gallery"] [style*="background"], ' +
                        '[class*="slider"] [style*="background"], ' +
                        '[class*="photo"] [style*="background"]'
                    );
                    for (const el of bgEls) {
                        const style = el.getAttribute('style') || '';
                        const bgMatch = style.match(/url\(['"]?(https?[^'")\s]+)['"]?\)/);
                        if (bgMatch) out.images.push(bgMatch[1]);
                    }
                }

                // Coordinates — check for map data attributes
                const mapEl = document.querySelector(
                    '[data-lat], [data-latitude], ' +
                    '[class*="map" i] [data-lat], ' +
                    '#map, .property-map, [class*="Map"]'
                );
                if (mapEl) {
                    const lat = mapEl.dataset.lat || mapEl.dataset.latitude ||
                                mapEl.getAttribute('data-lat') || mapEl.getAttribute('data-latitude');
                    const lon = mapEl.dataset.lng || mapEl.dataset.lon ||
                                mapEl.dataset.longitude ||
                                mapEl.getAttribute('data-lng') || mapEl.getAttribute('data-lon') ||
                                mapEl.getAttribute('data-longitude');
                    if (lat) out.lat = lat;
                    if (lon) out.lon = lon;
                }

                // Check for Google Maps embed/iframe with coordinates
                if (!out.lat) {
                    const gmapIframe = document.querySelector(
                        'iframe[src*="google.com/maps"], iframe[src*="maps.google"]'
                    );
                    if (gmapIframe) {
                        const src = gmapIframe.getAttribute('src') || '';
                        // ?q=lat,lng or center=lat,lng or @lat,lng
                        const coordMatch = src.match(/[?&](?:q|center|ll)=([-\d.]+)[,%20]+([-\d.]+)/) ||
                                          src.match(/@([-\d.]+),([-\d.]+)/);
                        if (coordMatch) {
                            out.lat = coordMatch[1];
                            out.lon = coordMatch[2];
                        }
                    }
                }

                // Check inline scripts for coordinates
                if (!out.lat) {
                    const scripts = document.querySelectorAll('script:not([src])');
                    for (const s of scripts) {
                        const t = s.textContent || '';
                        // Look for lat/lng in various JS patterns
                        const latMatch = t.match(/["']?lat(?:itude)?["']?\s*[:=]\s*([-]?\d+\.\d+)/);
                        const lngMatch = t.match(/["']?(?:lng|lon|longitude)["']?\s*[:=]\s*([-]?\d+\.\d+)/);
                        if (latMatch && lngMatch) {
                            const lat = parseFloat(latMatch[1]);
                            const lng = parseFloat(lngMatch[1]);
                            // Sanity check for Portugal coordinates
                            if (lat > 36 && lat < 43 && lng > -10 && lng < 0) {
                                out.lat = latMatch[1];
                                out.lon = lngMatch[1];
                                break;
                            }
                        }

                        // Google Maps center=lat%2Clng pattern
                        const centerMatch = t.match(/center=([\d.-]+)%2C([\d.-]+)/);
                        if (centerMatch) {
                            out.lat = centerMatch[1];
                            out.lon = centerMatch[2];
                            break;
                        }
                    }
                }

                // Condition from features/details
                const bodyText = document.body ? document.body.innerText.toLowerCase() : '';
                if (bodyText.includes('novo') || bodyText.includes('em construção')) {
                    out.condition = 'new';
                } else if (bodyText.includes('renovado') || bodyText.includes('remodelado')) {
                    out.condition = 'renovated';
                } else if (bodyText.includes('para recuperar') || bodyText.includes('para remodelar')) {
                    out.condition = 'to_renovate';
                } else if (bodyText.includes('usado') || bodyText.includes('bom estado')) {
                    out.condition = 'used';
                }

                return out;
            }
        """)
    except Exception as e:
        log.warning(f"  DOM extraction JS failed: {e}")
        return result

    # Parse price
    if data.get("priceText"):
        result["price"] = _num(data["priceText"])

    # Parse typology (T0-T9)
    if data.get("typology"):
        rooms = _int(data["typology"])
        result["rooms"] = rooms
        result["bedrooms"] = rooms

    # Parse area from matched text patterns
    area_texts = data.get("areaTexts") or []
    for at in area_texts:
        val = _num(re.search(r"([\d.,]+)", at).group(1) if re.search(r"([\d.,]+)", at) else None)
        if val and val > 5 and val < 10000:
            if not result.get("size"):
                result["size"] = val
            elif not result.get("gross_area") and val != result.get("size"):
                result["gross_area"] = val

    # Parse features list
    for feat in (data.get("features") or []):
        fl = feat.lower()
        if "m²" in feat or "m2" in fl:
            m = re.search(r"([\d.,]+)\s*m", feat)
            if m:
                val = _num(m.group(1))
                if val and val > 5:
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
            result["bedrooms"] = _int(feat)
        elif "casa de banho" in fl or "wc" in fl:
            result["bathrooms"] = _int(feat)
        elif "andar" in fl or "piso" in fl:
            m = re.search(r"(\d+)", feat)
            if m:
                result["floor"] = m.group(1)
            elif "rés" in fl or "r/c" in fl:
                result["floor"] = "0"

    # Parse key-value pairs
    kv_texts = data.get("kvTexts") or []
    for i in range(0, len(kv_texts) - 1, 2):
        key = kv_texts[i].lower()
        val = kv_texts[i + 1] if i + 1 < len(kv_texts) else ""
        if "área" in key and "útil" in key or "area util" in key:
            result["size"] = _num(val)
        elif "área" in key and "brut" in key or "area brut" in key:
            result["gross_area"] = _num(val)
        elif "tipologia" in key:
            rooms = _int(val)
            result["rooms"] = rooms
            result["bedrooms"] = rooms
        elif "quarto" in key:
            result["bedrooms"] = _int(val)
        elif "casa de banho" in key or "wc" in key:
            result["bathrooms"] = _int(val)
        elif "andar" in key or "piso" in key:
            m = re.search(r"(\d+)", val)
            if m:
                result["floor"] = m.group(1)
            elif "rés" in val.lower() or "r/c" in val.lower():
                result["floor"] = "0"
        elif "estado" in key or "condição" in key or "condicao" in key:
            result["condition"] = _normalize_condition(val)

    result["title"] = data.get("title")
    result["description"] = data.get("description")
    result["address"] = data.get("address")

    # Condition from page analysis
    if not result.get("condition") and data.get("condition"):
        result["condition"] = data["condition"]

    # Location from breadcrumbs: typically [Home, Type, District, City, Neighborhood]
    breadcrumbs = data.get("breadcrumbs") or []
    # Filter out generic breadcrumbs
    loc_crumbs = [b for b in breadcrumbs if b.lower() not in
                  ("home", "início", "inicio", "era", "comprar", "arrendar",
                   "vender", "apartamentos", "moradias", "apartamento", "moradia")]
    if len(loc_crumbs) >= 1:
        result.setdefault("city", loc_crumbs[0])
    if len(loc_crumbs) >= 2:
        result.setdefault("neighborhood", loc_crumbs[1])
    if len(loc_crumbs) >= 3:
        result.setdefault("district", loc_crumbs[0])
        result["city"] = loc_crumbs[1]
        result["neighborhood"] = loc_crumbs[2]

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
               "escritório", "escritorio", "garagem", "armazém", "armazem",
               "prédio", "predio", "quinta", "andar"]:
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

    # Try JSON-LD structured data first
    ld = extract_json_ld(page)

    # DOM extraction as primary/fallback
    dom = extract_from_dom(page)

    price      = ld.get("price")      or dom.get("price")
    size       = ld.get("size")       or dom.get("size")
    gross_area = dom.get("gross_area")
    lat        = ld.get("lat")        or dom.get("lat")
    lon        = ld.get("lon")        or dom.get("lon")
    address    = ld.get("address")    or dom.get("address")
    postal     = ld.get("postal_code")
    city       = ld.get("city")       or dom.get("city") or "Lisboa"
    district   = ld.get("district")   or dom.get("district") or "Lisboa"
    bedrooms   = ld.get("bedrooms")   or dom.get("bedrooms")
    bathrooms  = ld.get("bathrooms")  or dom.get("bathrooms")
    floor      = ld.get("floor")      or dom.get("floor")
    images     = ld.get("images")     or dom.get("images") or []

    price_per_sqm = round(price / size, 2) if price and size and size > 0 else None

    return Listing(
        source="era",
        source_id=source_id,
        url=url,
        listing_type=listing_type,
        status='active',
        price_amount=price,
        price_per_sqm=price_per_sqm,
        size_sqm=size,
        gross_area_sqm=gross_area,
        rooms=dom.get("rooms") or ld.get("bedrooms"),
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        floor=floor,
        property_type=dom.get("property_type") or _normalize_property_type(None),
        condition=dom.get("condition") or _normalize_condition(None),
        title=ld.get("title") or dom.get("title"),
        address=address,
        postal_code=postal,
        neighborhood=dom.get("neighborhood"),
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
    if any(w in s for w in ["moradia", "vivenda", "house", "villa", "quinta"]):  return "house"
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
    if any(w in s for w in ["para recuperar", "to_renovate", "recover", "para remodelar"]): return "to_renovate"
    if any(w in s for w in ["em construção", "under_construction"]):    return "new"
    return "used"


# ── Pagination ────────────────────────────────────────────────────────────────

def build_page_url(base_url: str, page_num: int) -> str:
    if page_num == 1:
        return base_url
    # ERA uses ?pag=N query parameter for pagination
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}pag={page_num}"


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
    run_id = db.start_scrape_run("era")

    new_count = updated_count = error_count = 0
    skipped_known = 0
    total_pushed = 0

    # Load already-scraped source_ids with prices so we can skip unchanged ones
    known_listings = _load_known_listings(listing_type)
    log.info(f"Loaded {len(known_listings)} known ERA listings from DB")

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
        log.info("Warming up session on era.pt...")
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

                # Wait for content to render (ERA uses client-side rendering)
                try:
                    page.wait_for_selector(
                        "h1, [class*='price' i], [class*='Price'], "
                        "script[type='application/ld+json']",
                        timeout=10000
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
            source="era", listing_type=listing_type,
            seen_source_ids=seen_source_ids, run_id=run_id,
        )
        log.info(f"Missing detection: {missing_result}")

    # ── Rebuild stats and close run ───────────────────────────────────────────
    db.rebuild_neighborhoods()

    run = ScrapeRun(
        source="era",
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
    parser = argparse.ArgumentParser(description="era.pt scraper")
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
                        help="Open browser for manual session setup, then exit")
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
            print("Browser is open. Accept cookies on era.pt,")
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
