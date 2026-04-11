"""
remax_playwright.py — Standalone remax.pt scraper using Python Playwright.

Uses RE/MAX's internal JSON API (/api/Listing/PaginatedMultiMatchSearch) which
returns complete listing data per page without needing to visit detail pages.
A browser context is used only to establish session cookies; the API requests
themselves go through page.request.post.

Setup (one-time):
    pip install playwright
    playwright install chromium

Run:
    python scrapers/remax_playwright.py --setup          # One-time: solve any challenge
    python scrapers/remax_playwright.py                  # Headless scrape (after --setup)
    python scrapers/remax_playwright.py --max-pages 5    # Quick test run
    python scrapers/remax_playwright.py --type rent      # Scrape rentals
"""

import re
import sys
import json
import time
import random
import hashlib
import logging
import argparse
import urllib.parse
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
SEARCH_API = "https://www.remax.pt/api/Listing/PaginatedMultiMatchSearch"
IMAGE_BASE = "https://i.maxwork.pt/l-feat/"

DEFAULT_SEARCH_VALUE = "Lisboa"
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_PAGES = 500     # 500 * 50 = 25000 items ceiling; API reports ~8.4k
DEFAULT_MAX_ITEMS = 25000
MAX_IMAGES = 15

# Delay between API page fetches (seconds)
MIN_DELAY = 1.0
MAX_DELAY = 2.5

PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile_remax"

# -- Listing exclusion filters -------------------------------------------------
# Exclude non-property listings: timeshares, garages, storage, parking, etc.
# The API filter `listingClassID=1` already limits to residential, but we keep
# these as belt-and-suspenders for edge cases.

# descriptionTags slugs that indicate non-property listings
EXCLUDED_URL_SLUGS = [
    "outros---habitac",   # Timeshares, hotel weeks
    "garagem",            # Garages / parking boxes
    "armazem", "armazém", # Warehouses / storage
    "escritorio", "escritório",  # Offices
    "loja",               # Shops / commercial
]

# Keywords in title or description that indicate non-property listings
EXCLUDED_KEYWORDS = [
    "time sharing", "timesharing", "time-sharing", "timeshare",
    "semana ", "semana)",  # "semana 14" = week 14 (timeshare week)
    "direito de habitação periódica",
    "habitação periódica",
    "multipropriedade",
]


def _is_excluded_tags(tags: str) -> bool:
    if not tags:
        return False
    t = tags.lower()
    return any(slug in t for slug in EXCLUDED_URL_SLUGS)


def _is_excluded_listing(listing: "Listing") -> bool:
    text = " ".join(filter(None, [
        (listing.title or "").lower(),
        (listing.description or "").lower(),
    ]))
    return any(kw in text for kw in EXCLUDED_KEYWORDS)


# -- Utilities -----------------------------------------------------------------

