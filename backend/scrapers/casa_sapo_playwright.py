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
from urllib.parse import urlparse, parse_qs, unquote

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


_RELEVANT_LD_TYPES = {
    "RealEstateListing", "Product", "Residence", "Apartment", "House",
    "SingleFamilyResidence", "Offer",
}


def _first_price(v) -> Optional[float]:
    """Parse a price value that may be a string, number, or (as Casa Sapo does)
    a single-element list of strings like ``['680.000 €']``."""
    if isinstance(v, list):
        v = v[0] if v else None
    return _num(v)


def extract_json_ld_listing(page: Page) -> dict:
    """Extract listing data from JSON-LD schemas.

    CASA SAPO emits an ``@type: "Offer"`` object whose ``availableAtOrFrom``
    field carries the address and geo-coordinates. This function also still
    supports the more standard RealEstateListing/Product/Apartment types for
    robustness.
    """
    items = extract_json_ld(page)
    result = {}

    for item in items:
        t = item.get("@type", "")
        types = t if isinstance(t, list) else [t]
        if not any(tp in _RELEVANT_LD_TYPES for tp in types):
            continue

        # Price — may be top-level, inside offers, or a list of strings w/ "€"
        if not result.get("price"):
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = _first_price(
                (offers.get("price") if isinstance(offers, dict) else None)
                or item.get("price")
            )
            if price:
                result["price"] = price

        # Description — prefer the first/longest across scripts
        desc = item.get("description")
        if desc and not result.get("description"):
            # CASA SAPO injects "<br/>" tags inside descriptions
            desc = re.sub(r"<br\s*/?>", "\n", str(desc))
            result["description"] = desc[:1000]

        # Title / name
        if item.get("name") and not result.get("title"):
            result["title"] = str(item["name"])

        # Images (may be a single string, an array, or ImageObject dicts)
        images = item.get("image") or []
        if isinstance(images, str):
            images = [images]
        elif isinstance(images, list):
            images = [
                (i if isinstance(i, str) else (i.get("url") or i.get("contentUrl")))
                for i in images if i
            ]
        good_imgs = [u for u in images if u and isinstance(u, str) and u.startswith("http")]
        if good_imgs and not result.get("images"):
            result["images"] = good_imgs[:MAX_IMAGES]

        # Floor size (rarely present on Casa Sapo but cheap to support)
        floor_size = item.get("floorSize") or {}
        if isinstance(floor_size, dict):
            size = _num(floor_size.get("value"))
            if size and not result.get("size"):
                result["size"] = size

        # Address can be at the top level, or nested under availableAtOrFrom.address
        # CASA SAPO uses the nested Place form.
        address_obj = item.get("address")
        geo = item.get("geo")
        available = item.get("availableAtOrFrom")
        if isinstance(available, dict):
            address_obj = address_obj or available.get("address")
            geo = geo or available.get("geo")

        if isinstance(address_obj, dict):
            if not result.get("address") and address_obj.get("streetAddress"):
                result["address"] = address_obj.get("streetAddress")
            if not result.get("postal_code"):
                result["postal_code"] = address_obj.get("postalCode")
            # Casa Sapo puts city in addressLocality and the *neighborhood* in
            # addressRegion — not a district. Use addressRegion as the
            # neighborhood and keep the district empty so it falls back to
            # "Lisboa" later.
            if not result.get("city"):
                result["city"] = address_obj.get("addressLocality")
            if not result.get("neighborhood"):
                result["neighborhood"] = address_obj.get("addressRegion")

        if isinstance(geo, dict):
            lat = _coord(geo.get("latitude"))
            lon = _coord(geo.get("longitude"))
            if lat is not None and not result.get("lat"):
                result["lat"] = lat
            if lon is not None and not result.get("lon"):
                result["lon"] = lon

        # Rooms / bedrooms / bathrooms — generic fallbacks
        if item.get("numberOfRooms") and not result.get("rooms"):
            result["rooms"] = _int(item["numberOfRooms"])
            result["bedrooms"] = result["rooms"]
        if item.get("numberOfBedrooms") and not result.get("bedrooms"):
            result["bedrooms"] = _int(item["numberOfBedrooms"])
        if item.get("numberOfBathroomsTotal") and not result.get("bathrooms"):
            result["bathrooms"] = _int(item["numberOfBathroomsTotal"])

    return result


