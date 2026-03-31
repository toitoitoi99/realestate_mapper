"""
Idealista.pt scraper for Lisbon real estate listings.

Targets: https://www.idealista.pt/comprar-casas/lisboa/

NOTE ON ANTI-BOT PROTECTION
----------------------------
Idealista uses Cloudflare and JS-rendered content on some pages.
This requests-based scraper works in many cases but may receive 403s.
If that happens, switch to the Playwright version (playwright_idealista.py)
which runs a real headless browser and bypasses most bot detection.

Data captured per listing:
  - Price (€)
  - Area (m²)
  - Rooms (typology: T0–T5+)
  - Neighborhood / parish
  - Listing URL
  - Price per m² (computed)
"""

import re
import time
import hashlib
import logging
from typing import Iterator, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from models import Listing

logger = logging.getLogger(__name__)

BASE_URL = "https://www.idealista.pt"

# Lisbon buy listings. Change "comprar-casas" → "arrendar-casas" for rentals.
SEARCH_URL = "https://www.idealista.pt/comprar-casas/lisboa/"

# Max pages to scrape per run (each page ~30 listings).
# Idealista caps public access at ~60 pages without login.
MAX_PAGES = 50


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


class IdealistaScraper(BaseScraper):

    source_name = "idealista"

    # Slightly slower — Idealista is aggressive about rate limiting
    MIN_DELAY = 3.0
    MAX_DELAY = 7.0

    def __init__(self, search_url: str = SEARCH_URL, max_pages: int = MAX_PAGES):
        super().__init__()
        self.search_url = search_url
        self.max_pages = max_pages
        self._geocode_cache = {}  # type: dict[str, Optional[Tuple[float, float]]]
        self._last_geocode_ts = 0.0

        # Warm up the session with a homepage visit (sets cookies)
        self._warm_up()

    def _warm_up(self):
        """Visit homepage first to pick up cookies and avoid cold-start 403s."""
        logger.info("[idealista] Warming up session…")
        resp = self.session.get(BASE_URL, timeout=20)
        if resp and resp.status_code == 200:
            logger.info("[idealista] Session warmed up.")
        else:
            logger.warning("[idealista] Warm-up failed — proceeding anyway.")

    # ── Pagination ───────────────────────────────────────────────────────────

    def listing_pages(self) -> Iterator[str]:
        """
        Yield the HTML of each search results page.
        Stops when no more listings are found or max_pages is reached.
        """
        url = self.search_url

        for page_num in range(1, self.max_pages + 1):
            logger.info(f"[idealista] Fetching page {page_num}: {url}")
            resp = self.get(url)

            if resp is None:
                logger.warning(f"[idealista] No response for page {page_num}. Stopping.")
                break

            html = resp.text
            yield html

            # Find the "next page" link
            next_url = self._next_page_url(html, page_num)
            if not next_url:
                logger.info(f"[idealista] No next page after page {page_num}. Done.")
                break

            url = next_url

    def _next_page_url(self, html: str, current_page: int) -> Optional[str]:
        """Extract the URL for the next results page."""
        soup = BeautifulSoup(html, "lxml")

        # Idealista uses a <a class="icon-arrow-right-after"> for next page
        next_link = soup.select_one("a.icon-arrow-right-after")
        if next_link and next_link.get("href"):
            return urljoin(BASE_URL, next_link["href"])

        # Fallback: construct paginated URL directly
        # Pattern: /comprar-casas/lisboa/pagina-N.htm
        if current_page == 1:
            next_url = self.search_url.rstrip("/") + f"/pagina-2.htm"
        else:
            # Check if page N+1 exists by trying the pattern
            next_url = re.sub(
                r"/pagina-\d+\.htm",
                f"/pagina-{current_page + 1}.htm",
                self.search_url.rstrip("/")
            )
            if f"/pagina-{current_page + 1}.htm" not in next_url:
                next_url = self.search_url.rstrip("/") + f"/pagina-{current_page + 1}.htm"

        return next_url

    # ── Parsing ──────────────────────────────────────────────────────────────

    def parse_listings(self, html: str) -> list:
        """Parse a search results page and return Listing objects."""
        soup = BeautifulSoup(html, "lxml")
        listings = []

        # Each listing is an <article class="item"> or similar
        articles = soup.select("article.item")

        if not articles:
            # Try alternate selector used on some versions of the page
            articles = soup.select("div.item-info-container")
            logger.debug(f"[idealista] Fallback selector found {len(articles)} items")

        for article in articles:
            try:
                listing = self._parse_article(article)
                if listing:
                    listings.append(listing)
            except Exception as e:
                logger.warning(f"[idealista] Failed to parse article: {e}")

        return listings

    def _parse_article(self, article) -> Optional[Listing]:
        """Parse a single listing article tag into a Listing."""

        # ── URL and ID ───────────────────────────────────────────────────────
        link_tag = article.select_one("a.item-link, a[href*='/imovel/']")
        if not link_tag:
            return None

        href = link_tag.get("href", "")
        url = urljoin(BASE_URL, href)

        # Extract source_id from URL path, e.g. /imovel/12345678/
        source_id_match = re.search(r"/imovel/(\d+)", href)
        source_id = source_id_match.group(1) if source_id_match else href

        title = link_tag.get_text(strip=True) or None

        # ── Price ────────────────────────────────────────────────────────────
        price = None
        price_tag = article.select_one(
            "span.item-price, .price-row span, span[class*='price']"
        )
        if price_tag:
            price = self._parse_price(price_tag.get_text())

        # ── Area and rooms ───────────────────────────────────────────────────
        area_sqm = None
        rooms = None

        detail_items = article.select(
            "span.item-detail, .item-detail-char span, span[class*='detail']"
        )
        for item in detail_items:
            text = item.get_text(strip=True)

            # Rooms: T0, T1, T2, T3, T4, T5, T4+ etc.
            room_match = re.match(r"T(\d+)\+?", text, re.IGNORECASE)
            if room_match:
                rooms = int(room_match.group(1))
                continue

            # Area: "120 m²" or "120m²"
            area_match = re.search(r"([\d.,]+)\s*m[²2]", text, re.IGNORECASE)
            if area_match:
                area_sqm = self._parse_number(area_match.group(1))
                continue

        # ── Location ─────────────────────────────────────────────────────────
        neighborhood = None
        address = None

        location_tag = article.select_one(
            "span.item-detail-location, span[class*='location'], "
            "p.item-detail, .item-address"
        )
        if location_tag:
            address = location_tag.get_text(strip=True)
            # Lisbon addresses often end with the neighborhood, e.g.
            # "Rua de Algures, 12, Alfama, Lisboa"
            # Grab the last meaningful part before "Lisboa"
            neighborhood = self._extract_neighborhood(address)

        # ── Price per m² ─────────────────────────────────────────────────────
        price_per_sqm = None
        if price and area_sqm and area_sqm > 0:
            price_per_sqm = round(price / area_sqm, 2)

        # ── Property type ────────────────────────────────────────────────────
        property_type = self._infer_property_type(title or "", rooms)

        # ── Geocode ──────────────────────────────────────────────────────────
        lat = None
        lon = None
        if address:
            coords = self._geocode(address, "Lisboa")
            if coords:
                lat, lon = coords

        addr = (address or "").lower().strip()
        price_r = str(round(price / 1000) * 1000) if price else ""
        size_r = str(round(area_sqm)) if area_sqm else ""
        raw = f"{addr}|lisboa|{price_r}|{size_r}|idealista"
        hash_dedupe = hashlib.sha1(raw.encode()).hexdigest()

        return Listing(
            source="idealista",
            source_id=source_id,
            url=url,
            price_amount=price,
            price_per_sqm=price_per_sqm,
            size_sqm=area_sqm,
            rooms=rooms,
            property_type=property_type,
            title=title,
            address=address,
            neighborhood=neighborhood,
            district="Lisboa",
            lat=lat,
            lon=lon,
            hash_dedupe=hash_dedupe,
        )

    # ── Geocoding ─────────────────────────────────────────────────────────────

    def _geocode(self, address: str, district: str) -> Optional[Tuple[float, float]]:
        """
        Geocode an address via Nominatim. Returns (lat, lon) or None.
        Respects Nominatim's 1 req/s rate limit and caches results.
        """
        query = f"{address}, {district}, Portugal"
        if query in self._geocode_cache:
            return self._geocode_cache[query]

        # Enforce 1 req/s for Nominatim
        elapsed = time.time() - self._last_geocode_ts
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "pt",
                },
                headers={"User-Agent": "realestate-mapper/1.0"},
                timeout=10,
            )
            self._last_geocode_ts = time.time()

            if resp.status_code == 200:
                results = resp.json()
                if results:
                    coords = (float(results[0]["lat"]), float(results[0]["lon"]))
                    self._geocode_cache[query] = coords
                    return coords
                # Genuine miss — safe to cache
                self._geocode_cache[query] = None
                return None

            logger.debug(f"[idealista] Geocode HTTP {resp.status_code} for: {query}")
        except Exception as e:
            logger.debug(f"[idealista] Geocode error for {query}: {e}")

        return None

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        """
        Parse price strings like '250.000 €', '1.200.000€', '250000€'.
        Portuguese format uses '.' as thousands separator.
        """
        # Remove everything except digits and dots/commas
        clean = re.sub(r"[^\d.,]", "", text)
        if not clean:
            return None
        # In PT format, '.' = thousands separator, ',' = decimal
        # Remove thousands dots, replace comma decimal with '.'
        clean = clean.replace(".", "").replace(",", ".")
        try:
            return float(clean)
        except ValueError:
            return None

    @staticmethod
    def _parse_number(text: str) -> Optional[float]:
        """Parse a general number string, handling PT formatting."""
        clean = text.replace(".", "").replace(",", ".")
        try:
            return float(clean)
        except ValueError:
            return None

    @staticmethod
    def _extract_neighborhood(address: str) -> Optional[str]:
        """
        Best-effort neighborhood extraction from a Lisbon address string.
        Example: "Rua X, Alfama, Lisboa" → "Alfama"
        """
        if not address:
            return None

        parts = [p.strip() for p in address.split(",")]

        # Filter out known non-neighborhood parts
        skip = {"lisboa", "portugal", ""}
        candidates = [p for p in parts if p.lower() not in skip]

        # The second-to-last part is usually the neighborhood
        if len(candidates) >= 2:
            return candidates[-2]
        if candidates:
            return candidates[-1]
        return None

    @staticmethod
    def _infer_property_type(title: str, rooms: Optional[int]) -> str:
        """Infer property type from title text."""
        title_lower = title.lower()
        if any(w in title_lower for w in ["moradia", "vivenda", "villa", "house"]):
            return "house"
        if any(w in title_lower for w in ["estúdio", "studio"]):
            return "studio"
        if any(w in title_lower for w in ["loft"]):
            return "loft"
        if rooms == 0:
            return "studio"
        return "apartment"


# ── CLI runner ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import database as db
    db.init_db()

    scraper = IdealistaScraper(max_pages=3)  # limit for testing
    run_id = db.start_scrape_run("idealista")

    listings = scraper.scrape()

    new_count = updated_count = 0
    for listing in listings:
        _, is_new = db.upsert_listing(listing)
        if is_new:
            new_count += 1
        else:
            updated_count += 1

    db.rebuild_neighborhoods()

    from models import ScrapeRun
    from datetime import datetime
    run = ScrapeRun(
        source="idealista",
        listings_found=len(listings),
        listings_new=new_count,
        listings_updated=updated_count,
        status="completed",
    )
    db.finish_scrape_run(run_id, run)

    print(f"\n✓ Scraped {len(listings)} listings ({new_count} new, {updated_count} updated)")

    # Print a sample
    sample = [l.to_dict() for l in listings[:3]]
    print(json.dumps(sample, indent=2, ensure_ascii=False))