def _num(v) -> Optional[float]:
    """Parse a value as a float. Handles Portuguese thousands/decimal
    separators and native numeric types."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v != 0 else None
    try:
        s = str(v).strip()
        s = re.sub(r"[^\d.,\-]", "", s)
        if not s:
            return None
        if re.match(r'^\d{1,3}(\.\d{3})+([,]\d+)?$', s):
            s = s.replace(".", "").replace(",", ".")
        else:
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
    if isinstance(v, int):
        return v
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


def _extract_source_id(url: str) -> str:
    """Extract the RE/MAX listing code from URL.
    URLs look like /pt/imoveis/{slug}/{code} where code is e.g. 122911464-25.
    The full code (including the -N suffix) is the unique identifier — the
    numeric prefix alone is an office/group number and collides across
    listings from the same office."""
    # Full code with required -N suffix (this is RE/MAX's canonical format)
    m = re.search(r'/(\d{6,10}-\d+)(?:\?|$|/)', url)
    if m:
        return m.group(1)
    # Bare numeric ID as a fallback
    m = re.search(r'/(\d{6,10})(?:\?|$|/)', url)
    if m:
        return m.group(1)
    # Last-resort alphanumeric tail
    m = re.search(r'/([a-zA-Z0-9\-]{8,})(?:\?|$)', url)
    if m:
        return m.group(1)
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _load_known_listings(listing_type: str = 'sale') -> dict:
    """Load source_id -> price_amount for all remax listings in the DB."""
    table = "rentals" if listing_type == "rent" else "sales"
    try:
        conn = db.get_connection()
        rows = conn.execute(
            f"SELECT source_id, price_amount FROM {table} WHERE source='remax'"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


# -- Normalisation helpers -----------------------------------------------------

def _normalize_property_type(raw) -> str:
    if not raw:
        return "apartment"
    s = str(raw).lower()
    if any(w in s for w in ["moradia", "vivenda", "house", "villa"]):  return "house"
    if "terreno" in s or "lote" in s:                                   return "land"
    if any(w in s for w in ["est\u00fadio", "studio", "t0"]):          return "studio"
    if "loft" in s:                                                     return "loft"
    if "duplex" in s:                                                   return "duplex"
    if any(w in s for w in ["penthouse", "cobertura"]):                 return "penthouse"
    return "apartment"


# Best-effort mapping for RE/MAX conservationStatusID. Numeric IDs aren't
# publicly documented; values are derived from observed listings.
_CONSERVATION_STATUS_MAP = {
    1: "new",
    2: "used",
    3: "renovated",
    4: "to_renovate",
    5: "new",   # "Em construção"
}


def _map_condition(status_id) -> Optional[str]:
    try:
        return _CONSERVATION_STATUS_MAP.get(int(status_id))
    except (ValueError, TypeError):
        return None


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
        except Exception:
            continue


# -- API client ----------------------------------------------------------------

def _build_filters(listing_type: str) -> list:
    """Build the `filters` array for the PaginatedMultiMatchSearch API.
    Mirrors what the RE/MAX website sends when filtering to residential
    buy/rent listings."""
    bt = 1 if listing_type == 'sale' else 2
    return [
        {
            "field": "businessTypeID",
            "operationType": "int",
            "operator": "=",
            "value": str(bt),
            "label": "buy" if bt == 1 else "rent",
        },
        # Residential only (excludes commercial, garages, warehouses, etc.)
        {
            "field": "listingClassID",
            "operationType": "int",
            "operator": "=",
            "value": "1",
        },
        {
            "field": "isSpecialExclusive",
            "operator": "=",
            "operationType": "string",
            "value": "false",
        },
    ]


def fetch_search_page(
    page: Page,
    listing_type: str,
    search_value: str,
    page_number: int,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Optional[dict]:
    """POST to /api/Listing/PaginatedMultiMatchSearch and return parsed JSON.
    Uses the browser context's cookies so RE/MAX treats this as a real user."""
    body = {
        "filters": _build_filters(listing_type),
        "pageNumber": page_number,
        "pageSize": page_size,
        "sort": ["-PublishDate"],
        "searchValue": search_value,
    }
    referer = f"{BASE_URL}/pt/{'comprar' if listing_type == 'sale' else 'arrendar'}"
    try:
        resp = page.request.post(
            SEARCH_API,
            data=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "pt-PT,pt;q=0.9",
                "Origin": BASE_URL,
                "Referer": referer,
            },
            timeout=30000,
        )
    except Exception as e:
        log.warning(f"API request failed: {e}")
        return None

    if resp.status != 200:
        log.warning(f"API returned HTTP {resp.status}")
        return None
    try:
        return resp.json()
    except Exception as e:
        log.warning(f"Failed to parse API JSON: {e}")
        return None


# -- Listing builder -----------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _pt_description(descriptions) -> Optional[str]:
    """Pick the Portuguese description from the multi-language list and
    strip HTML tags."""
    if not isinstance(descriptions, list):
        return None
    for d in descriptions:
        if not isinstance(d, dict):
            continue
        code = (d.get("languageCode") or "").upper()
        if code == "PT":
            raw = d.get("description")
            if raw:
                text = _HTML_TAG_RE.sub(" ", str(raw))
                text = _WS_RE.sub(" ", text).strip()
                return text[:1000] if text else None
    return None