# ── Search results: collect detail URLs ───────────────────────────────────────

def extract_search_listings(page: Page) -> list:
    """Extract listing cards from a CASA SAPO search results page.

    Returns list of dicts with:
        url            — real detail URL (counter.aspx wrapper unwrapped)
        source_id      — listing UUID (from onclick or from URL)
        price          — preview price (float) or None
        location       — breadcrumb-style location string from the card
        property_type  — e.g. "Apartamento T4"
        features_text  — raw "Recuperado  ·  107m²" card snippet
    """
    try:
        items = page.evaluate(r"""
            () => {
                const results = [];
                const seen = new Set();

                // Listing cards are <a class="property-info"> inside a
                // .property-info-content wrapper. The wrapper also holds the
                // sibling .property-price element.
                const anchors = document.querySelectorAll('a.property-info[href]');

                for (const a of anchors) {
                    const href = a.href;
                    if (!href || seen.has(href)) continue;

                    // Skip obviously non-listing anchors (categories, footers…).
                    // Listing anchors point to counter.aspx or directly to casa.sapo.pt
                    // detail pages.
                    const isCounter = href.includes('counter.aspx');
                    const isDetail = /casa\.sapo\.pt\/(comprar|arrendar)-[^\/]+-.+\.html/.test(href);
                    if (!isCounter && !isDetail) continue;

                    seen.add(href);

                    // Source ID from Search.setLastSearch('<uuid>')
                    let sourceId = null;
                    const onclick = a.getAttribute('onclick') || '';
                    const oc = onclick.match(/setLastSearch\(\s*['"]([a-f0-9-]{16,})['"]/i);
                    if (oc) sourceId = oc[1];

                    // Type / location / feature snippet from the card
                    const typeEl = a.querySelector('.property-type');
                    const locEl  = a.querySelector('.property-location');
                    const featEl = a.querySelector('.property-features');

                    // Price lives OUTSIDE the anchor, in the surrounding
                    // .property-info-content wrapper.
                    const wrapper = a.closest('.property-info-content')
                                 || a.closest('[class*="property"]')
                                 || a.parentElement;
                    const priceEl = wrapper
                        ? wrapper.querySelector('.property-price, [class*="price"]')
                        : null;

                    results.push({
                        url: href,
                        source_id: sourceId,
                        priceText: priceEl ? priceEl.textContent.trim() : null,
                        location: typeEl || locEl
                            ? ((locEl && locEl.textContent.trim()) || null)
                            : null,
                        property_type: typeEl ? typeEl.textContent.trim() : null,
                        features_text: featEl ? featEl.textContent.trim() : null,
                    });
                }
                return results;
            }
        """)
        # Unwrap counter URLs and parse prices
        for item in items:
            item["url"] = _unwrap_counter_url(item["url"])
            if not item.get("source_id"):
                item["source_id"] = _extract_source_id(item["url"])
            item["price"] = _num(item.pop("priceText", None))
        return items
    except Exception as e:
        log.warning(f"  extract_search_listings error: {e}")
        return []


# CASA SAPO detail URL → embedded listing UUID
#   /comprar-apartamento-t4-lisboa-alvalade-8227ac5a-0db8-11f1-9e61-060000000056.html
_UUID_RE = re.compile(r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})")


def _unwrap_counter_url(url: str) -> str:
    """Casa Sapo wraps every listing link in a gespub.casa.sapo.pt/counter.aspx
    tracker with the real URL in the `l=` query param. Unwrap it so navigation
    hits the detail page directly (and bypasses the 'Site Offline' interstitial).
    """
    if not url or "counter.aspx" not in url:
        return url
    try:
        qs = parse_qs(urlparse(url).query)
        raw = qs.get("l", [None])[0]
        if not raw:
            return url
        # parse_qs returns the value already decoded once; if it still looks
        # percent-encoded, decode again.
        real = unquote(raw) if "%" in raw else raw
        return real if real.startswith("http") else url
    except Exception:
        return url


