"""
idealista_playwright.py — Standalone Idealista.pt scraper using Python Playwright.
No Apify account needed. Runs entirely on your local machine.

Setup (one-time):
    pip install playwright
    playwright install chromium

Run:
    python scrapers/idealista_playwright.py                 # all Lisboa, default limits
    python scrapers/idealista_playwright.py --max-pages 5  # quick test run
    python scrapers/idealista_playwright.py --url "https://www.idealista.pt/comprar-casas/lisboa/alfama/"

The scraper will:
    1. Open a headless Chromium browser
    2. Paginate through Idealista search results for Lisbon
    3. Visit each listing's detail page
    4. Extract data from __NEXT_DATA__ JSON (embedded Next.js payload) + JSON-LD fallback
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
    # Stub types so the rest of the module parses cleanly
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

BASE_URL        = "https://www.idealista.pt"
DEFAULT_SEARCH  = "https://www.idealista.pt/comprar-casas/lisboa/"
RENTAL_SEARCH   = "https://www.idealista.pt/arrendar-casas/lisboa/"
DEFAULT_MAX_PAGES  = 60
DEFAULT_MAX_ITEMS  = 1500
MAX_IMAGES         = 15
DATADOME_MAX_HTML  = 5000   # pages below this size are DataDome captcha pages
DATADOME_RETRIES   = 3      # retries per URL when DataDome is detected
DATADOME_WAIT      = 8      # seconds to wait before retrying after DataDome

# Delay between page visits (seconds) — be polite
MIN_DELAY = 2.5
MAX_DELAY = 5.5

# ── Lisbon neighborhood slugs for per-neighborhood scraping ──────────────────
# Idealista uses these as URL segments: /comprar-casas/lisboa/{slug}/
# This avoids pagination (each neighborhood fits on page 1).
LISBON_NEIGHBORHOODS = [
    "ajuda", "alcantara", "alfama", "alvalade", "areeiro",
    "arroios", "avenidas-novas", "belem", "benfica", "beato",
    "campolide", "campo-de-ourique", "carnide", "estrela",
    "lumiar", "marvila", "misericordia", "olivais",
    "parque-das-nacoes", "penha-de-franca", "santa-clara",
    "santa-maria-maior", "santo-antonio", "sao-domingos-de-benfica",
    "sao-vicente",
]

# ── Utilities ─────────────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    """Parse Portuguese-formatted number: '250.000' → 250000.0"""
    if v is None:
        return None
    try:
        s = str(v).strip()
        if re.search(r'\.\d{3}($|[^0-9])', s):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", ".")
        n = float(re.sub(r"[^\d.\-]", "", s))
        return n if n != 0 else None
    except (ValueError, TypeError):
        return None


def _coord(v) -> Optional[float]:
    """Parse coordinate — plain float, range-checked."""
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


def _hash(address, city, price, size, source="idealista") -> str:
    addr = (address or "").lower().strip()
    price_r = str(round(price / 1000) * 1000) if price else ""
    size_r  = str(round(size)) if size else ""
    raw = f"{addr}|{(city or '').lower()}|{price_r}|{size_r}|{source}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _polite_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def _is_datadome(page: "Page") -> bool:
    """Check if the current page is a DataDome captcha page."""
    try:
        return len(page.content()) < DATADOME_MAX_HTML
    except Exception:
        return False


def _goto_with_retry(page: "Page", url: str, retries: int = DATADOME_RETRIES) -> bool:
    """Navigate to URL, retrying if DataDome captcha is detected.
    Returns True if page loaded successfully (not a captcha)."""
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
        except PWTimeout:
            log.warning(f"  Timeout loading {url} (attempt {attempt}/{retries})")
            if attempt < retries:
                time.sleep(DATADOME_WAIT)
                continue
            return False

        if not _is_datadome(page):
            return True

        if attempt < retries:
            log.info(f"  DataDome detected (attempt {attempt}/{retries}), waiting {DATADOME_WAIT}s…")
            time.sleep(DATADOME_WAIT)
            # Try reloading instead of fresh goto — sometimes cookies settle
            try:
                page.reload(timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                if not _is_datadome(page):
                    return True
            except PWTimeout:
                pass
        else:
            log.warning(f"  DataDome blocked after {retries} attempts: {url}")
    return False


def _load_known_source_ids(listing_type: str = 'sale') -> set:
    """Load all source_ids already in the DB for idealista, to skip re-fetching."""
    table = "rental_listings" if listing_type == "rent" else "sales_listings"
    try:
        conn = db.get_connection()
        rows = conn.execute(
            f"SELECT source_id FROM {table} WHERE source='idealista'"
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


# ── Cookie banner ─────────────────────────────────────────────────────────────

def accept_cookies(page: Page):
    """Dismiss the cookie/GDPR banner if present."""
    selectors = [
        "button:has-text('Aceitar')",
        "button:has-text('Aceito')",
        "button:has-text('Aceitar tudo')",
        "#didomi-notice-agree-button",
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


def extract_listing_from_next_data(ad: dict) -> dict:
    """Extract listing fields from the Idealista ad object in __NEXT_DATA__."""

    # Price
    price = _num(
        ad.get("price")
        or ad.get("priceInfo", {}).get("amount")
        or ad.get("askingPrice")
    )

    # Size
    size = _num(
        ad.get("size") or ad.get("floorSize") or ad.get("usableArea") or ad.get("area")
    )

    # Rooms (Portuguese typology: T2 → 2)
    rooms_raw = ad.get("rooms") or ad.get("typology") or ad.get("roomNumber")
    rooms = _int(rooms_raw)

    bedrooms = _int(ad.get("bedrooms") or ad.get("noOfBedrooms")) or rooms

    floor = str(ad.get("floor")) if ad.get("floor") is not None else None
    condition = _normalize_condition(ad.get("condition") or ad.get("status"))
    property_type = _normalize_property_type(ad.get("propertyType") or ad.get("typeName"))

    # Location
    title   = ad.get("title") or ad.get("heading")
    address = ad.get("address") or ad.get("fullAddress")
    if not address:
        parts = [ad.get("street"), ad.get("number")]
        address = " ".join(p for p in parts if p) or None

    postal_code  = ad.get("postalCode") or ad.get("zipCode")
    neighborhood = ad.get("neighborhood") or ad.get("barrio")
    parish       = ad.get("parish") or ad.get("freguesia")
    city         = ad.get("city") or ad.get("municipality") or "Lisboa"
    district     = ad.get("province") or ad.get("region") or "Lisboa"

    # Coordinates
    lat = _coord(
        ad.get("latitude")
        or find_deep(ad, ["coordinates", "latitude"], ["ubication", "latitude"])
    )
    lon = _coord(
        ad.get("longitude")
        or find_deep(ad, ["coordinates", "longitude"], ["ubication", "longitude"])
    )

    # Images
    imgs_raw = ad.get("images") or ad.get("gallery") or ad.get("media") or []
    images = []
    if isinstance(imgs_raw, list):
        for i in imgs_raw:
            if isinstance(i, str):
                images.append(i)
            elif isinstance(i, dict):
                images.append(i.get("url") or i.get("src") or i.get("imageUrl"))
    images = [u for u in images if u and u.startswith("http")][:MAX_IMAGES]

    description = ad.get("description") or ad.get("detail")
    if description:
        description = str(description)[:1000]

    return {
        "price": price,
        "size": size,
        "rooms": rooms,
        "bedrooms": bedrooms,
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


# ── JSON-LD fallback ──────────────────────────────────────────────────────────

def extract_json_ld_listing(page: Page) -> dict:
    """Extract RealEstateListing JSON-LD as a fallback."""
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
                if t == "RealEstateListing" or (isinstance(t, list) and "RealEstateListing" in t):
                    return _parse_json_ld(item)
        except (json.JSONDecodeError, AttributeError):
            continue
    return {}


def _parse_json_ld(ld: dict) -> dict:
    about   = ld.get("about") or {}
    address = about.get("address") or {}
    geo     = about.get("geo") or {}
    specs   = (ld.get("offers") or {}).get("priceSpecification") or []

    # Find "prisantydning"-like price (asking price)
    price = None
    for p in specs:
        if "pric" in (p.get("name") or "").lower():
            price = _num(p.get("price"))
            break
    if price is None and specs:
        price = _num(specs[0].get("price"))

    size = _num((about.get("floorSize") or {}).get("value"))

    imgs = ld.get("image") or []
    images = [i if isinstance(i, str) else i.get("url") for i in imgs if i]
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
        "bedrooms": _int(about.get("numberOfBedrooms")),
        "floor": str(about.get("floorLevel")) if about.get("floorLevel") else None,
        "images": images,
    }


# ── DOM-based extraction (server-rendered HTML) ──────────────────────────────

def extract_from_dom(page: Page) -> dict:
    """Extract listing data directly from DOM elements and inline JS.

    Idealista moved from Next.js (__NEXT_DATA__) to server-rendered HTML.
    Data is in CSS-classed elements and inline JavaScript variables.
    """
    result = {}  # type: dict

    try:
        data = page.evaluate(r"""
            () => {
                const out = {};

                // Price: <strong class="price">650.000 €</strong>
                const priceEl = document.querySelector('strong.price, .info-data-price .txt-bold');
                if (priceEl) out.priceText = priceEl.textContent.trim();

                // Info features: "127 m² área bruta", "T2", "9º andar com elevador"
                const featureSpans = document.querySelectorAll('.info-features > span');
                out.features = Array.from(featureSpans).map(s => s.textContent.trim());

                // Title
                const titleEl = document.querySelector('.main-info__title-main, h1 .main-info__title-main');
                if (titleEl) out.title = titleEl.textContent.trim();

                // Subtitle (neighborhood, city): "Olivais, Lisboa"
                const subtitleEl = document.querySelector('.main-info__title-minor');
                if (subtitleEl) out.subtitle = subtitleEl.textContent.trim();

                // Location from header-map-list items
                const mapItems = document.querySelectorAll('.header-map-list');
                out.locationParts = Array.from(mapItems).map(li => li.textContent.trim());

                // Description
                const descEl = document.querySelector('.comment p, .adCommentsLanguage p, [class*="comment"] p');
                if (descEl) out.description = descEl.textContent.trim().slice(0, 1000);

                // Price per sqm
                const ppsEl = document.querySelector('.flex-feature-details, .squaredmeterprice .flex-feature-details');
                if (ppsEl) out.pricePerSqm = ppsEl.textContent.trim();

                // Condition from detail features
                const details = document.querySelectorAll('.details-property-feature-one li, .details-property li');
                out.detailFeatures = Array.from(details).map(li => li.textContent.trim());

                // Try to get coordinates from Google Maps static URL
                const staticMap = document.querySelector('.static-map a, img[src*="maps.googleapis"]');
                if (staticMap) {
                    const src = staticMap.getAttribute('href') || staticMap.getAttribute('src') || '';
                    out.staticMapUrl = src;
                }

                // Get inline JS data: multimediaCarrousel, idForm, etc.
                const scripts = document.querySelectorAll('script:not([src])');
                for (const s of scripts) {
                    const t = s.textContent || '';

                    // buyingPrice
                    const priceMatch = t.match(/buyingPrice\s*:\s*([\d.]+)/);
                    if (priceMatch) out.buyingPrice = priceMatch[1];

                    // coordinates from Google Maps URL in multimediaCarrousel
                    const coordMatch = t.match(/center=([\d.-]+)%2C([\d.-]+)/);
                    if (coordMatch) {
                        out.lat = coordMatch[1];
                        out.lon = coordMatch[2];
                    }

                    // multimediaCarrousel images
                    if (t.includes('multimediaCarrousel')) {
                        const mcMatch = t.match(/multimediaCarrousel\s*:\s*(\{.+?\})\s*,\s*\n?\s*dynamicMapKey/s);
                        if (mcMatch) out.multimediaJSON = mcMatch[1];
                    }
                }

                return out;
            }
        """)
    except Exception as e:
        log.warning(f"  DOM extraction JS failed: {e}")
        return result

    # Parse price
    if data.get("buyingPrice"):
        result["price"] = _num(data["buyingPrice"])
    elif data.get("priceText"):
        result["price"] = _num(data["priceText"])

    # Parse features: size, typology, floor
    for feat in (data.get("features") or []):
        if "m²" in feat or "m2" in feat.lower():
            m = re.search(r"([\d.,]+)\s*m", feat)
            if m:
                result["size"] = _num(m.group(1))
        elif re.match(r"T\d", feat.strip()):
            rooms = _int(feat)
            result["rooms"] = rooms
            result["bedrooms"] = rooms
        elif "andar" in feat.lower() or "res" in feat.lower().replace("é", "e"):
            m = re.search(r"(\d+)", feat)
            if m:
                result["floor"] = m.group(1)
            elif "rés" in feat.lower() or "r/c" in feat.lower():
                result["floor"] = "0"

    result["title"] = data.get("title")
    result["description"] = data.get("description")

    # Location
    loc_parts = data.get("locationParts") or []
    if len(loc_parts) >= 1:
        result["address"] = loc_parts[0]
    if len(loc_parts) >= 2:
        result["neighborhood"] = loc_parts[1]
    if len(loc_parts) >= 3:
        city_parts = loc_parts[2].split(",")
        result["city"] = city_parts[0].strip()
        if len(city_parts) > 1:
            result["district"] = city_parts[-1].strip()

    # Subtitle fallback for neighborhood
    if not result.get("neighborhood") and data.get("subtitle"):
        parts = data["subtitle"].split(",")
        if parts:
            result["neighborhood"] = parts[0].strip()

    # Coordinates
    if data.get("lat"):
        result["lat"] = _coord(data["lat"])
    if data.get("lon"):
        result["lon"] = _coord(data["lon"])

    # Images from multimediaCarrousel
    if data.get("multimediaJSON"):
        try:
            mc = json.loads(data["multimediaJSON"])
            images = []
            for media_group in mc.get("multimedias") or []:
                if media_group.get("type") == "PICTURE":
                    for img in media_group.get("content") or []:
                        src = img.get("src") or img.get("srcWebp")
                        if src and src.startswith("http"):
                            images.append(src)
            result["images"] = images[:MAX_IMAGES]
        except (json.JSONDecodeError, TypeError):
            pass

    # Condition from detail features
    for feat in (data.get("detailFeatures") or []):
        fl = feat.lower()
        if any(w in fl for w in ["renovado", "remodelado", "novo", "bom estado", "usado", "para recuperar", "em construção"]):
            result["condition"] = _normalize_condition(feat)
            break

    # Property type from title
    title = (result.get("title") or "").lower()
    for pt in ["apartamento", "moradia", "vivenda", "loja", "terreno", "escritório", "garagem", "armazém", "prédio"]:
        if pt in title:
            result["property_type"] = _normalize_property_type(pt)
            break

    return result


# ── Detail page scraper ───────────────────────────────────────────────────────

def scrape_detail_page(page: Page, url: str, listing_type: str = 'sale') -> Optional[Listing]:
    """Visit a listing detail page and return a Listing object."""

    # Extract source_id from URL: /imovel/12345678/
    id_match = re.search(r"/imovel/(\d+)", url)
    source_id = id_match.group(1) if id_match else hashlib.sha1(url.encode()).hexdigest()[:16]

    # Pull __NEXT_DATA__
    next_data = extract_next_data(page)
    page_props = find_deep(
        next_data,
        ["props", "pageProps"],
        ["pageProps"],
    ) or {}

    ad = find_deep(
        page_props,
        ["adDetail"],
        ["ad"],
        ["listing"],
        ["estate"],
    ) or {}

    nd = extract_listing_from_next_data(ad)

    # JSON-LD fallback for any missing fields
    ld = extract_json_ld_listing(page) if not nd.get("price") else {}

    # DOM fallback — Idealista moved from Next.js to server-rendered HTML
    dom = extract_from_dom(page) if not (nd.get("price") or ld.get("price")) else {}

    price    = nd.get("price")    or ld.get("price")    or dom.get("price")
    size     = nd.get("size")     or ld.get("size")     or dom.get("size")
    lat      = nd.get("lat")      or ld.get("lat")      or dom.get("lat")
    lon      = nd.get("lon")      or ld.get("lon")      or dom.get("lon")
    address  = nd.get("address")  or ld.get("address")  or dom.get("address")
    postal   = nd.get("postal_code") or ld.get("postal_code")
    city     = nd.get("city")     or ld.get("city")     or dom.get("city") or "Lisboa"
    district = nd.get("district") or ld.get("district") or dom.get("district") or "Lisboa"
    bedrooms = nd.get("bedrooms") or ld.get("bedrooms") or dom.get("bedrooms")
    floor    = nd.get("floor")    or ld.get("floor")    or dom.get("floor")
    images   = nd.get("images") or ld.get("images") or dom.get("images") or []

    price_per_sqm = round(price / size, 2) if price and size and size > 0 else None

    # ── Detect sold / reserved status ────────────────────────────────────────
    status = 'active'
    raw_state = (
        ad.get("state") or ad.get("status") or
        page_props.get("status") or
        find_deep(page_props, ["adDetail", "state"], ["ad", "state"]) or ""
    )
    if isinstance(raw_state, str) and raw_state:
        s = raw_state.lower()
        if any(w in s for w in ["vend", "sold", "closed", "inactive"]):
            status = 'sold'
        elif "reserv" in s:
            status = 'reserved'

    # DOM fallback — look for sold/reserved badge elements
    if status == 'active':
        try:
            badge = page.evaluate("""
                () => {
                    const sold = document.querySelector(
                        '[class*="vendido"], [class*="sold-tag"], .estado-vendido, ' +
                        '[data-testid*="sold"], [class*="property-status--sold"]'
                    );
                    if (sold) return 'sold';
                    const reserved = document.querySelector(
                        '[class*="reservado"], [class*="reserved"]'
                    );
                    if (reserved) return 'reserved';
                    return null;
                }
            """)
            if badge:
                status = badge
        except Exception:
            pass

    return Listing(
        source="idealista",
        source_id=source_id,
        url=url,
        listing_type=listing_type,
        status=status,
        price_amount=price,
        price_per_sqm=price_per_sqm,
        size_sqm=size,
        rooms=nd.get("rooms") or dom.get("rooms"),
        bedrooms=bedrooms,
        floor=floor,
        property_type=nd.get("property_type") or ld.get("property_type") or dom.get("property_type"),
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


# ── Search results: collect detail URLs ──────────────────────────────────────

def extract_detail_urls(page: Page) -> list[str]:
    """Extract listing detail URLs from a search results page."""

    # Try JSON-LD ItemList first
    try:
        scripts = page.evaluate("""
            () => Array.from(
                document.querySelectorAll('script[type="application/ld+json"]')
            ).map(s => s.textContent)
        """)
        urls = []
        for raw in scripts:
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for obj in items:
                if obj.get("@type") == "ItemList":
                    for el in obj.get("itemListElement") or []:
                        u = el.get("item", {}).get("url") or el.get("url")
                        if u:
                            urls.append(u)
        if urls:
            return list(dict.fromkeys(urls))  # deduplicate, preserve order
    except Exception:
        pass

    # Fallback: <a href="/imovel/..."> links
    try:
        hrefs = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href*="/imovel/"]'))
                .map(a => a.href)
                .filter(h => /\\/imovel\\/\\d+/.test(h))
        """)
        return list(dict.fromkeys(hrefs))
    except Exception:
        return []