def _image_url(path_or_url: str) -> Optional[str]:
    if not path_or_url or not isinstance(path_or_url, str):
        return None
    if path_or_url.startswith("http"):
        return path_or_url
    return IMAGE_BASE + path_or_url.lstrip("/")


def _collect_images(result: dict) -> list:
    images = []
    raw = result.get("listingPictures") or []
    if isinstance(raw, list):
        for img in raw:
            if isinstance(img, str):
                u = _image_url(img)
                if u:
                    images.append(u)
            elif isinstance(img, dict):
                src = (
                    img.get("url") or img.get("path") or img.get("src") or
                    img.get("large") or img.get("medium") or img.get("original")
                )
                if src:
                    u = _image_url(src)
                    if u:
                        images.append(u)
    # Fall back to the main listing picture if the gallery is empty
    if not images and result.get("listingPictureUrl"):
        u = _image_url(result["listingPictureUrl"])
        if u:
            images.append(u)
    # De-duplicate while preserving order
    seen = set()
    out = []
    for u in images:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:MAX_IMAGES]


# RE/MAX API boolean fields → Portuguese chip labels that match the
# _PROPERTY_FEATURES regex patterns in database.py.
# Only truthy booleans become positive chips; explicit `False` becomes a
# negated chip so `parking: false` can override text matches.
_BOOL_CHIP_LABELS = {
    "parking":              ("Estacionamento", "Sem estacionamento"),
    "garage":               ("Garagem", "Sem garagem"),
    "elevator":              ("Elevador", "Sem elevador"),
    "electricCarsCharging": ("Carregamento de carros elétricos", None),
}


def _collect_feature_chips(result: dict) -> list:
    """Build a list of structured feature-chip strings from an API result.
    Uses the boolean amenity fields RE/MAX exposes (parking, garage,
    elevator, electricCarsCharging). Returns an empty list if none are set
    — the description-based scoring in database.py handles everything
    else via text matching on the Listing.description field."""
    chips: list = []
    for key, (positive, negative) in _BOOL_CHIP_LABELS.items():
        v = result.get(key)
        if v is True:
            chips.append(positive)
        elif v is False and negative:
            chips.append(negative)

    # Garage spots count — only add when the `garage` boolean is also set,
    # otherwise the count refers to outdoor/shared parking spots (which we
    # already captured above via `parking`)
    if result.get("garage") is True:
        garage_spots = result.get("garageSpots")
        if isinstance(garage_spots, (int, float)) and garage_spots > 0:
            chips.append(f"{int(garage_spots)} lugares de garagem")

    # De-duplicate while preserving order
    seen = set()
    out = []
    for c in chips:
        k = c.lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def build_listing_url(tags: str, title_code: str) -> str:
    return f"{BASE_URL}/pt/imoveis/{tags}/{title_code}"


