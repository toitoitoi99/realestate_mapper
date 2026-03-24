"""
Base scraper class. All source scrapers inherit from this.
Handles session management, rate limiting, retry logic, and headers.
"""

import time
import random
import logging
from abc import ABC, abstractmethod
from typing import Iterator, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Rotate through a few realistic user agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]


class BaseScraper(ABC):
    """
    Abstract base class for all real estate scrapers.

    Subclasses must implement:
      - source_name (str property)
      - listing_pages() -> Iterator[str]  (yields page HTML)
      - parse_listings(html) -> list[Listing]
    """

    # Minimum and maximum seconds to wait between requests
    MIN_DELAY: float = 2.0
    MAX_DELAY: float = 5.0

    def __init__(self):
        self.session = self._build_session()
        self._request_count = 0

    # ── Abstract interface ───────────────────────────────────────────────────

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short identifier, e.g. 'idealista'."""
        ...

    @abstractmethod
    def listing_pages(self) -> Iterator[str]:
        """
        Generator that yields raw HTML strings for each listing page.
        Handles pagination internally.
        """
        ...

    @abstractmethod
    def parse_listings(self, html: str) -> list:
        """
        Parse a listing-page HTML string.
        Returns a list of Listing objects.
        """
        ...

    # ── HTTP helpers ─────────────────────────────────────────────────────────

    def _build_session(self) -> requests.Session:
        session = requests.Session()

        # Retry on transient server errors (not 403/429 — those need back-off)
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        session.headers.update(self._base_headers())
        return session

    def _base_headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    def get(self, url: str, **kwargs) -> Optional[requests.Response]:
        """
        Polite GET with random delay and error handling.
        Returns None on unrecoverable errors (403, 429, etc.)
        """
        self._polite_delay()

        # Rotate user-agent every ~10 requests
        if self._request_count % 10 == 0:
            self.session.headers["User-Agent"] = random.choice(USER_AGENTS)

        try:
            resp = self.session.get(url, timeout=20, **kwargs)
            self._request_count += 1

            if resp.status_code == 200:
                return resp

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                logger.warning(f"[{self.source_name}] Rate limited. Waiting {wait}s…")
                time.sleep(wait)
                return self.get(url, **kwargs)  # one retry

            if resp.status_code == 403:
                logger.error(
                    f"[{self.source_name}] 403 Forbidden for {url}. "
                    "The site may require a Playwright-based scraper. "
                    "See scrapers/playwright_idealista.py for the headless-browser version."
                )
                return None

            logger.warning(f"[{self.source_name}] HTTP {resp.status_code} for {url}")
            return None

        except requests.RequestException as e:
            logger.error(f"[{self.source_name}] Request error for {url}: {e}")
            return None

    def _polite_delay(self):
        delay = random.uniform(self.MIN_DELAY, self.MAX_DELAY)
        time.sleep(delay)

    # ── Main entry point ─────────────────────────────────────────────────────

    def scrape(self) -> list:
        """
        Run the full scrape. Returns a flat list of Listing objects.
        """
        from models import ScrapeRun
        all_listings = []
        errors = 0

        logger.info(f"[{self.source_name}] Starting scrape…")

        for html in self.listing_pages():
            try:
                listings = self.parse_listings(html)
                all_listings.extend(listings)
                logger.info(
                    f"[{self.source_name}] Page parsed — "
                    f"{len(listings)} listings (total so far: {len(all_listings)})"
                )
            except Exception as e:
                errors += 1
                logger.error(f"[{self.source_name}] Parse error: {e}")

        logger.info(
            f"[{self.source_name}] Done. "
            f"{len(all_listings)} listings, {errors} errors."
        )
        return all_listings