# ── Pagination ────────────────────────────────────────────────────────────────

def build_page_url(base_url: str, page_num: int) -> str:
    if page_num == 1:
        return base_url
    base = base_url.rstrip("/")
    # Remove existing pagina-N.htm if present
    base = re.sub(r"/pagina-\d+\.htm$", "", base)
    return f"{base}/pagina-{page_num}.htm"


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
    return "used"


# ── Main scraper ──────────────────────────────────────────────────────────────

def load_cookies(path: str) -> list:
    """
    Load cookies from a JSON file exported by a browser extension
    (e.g. Cookie-Editor, EditThisCookie).
    Returns a list of Playwright-compatible cookie dicts.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    cookies = []
    for c in raw:
        cookie = {
            "name":   c["name"],
            "value":  c["value"],
            "domain": c.get("domain", ".idealista.pt"),
            "path":   c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
        }
        # expirationDate (Chrome) or expiry (Firefox) → expires
        exp = c.get("expirationDate") or c.get("expiry")
        if exp:
            cookie["expires"] = int(exp)
        same_site = c.get("sameSite", "Lax")
        if same_site not in ("Strict", "Lax", "None"):
            same_site = "Lax"
        cookie["sameSite"] = same_site
        cookies.append(cookie)

    return cookies


PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile"


def _build_search_urls(search_url: str, listing_type: str) -> list:
    """Build list of search URLs.

    If search_url targets Lisboa city (the default), split into
    per-neighborhood URLs so each result set fits on page 1 and we
    never need pagination (avoids DataDome blocking page 2+).
    """
    buy_or_rent = "arrendar-casas" if listing_type == "rent" else "comprar-casas"

    # Check if this is a top-level Lisboa search that should be split
    if re.search(r"/(?:comprar|arrendar)-casas/lisboa/?$", search_url.rstrip("/")):
        urls = []
        for slug in LISBON_NEIGHBORHOODS:
            urls.append(f"{BASE_URL}/{buy_or_rent}/lisboa/{slug}/")
        log.info(f"Split Lisboa search into {len(urls)} neighborhood URLs")
        return urls

    # For other searches (specific neighborhood, AML, Porto, etc.) use as-is
    return [search_url]


def run_scraper(
    search_url: str = DEFAULT_SEARCH,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_items: int = DEFAULT_MAX_ITEMS,
    headless: bool = True,
    cookies_file: Optional[str] = None,
    listing_type: str = 'sale',
):
    if not _PLAYWRIGHT_AVAILABLE:
        print("Playwright is not installed.")
        print("Run: pip install playwright && playwright install chromium")
        sys.exit(1)
    db.init_db()
    run_id = db.start_scrape_run("idealista")

    new_count = updated_count = error_count = 0
    skipped_known = 0
    total_pushed = 0

    # Load already-scraped source_ids so we can skip them
    known_ids = _load_known_source_ids(listing_type)
    log.info(f"Loaded {len(known_ids)} known source_ids — will skip these")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Build search URL list (may split into neighborhoods)
    search_urls = _build_search_urls(search_url, listing_type)

    with sync_playwright() as pw:
        # Persistent context saves cookies/session across runs so the
        # DataDome challenge only needs to be solved once (manually).
        context: BrowserContext = pw.chromium.launch_persistent_context(
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

        if cookies_file:
            cookies = load_cookies(cookies_file)
            context.add_cookies(cookies)
            log.info(f"Loaded {len(cookies)} cookies from {cookies_file}")

        # Warm up: visit homepage to pick up cookies
        log.info("Warming up session on idealista.pt…")
        page.goto(BASE_URL, timeout=30000)
        accept_cookies(page)
        _polite_delay()

        # ── Collect detail URLs from all search pages ─────────────────────────
        all_detail_urls: list[str] = []
        datadome_blocked = 0

        for search_idx, s_url in enumerate(search_urls):
            for page_num in range(1, max_pages + 1):
                page_url = build_page_url(s_url, page_num)
                log.info(f"Search [{search_idx+1}/{len(search_urls)}] page {page_num}: {page_url}")

                if not _goto_with_retry(page, page_url):
                    datadome_blocked += 1
                    if datadome_blocked >= 3:
                        log.warning("DataDome blocked 3 search pages — session may be expired. Run --setup.")
                    break

                detail_urls = extract_detail_urls(page)
                log.info(f"  Found {len(detail_urls)} listings on page {page_num}")

                if not detail_urls:
                    break  # end of results for this search URL

                all_detail_urls.extend(u for u in detail_urls if u not in all_detail_urls)

                if len(all_detail_urls) >= max_items * 2:
                    log.info(f"Collected enough URLs ({len(all_detail_urls)}). Moving to detail scraping.")
                    break

                _polite_delay()

        log.info(f"Total detail URLs collected: {len(all_detail_urls)}")

        # ── Filter out already-known listings ─────────────────────────────────
        new_urls = []
        for url in all_detail_urls:
            id_match = re.search(r"/imovel/(\d+)", url)
            source_id = id_match.group(1) if id_match else None
            if source_id and source_id in known_ids:
                skipped_known += 1
            else:
                new_urls.append(url)

        log.info(f"Skipping {skipped_known} already-known listings, {len(new_urls)} new to scrape")

        # ── Visit each detail page ────────────────────────────────────────────
        for i, detail_url in enumerate(new_urls):
            if total_pushed >= max_items:
                log.info(f"Reached max_items={max_items}. Stopping.")
                break

            log.info(f"[{i+1}/{len(new_urls)}] {detail_url}")

            try:
                if not _goto_with_retry(page, detail_url):
                    log.warning(f"  DataDome blocked detail page — skipping")
                    error_count += 1
                    continue

                # Wait for __NEXT_DATA__ or JSON-LD to be available
                try:
                    page.wait_for_selector(
                        "#__NEXT_DATA__, script[type='application/ld+json']",
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
                        f"  ✓ {listing.neighborhood or 'unknown'} "
                        f"T{listing.rooms} "
                        f"€{listing.price_amount:,.0f} "
                        f"({listing.size_sqm}m²)"
                    )

            except PWTimeout:
                log.warning(f"  Timeout on detail page — skipping")
                error_count += 1
            except Exception as e:
                log.warning(f"  Error: {e}")
                error_count += 1

            _polite_delay()

        context.close()

    # ── Rebuild stats and close run ───────────────────────────────────────────
    db.rebuild_neighborhoods()

    run = ScrapeRun(
        source="idealista",
        listings_found=total_pushed,
        listings_new=new_count,
        listings_updated=updated_count,
        errors=error_count,
        status="completed",
    )
    db.finish_scrape_run(run_id, run)

    print(f"\n✓ Scrape complete")
    print(f"  Listings scraped : {total_pushed}")
    print(f"  New              : {new_count}")
    print(f"  Updated          : {updated_count}")
    print(f"  Skipped (known)  : {skipped_known}")
    print(f"  Errors           : {error_count}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Idealista.pt Lisbon scraper")
    parser.add_argument("--url", default=None,
                        help="Search URL to start from (overrides --type)")
    parser.add_argument("--type", choices=["sale", "rent"], default="sale",
                        help="Listing type to scrape: 'sale' (default) or 'rent'")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Max search result pages to paginate (default: {DEFAULT_MAX_PAGES})")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS,
                        help=f"Max listings to scrape (default: {DEFAULT_MAX_ITEMS})")
    parser.add_argument("--no-headless", action="store_true",
                        help="Show the browser window (useful for debugging)")
    parser.add_argument("--setup", action="store_true",
                        help="Open browser for manual challenge solving, then exit (run once before scraping)")
    args = parser.parse_args()

    if args.setup:
        # Open the browser visibly so the user can solve the DataDome challenge.
        # The session is saved to data/browser_profile/ and reused on future runs.
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
            print("\n" + "="*60)
            print("Browser is open. Solve the challenge on idealista.pt,")
            print("verify listings are visible, then press ENTER here.")
            print("="*60 + "\n")
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