def build_listing_from_api(result: dict, listing_type: str) -> Optional[Listing]:
    """Build a Listing directly from a PaginatedMultiMatchSearch result item.
    Returns None if the item lacks the bare essentials (URL or price)."""
    tags = result.get("descriptionTags")
    title_code = result.get("listingTitle")
    if not tags or not title_code:
        return None

    url = build_listing_url(tags, title_code)
    source_id = _extract_source_id(url) or str(title_code)

    price = _num(result.get("listingPrice"))
    if not price:
        return None

    listing_type_raw = str(result.get("listingType") or "").lower()
    is_land = "terreno" in listing_type_raw or "lote" in listing_type_raw

    living = _num(result.get("livingArea"))
    total = _num(result.get("totalArea"))
    built = _num(result.get("builtArea"))

    if is_land:
        size = _num(result.get("lotSize")) or total or living
    else:
        size = living or total

    # Gross area: prefer builtArea; fall back to totalArea if larger than size
    gross_area = built
    if not gross_area and total and size and total > size:
        gross_area = total
    if gross_area and size and gross_area == size:
        gross_area = None

    bedrooms = _int(result.get("numberOfBedrooms"))
    bathrooms = _int(result.get("numberOfBathrooms"))
    # In the Portuguese typology system a "T2" = 2 bedrooms, so rooms==bedrooms
    rooms = bedrooms if bedrooms is not None else _int(result.get("totalRooms"))

    floor = None
    floor_num = result.get("floorAsNumber")
    if floor_num is not None:
        floor = str(floor_num)
    elif result.get("floorDescription"):
        floor = str(result["floorDescription"])

    lat = _coord(result.get("latitude"))
    lon = _coord(result.get("longitude"))
    if (lat is None or lon is None) and isinstance(result.get("coordinates"), dict):
        lat = lat if lat is not None else _coord(result["coordinates"].get("latitude"))
        lon = lon if lon is not None else _coord(result["coordinates"].get("longitude"))

    address = result.get("address")
    if address:
        address = str(address).strip().rstrip(",").strip() or None
    postal = result.get("zipCode")

    # Location hierarchy as reported by the API:
    #   regionName1 = district (e.g. "Lisboa")
    #   regionName2 = municipality / city (e.g. "Loures", "Sintra")
    #   regionName3 = parish / freguesia (e.g. "Queluz e Belas")
    #   localZone   = neighborhood / zone (e.g. "Benfica", "São João da Talha")
    district = result.get("regionName1") or "Lisboa"
    city = result.get("regionName2") or district
    parish = result.get("regionName3")
    neighborhood = result.get("localZone") or parish

    property_type = _normalize_property_type(result.get("listingType"))
    condition = _map_condition(result.get("conservationStatusID"))

    description = _pt_description(result.get("descriptions"))

    # Title: API doesn't expose a clean human title, so synthesise one
    title = result.get("newListingTitle") or result.get("previousListingTitle")
    if not title:
        type_name = result.get("listingType") or "Imóvel"
        typology = f"T{bedrooms}" if bedrooms is not None else ""
        locale_part = neighborhood or city or district or "Lisboa"
        title = " ".join(filter(None, [type_name, typology, "em", locale_part]))

    images = _collect_images(result)
    feature_chips = _collect_feature_chips(result)

    price_per_sqm = round(price / size, 2) if price and size and size > 0 else None

    status = "sold" if result.get("isSold") else "active"

    return Listing(
        source="remax",
        source_id=source_id,
        url=url,
        listing_type=listing_type,
        status=status,
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
        parish=parish,
        district=district,
        city=city,
        lat=lat,
        lon=lon,
        images=json.dumps(images) if images else None,
        hash_dedupe=_hash(address, city, price, size),
        description=description,
        feature_chips=json.dumps(feature_chips) if feature_chips else None,
        scraped_at=datetime.utcnow(),
    )


# -- Main scraper --------------------------------------------------------------