def _extract_source_id(url: str) -> str:
    """Extract the CASA SAPO listing UUID from a detail URL.

    Handles both direct casa.sapo.pt detail URLs and counter.aspx wrapper URLs.
    Real detail URLs look like:
        https://casa.sapo.pt/comprar-apartamento-t4-lisboa-alvalade-
            8227ac5a-0db8-11f1-9e61-060000000056.html
    where the 36-char UUID before `.html` is the unique listing ID.
    """
    real = _unwrap_counter_url(url)
    # Try UUID match first (most reliable)
    m = _UUID_RE.search(real)
    if m:
        return m.group(1)
    # Fallback: strip query / fragment and use the last path segment
    try:
        path = real.split("?")[0].split("#")[0].rstrip("/")
        last = path.split("/")[-1]
        # Strip trailing .html
        if last.endswith(".html"):
            last = last[:-5]
        # Avoid degenerate values like "counter.aspx"
        if last and len(last) >= 8 and last not in ("counter.aspx", "counter"):
            return last
    except Exception:
        pass
    return hashlib.sha1(real.encode()).hexdigest()[:16]


# ── Detail page extraction from DOM ──────────────────────────────────────────

def _parse_typology(text: str) -> Optional[int]:
    """Extract the bedroom count from a typology string like ``T3`` or
    ``Apartamento T4 +1``. Returns None if no ``T<digit>`` pattern is found.
    Crucially this does NOT fall back to ``_int()``, which would pick up the
    first number in the string (e.g. a price) when the typology is missing."""
    if not text:
        return None
    m = re.search(r"\bT(\d+)\b", text)
    return int(m.group(1)) if m else None


def extract_from_dom(page: Page) -> dict:
    """Extract listing data from DOM elements on a CASA SAPO detail page.

    CASA SAPO is server-rendered (eGO Real Estate platform). The detail page
    uses ``.detail-*`` classes:
        .detail-title-price-value   — price, e.g. "680.000 €"
        .detail-title-location      — "Alvalade, Lisboa, Distrito de Lisboa"
        .detail-main-features-item  — title/value pairs (Área útil, Estado, …)
        .detail-features            — additional characteristics block
        .detail-description-text    — long description
    """
    result = {}

    try:
        data = page.evaluate(r"""
            () => {
                const out = {};
                const txt = (el) => el ? el.textContent.trim() : null;

                // Title
                out.title = txt(document.querySelector('h1'));

                // Price
                out.priceText = txt(document.querySelector(
                    '.detail-title-price-value, .detail-fixed-navbar-price'
                ));

                // Location line — "Neighborhood, City[, District]"
                out.location = txt(document.querySelector('.detail-title-location'));

                // Main features — title/value pairs in .detail-main-features-item
                const mainFeatures = Array.from(
                    document.querySelectorAll('.detail-main-features-item')
                ).map(el => ({
                    title: txt(el.querySelector('.detail-main-features-item-title')),
                    value: txt(el.querySelector('.detail-main-features-item-value')),
                }));
                out.mainFeatures = mainFeatures;

                // Extra characteristics block — free-form lines
                const detailSection = document.querySelector('.detail-features');
                out.detailFeaturesText = detailSection
                    ? detailSection.textContent.replace(/\s+/g, ' ').trim().slice(0, 3000)
                    : null;

                // Description
                out.description = txt(document.querySelector(
                    '.detail-description-text, .detail-description'
                ));
                if (out.description) out.description = out.description.slice(0, 1000);

                // Images — gallery / carousel
                const imgs = document.querySelectorAll(
                    '.detail-main-gallery img, .property-gallery img, ' +
                    '[class*="gallery"] img, [class*="swiper"] img, picture img'
                );
                const seenSrc = new Set();
                out.images = [];
                for (const i of imgs) {
                    const src = i.src || i.dataset.src || i.getAttribute('data-lazy') || '';
                    if (src.startsWith('http') && !seenSrc.has(src)) {
                        seenSrc.add(src);
                        out.images.push(src);
                    }
                }

                // Breadcrumbs (rarely present but useful fallback)
                out.breadcrumbs = Array.from(document.querySelectorAll(
                    '.breadcrumb a, [class*="breadcrumb"] a'
                )).map(a => a.textContent.trim()).filter(t => t.length);

                return out;
            }
        """)
    except Exception as e:
        log.warning(f"  DOM extraction JS failed: {e}")
        return result

    if data.get("priceText"):
        result["price"] = _num(data["priceText"])

    if data.get("title"):
        result["title"] = data["title"]
        # Pull typology straight from the H1, which reliably contains e.g.
        # "Apartamento T4 para comprar em Lisboa"
        rooms = _parse_typology(data["title"])
        if rooms is not None:
            result["rooms"] = rooms
            result["bedrooms"] = rooms
        # Property type from title
        tl = data["title"].lower()
        for pt in ["apartamento", "moradia", "vivenda", "loft", "duplex",
                   "penthouse", "estúdio", "loja", "terreno", "escritório",
                   "garagem", "armazém", "prédio"]:
            if pt in tl:
                result["property_type"] = _normalize_property_type(pt)
                break

    if data.get("description"):
        result["description"] = data["description"]

    # Main features (Área útil / Área bruta / Estado / …)
    for feat in (data.get("mainFeatures") or []):
        title = (feat.get("title") or "").strip()
        value = (feat.get("value") or "").strip()
        if not title or not value:
            continue
        tl = title.lower()
        vl = value.lower()

        if "área" in tl or "area" in tl:
            size = _num(value)
            if size:
                if "brut" in tl:
                    result["gross_area"] = size
                    if not result.get("size"):
                        result["size"] = size
                else:  # "útil" or bare "Área"
                    result["size"] = size
        elif "estado" in tl or "condi" in tl:
            result["condition"] = _normalize_condition(value)
        elif "tipologia" in tl:
            rooms = _parse_typology(value) or _int(value)
            if rooms is not None:
                result["rooms"] = rooms
                result["bedrooms"] = rooms
        elif "andar" in tl or "piso" in tl:
            m = re.search(r"(\d+)", value)
            if m:
                result["floor"] = m.group(1)
            elif "rés" in vl or "r/c" in vl:
                result["floor"] = "0"

    # Extra characteristics — scan once for bathroom / floor info
    details_text = (data.get("detailFeaturesText") or "").lower()
    if details_text:
        if not result.get("bathrooms"):
            m = re.search(r"casa\(?s?\)?\s*de\s*banho[:\s]*(\d+)", details_text)
            if m:
                result["bathrooms"] = int(m.group(1))
        if not result.get("floor"):
            m = re.search(r"andar[:\s]*(\d+)", details_text)
            if m:
                result["floor"] = m.group(1)

    # Location — "Neighborhood, City, District"
    loc = data.get("location")
    if loc:
        parts = [p.strip() for p in loc.split(",") if p.strip()]
        if parts:
            result["neighborhood"] = parts[0]
        if len(parts) >= 2:
            result["city"] = parts[1]
        if len(parts) >= 3:
            result["district"] = parts[2]

    # Breadcrumb fallback
    if not result.get("neighborhood"):
        breadcrumbs = data.get("breadcrumbs") or []
        if len(breadcrumbs) >= 3:
            result["district"] = result.get("district") or breadcrumbs[1]
            result["city"] = result.get("city") or breadcrumbs[2]
            if len(breadcrumbs) > 3:
                result["neighborhood"] = breadcrumbs[3]

    if data.get("images"):
        result["images"] = data["images"][:MAX_IMAGES]

    return result


