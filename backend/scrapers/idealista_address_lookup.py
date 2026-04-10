"""
idealista_address_lookup.py — Search Idealista for a specific address and scrape matching listings.

Reuses the persistent Chrome profile and extraction pipeline from idealista_playwright.py.
"""

import math
import logging
import time
import random
import sys
from pathlib import Path
from typing import Optional, List
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Listing

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False
    PWTimeout = Exception

try:
    from playwright_stealth import Stealth
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

from scrapers.idealista_playwright import (
    PROFILE_DIR,
    BASE_URL,
    accept_cookies,
    _goto_with_retry,
    _is_datadome,
    _polite_delay,
    scrape_detail_page,
    extract_detail_urls,
)

log = logging.getLogger(__name__)

MAX_DETAIL_PAGES = 5


def _haversine(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(a))


def search_idealista_by_address(
    address,          # type: str
    lat,              # type: float
    lon,              # type: float
    listing_type="sale",  # type: str
    headless=True,    # type: bool
    max_results=5,    # type: int
    proximity_m=300,  # type: int
):
    # type: (...) -> List[Listing]
    """Search Idealista for listings near an address and scrape matching detail pages.

    Returns a list of Listing objects within proximity_m of the target coordinates.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright is not installed")

    buy_or_rent = "arrendar-casas" if listing_type == "rent" else "comprar-casas"
    search_url = "{}/{}/lisboa-distrito/?q={}".format(
        BASE_URL, buy_or_rent, quote_plus(address)
    )

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    results = []

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

        # Warm up session
        log.info("Address lookup: warming up session...")
        page.goto(BASE_URL, timeout=30000)
        accept_cookies(page)
        time.sleep(random.uniform(1.5, 3.0))

        # Navigate to search results
        log.info("Address lookup: searching for '%s'", address)
        if not _goto_with_retry(page, search_url):
            log.warning("Address lookup: DataDome blocked search page")
            context.close()
            return []

        # Check if we landed on a detail page directly (Idealista sometimes redirects)
        current_url = page.url
        if "/imovel/" in current_url:
            log.info("Address lookup: redirected to detail page %s", current_url)
            try:
                page.wait_for_selector(
                    "#__NEXT_DATA__, script[type='application/ld+json']",
                    timeout=8000
                )
            except PWTimeout:
                pass
            listing = scrape_detail_page(page, current_url, listing_type=listing_type)
            if listing and listing.lat and listing.lon:
                dist = _haversine(lat, lon, listing.lat, listing.lon)
                if dist <= proximity_m:
                    results.append(listing)
            context.close()
            return results

        # Extract detail URLs from search results
        detail_urls = extract_detail_urls(page)
        log.info("Address lookup: found %d listings on search page", len(detail_urls))

        if not detail_urls:
            context.close()
            return []

        # Scrape up to MAX_DETAIL_PAGES detail pages
        for i, detail_url in enumerate(detail_urls[:MAX_DETAIL_PAGES]):
            log.info("Address lookup: [%d/%d] %s", i + 1, min(len(detail_urls), MAX_DETAIL_PAGES), detail_url)
            try:
                if not _goto_with_retry(page, detail_url):
                    log.warning("  DataDome blocked — skipping")
                    continue

                try:
                    page.wait_for_selector(
                        "#__NEXT_DATA__, script[type='application/ld+json']",
                        timeout=8000
                    )
                except PWTimeout:
                    pass

                listing = scrape_detail_page(page, detail_url, listing_type=listing_type)
                if listing is None or listing.price_amount is None:
                    log.warning("  No price extracted — skipping")
                    continue

                # Filter by proximity to target
                if listing.lat and listing.lon:
                    dist = _haversine(lat, lon, listing.lat, listing.lon)
                    if dist <= proximity_m:
                        log.info("  Match: %.0fm away, EUR %.0f, %sm2", dist, listing.price_amount, listing.size_sqm)
                        results.append(listing)
                    else:
                        log.info("  Too far: %.0fm (limit %dm)", dist, proximity_m)

                if len(results) >= max_results:
                    break

            except PWTimeout:
                log.warning("  Timeout — skipping")
            except Exception as e:
                log.exception("  Error: %s", e)

            _polite_delay()

        context.close()

    log.info("Address lookup: found %d matching listings", len(results))
    return results