def run_scraper(
    search_value: str = DEFAULT_SEARCH_VALUE,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_items: int = DEFAULT_MAX_ITEMS,
    page_size: int = DEFAULT_PAGE_SIZE,
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
    skipped_known = skipped_excluded = 0
    total_pushed = 0
    seen_source_ids = set()

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

        # Warm up: visit the search page once so the API call inherits
        # cookies / tokens / bot-detection fingerprints from a real page load
        warmup_state = {
            "regionName": search_value,
            "businessType": 1 if listing_type == 'sale' else 2,
            "mediaTypes": [1],
        }
        warmup_path = "comprar" if listing_type == 'sale' else "arrendar"
        warmup_url = (
            f"{BASE_URL}/{warmup_path}?searchQueryState="
            f"{urllib.parse.quote(json.dumps(warmup_state, separators=(',', ':')))}"
        )
        log.info(f"Warming up session: {warmup_url}")
        try:
            page.goto(warmup_url, timeout=30000, wait_until="domcontentloaded")
            accept_cookies(page)
            page.wait_for_timeout(2500)
        except PWTimeout:
            log.warning("Warm-up timeout, continuing anyway")

        # -- Paginate through the API -----------------------------------------
        page_number = 1
        total_pages_reported = None
        empty_page_count = 0

        while page_number <= max_pages and total_pushed < max_items:
            log.info(f"API page {page_number}/{max_pages} (pageSize={page_size})")
            data = fetch_search_page(page, listing_type, search_value, page_number, page_size)
            if data is None:
                log.warning("No data from API, stopping")
                break

            if total_pages_reported is None:
                total_pages_reported = data.get("totalPages")
                total_count = data.get("total")
                log.info(
                    f"  API reports total={total_count}, totalPages={total_pages_reported}"
                )

            results = data.get("results") or []
            if not results:
                empty_page_count += 1
                if empty_page_count >= 2:
                    log.info("  Two empty pages in a row, stopping")
                    break
                page_number += 1
                _polite_delay()
                continue
            empty_page_count = 0

            page_new = page_updated = 0
            for result in results:
                if total_pushed >= max_items:
                    break

                try:
                    if _is_excluded_tags(result.get("descriptionTags") or ""):
                        skipped_excluded += 1
                        continue

                    listing = build_listing_from_api(result, listing_type)
                    if listing is None:
                        error_count += 1
                        continue

                    if _is_excluded_listing(listing):
                        skipped_excluded += 1
                        continue

                    sid = listing.source_id
                    seen_source_ids.add(sid)

                    # Skip unchanged known listings
                    if sid in known_listings:
                        old_price = known_listings[sid]
                        new_price = listing.price_amount
                        if (old_price and new_price and
                                abs(new_price - old_price) <= 1):
                            skipped_known += 1
                            continue
                        if old_price and new_price:
                            log.info(
                                f"  Price changed for {sid}: "
                                f"{old_price:,.0f} -> {new_price:,.0f}"
                            )

                    _, is_new = db.upsert_listing(listing)
                    if is_new:
                        new_count += 1
                        page_new += 1
                    else:
                        updated_count += 1
                        page_updated += 1
                    total_pushed += 1
                except Exception as e:
                    log.warning(f"  Error processing result: {e}")
                    error_count += 1

            log.info(
                f"  Page {page_number}: +{page_new} new, {page_updated} updated "
                f"(total so far: {total_pushed})"
            )

            if total_pages_reported and page_number >= total_pages_reported:
                log.info(f"Reached last API page ({total_pages_reported})")
                break
            if data.get("hasNextPage") is False:
                log.info("API reports no next page")
                break

            page_number += 1
            _polite_delay()

        context.close()

    # -- Missing detection -----------------------------------------------------
    if len(known_listings) > 0 and len(seen_source_ids) < len(known_listings) * 0.5:
        log.warning(
            f"Only saw {len(seen_source_ids)}/{len(known_listings)} listings — "
            f"skipping missing detection (possible block)"
        )
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
    print(f"  Skipped (excl)   : {skipped_excluded}")
    print(f"  Errors           : {error_count}")


# -- CLI -----------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="remax.pt scraper")
    parser.add_argument("--search", default=DEFAULT_SEARCH_VALUE,
                        help=f"Search value (region name) to scrape (default: {DEFAULT_SEARCH_VALUE})")
    parser.add_argument("--type", choices=["sale", "rent"], default="sale",
                        help="Listing type to scrape: 'sale' (default) or 'rent'")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Max API result pages (default: {DEFAULT_MAX_PAGES})")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS,
                        help=f"Max listings to scrape (default: {DEFAULT_MAX_ITEMS})")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE,
                        help=f"API pageSize (default: {DEFAULT_PAGE_SIZE})")
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
            warmup_state = {"regionName": DEFAULT_SEARCH_VALUE, "businessType": 1, "mediaTypes": [1]}
            warmup_url = (
                f"{BASE_URL}/comprar?searchQueryState="
                f"{urllib.parse.quote(json.dumps(warmup_state, separators=(',', ':')))}"
            )
            page.goto(warmup_url, timeout=60000)
            print("\n" + "=" * 60)
            print("Browser is open. Solve any challenge on remax.pt,")
            print("verify listings are visible, then press ENTER here.")
            print("=" * 60 + "\n")
            input()
            context.close()
            print("Session saved. You can now run the scraper normally.")
    else:
        run_scraper(
            search_value=args.search,
            max_pages=args.max_pages,
            max_items=args.max_items,
            page_size=args.page_size,
            headless=not args.no_headless,
            listing_type=args.type,
        )