# ── Detail page scraper ───────────────────────────────────────────────────────

def _parse_card_features(text: Optional[str]) -> dict:
    """Parse a search-card feature string like "Recuperado  ·  107m²" into
    a partial result dict."""
    out: dict = {}
    if not text:
        return out
    for part in re.split(r"[·•|]", text):
        p = part.strip()
        if not p:
            continue
        pl = p.lower()
        if "m²" in p or "m2" in pl:
            m = re.search(r"([\d.,]+)\s*m", p)
            if m:
                out["size"] = _num(m.group(1))
        elif any(w in pl for w in ["renovado", "recuperado", "remodelado", "novo",
                                    "bom estado", "usado", "para recuperar",
                                    "em construção"]):
            out["condition"] = _normalize_condition(p)
    return out


def scrape_detail_page(
    page: Page,
    url: str,
    listing_type: str = 'sale',
    hint: Optional[dict] = None,
) -> Optional[Listing]:
    """Visit a listing detail page and return a Listing object.

    ``hint`` — optional dict of values already harvested from the search card
    (``location``, ``property_type``, ``features_text``, ``price``,
    ``source_id``). These fill in anything the detail-page extraction misses.
    """
    from models import detect_listing_type
    listing_type = detect_listing_type(url, fallback=listing_type)
    hint = hint or {}

    source_id = hint.get("source_id") or _extract_source_id(url)

    # Always try both JSON-LD and DOM — they cover different fields on Casa Sapo.
    ld = extract_json_ld_listing(page)
    dom = extract_from_dom(page)

    # Merge card hints (location, features, card price) into the result.
    hint_loc_parts = []
    if hint.get("location"):
        hint_loc_parts = [p.strip() for p in hint["location"].split(",") if p.strip()]
    card_feat = _parse_card_features(hint.get("features_text"))

    def pick(*vals):
        for v in vals:
            if v not in (None, ""):
                return v
        return None

    price      = pick(ld.get("price"), dom.get("price"), hint.get("price"))
    size       = pick(ld.get("size"), dom.get("size"), card_feat.get("size"))
    gross_area = pick(ld.get("gross_area"), dom.get("gross_area"))
    lat        = pick(ld.get("lat"), dom.get("lat"))
    lon        = pick(ld.get("lon"), dom.get("lon"))
    address    = pick(ld.get("address"), dom.get("address"))
    postal     = pick(ld.get("postal_code"), dom.get("postal_code"))

    neighborhood = pick(
        dom.get("neighborhood"),
        ld.get("neighborhood"),
        hint_loc_parts[0] if hint_loc_parts else None,
    )
    city = pick(
        dom.get("city"),
        ld.get("city"),
        hint_loc_parts[1] if len(hint_loc_parts) > 1 else None,
        "Lisboa",
    )
    district = pick(
        dom.get("district"),
        ld.get("district"),
        hint_loc_parts[2] if len(hint_loc_parts) > 2 else None,
        "Lisboa",
    )

    rooms    = pick(ld.get("rooms"), dom.get("rooms"),
                    _parse_typology(hint.get("property_type")))
    bedrooms = pick(ld.get("bedrooms"), dom.get("bedrooms"), rooms)
    bathrooms = pick(ld.get("bathrooms"), dom.get("bathrooms"))
    floor     = pick(ld.get("floor"), dom.get("floor"))
    condition = pick(
        dom.get("condition"), ld.get("condition"), card_feat.get("condition")
    )
    property_type = pick(
        dom.get("property_type"),
        ld.get("property_type"),
        _normalize_property_type(hint.get("property_type")) if hint.get("property_type") else None,
    )
    title = pick(dom.get("title"), ld.get("title"))
    images = ld.get("images") or dom.get("images") or []

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
        rooms=rooms,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        floor=floor,
        property_type=property_type,
        condition=condition,
        title=title,
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
        items_to_scrape = []
        seen_source_ids = set()
        price_changed = 0
        for item in all_search_items:
            sid = item.get("source_id") or _extract_source_id(item["url"])
            item["source_id"] = sid
            if sid:
                seen_source_ids.add(sid)
            if sid in known_listings:
                old_price = known_listings[sid]
                new_price = item.get("price")
                # Re-scrape if price changed (or if we couldn't read the preview price)
                if not new_price or not old_price or abs(new_price - old_price) > 1:
                    if new_price and old_price:
                        log.info(f"  Price changed for {sid}: {old_price} -> {new_price}")
                    items_to_scrape.append(item)
                    price_changed += 1
                else:
                    skipped_known += 1
            else:
                items_to_scrape.append(item)

        log.info(
            f"Skipping {skipped_known} unchanged listings, "
            f"{price_changed} price changes to update, "
            f"{len(items_to_scrape) - price_changed} new to scrape"
        )

        # ── Visit each detail page ────────────────────────────────────────────
        for i, item in enumerate(items_to_scrape):
            if total_pushed >= max_items:
                log.info(f"Reached max_items={max_items}. Stopping.")
                break

            detail_url = item["url"]  # already unwrapped by extract_search_listings
            log.info(f"[{i+1}/{len(items_to_scrape)}] {detail_url}")

            try:
                if not _goto_with_retry(page, detail_url):
                    log.warning(f"  Blocked on detail page — skipping")
                    error_count += 1
                    continue

                # Wait for content to be available
                try:
                    page.wait_for_selector(
                        "script[type='application/ld+json'], h1, "
                        ".detail-title-price-value",
                        timeout=8000
                    )
                except PWTimeout:
                    pass

                listing = scrape_detail_page(
                    page, detail_url, listing_type=listing_type, hint=item
                )

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
