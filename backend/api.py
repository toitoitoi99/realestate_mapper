"""
REST API server for the Lisboa Real Estate app.
Built with Tornado (stdlib-compatible, no extra install needed beyond pip).

Endpoints
─────────
GET  /api/listings            – query listings with filters
GET  /api/listings/:id        – single listing detail
GET  /api/neighborhoods       – neighborhood stats + geometry
GET  /api/neighborhoods/:name – single neighborhood
GET  /api/scrape-runs         – scrape audit history
POST /api/scrape              – trigger a scrape job (async)
GET  /api/stats               – overall summary stats

Run with:  python api.py
"""

import json
import logging
import os
import threading
import sys
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import tornado.ioloop
import tornado.web

# Make sure the backend directory is on the path
sys.path.insert(0, str(Path(__file__).parent))

import database as db
from models import ScrapeRun

# Scrapers available via /api/scrape — keyed by the `source` string written
# to the scrape_runs table. Each module exposes `run_scraper(...)` with the
# same signature and manages its own scrape_runs row lifecycle.
SCRAPER_MODULES = {
    "idealista":  "scrapers.idealista_playwright",
    "era":        "scrapers.era_playwright",
    "remax":      "scrapers.remax_playwright",
    "imovirtual": "scrapers.imovirtual_playwright",
    "olx":        "scrapers.olx_playwright",
    "casa_sapo":  "scrapers.casa_sapo_playwright",
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8000))
_executor = ThreadPoolExecutor(max_workers=4)
_address_lookup_lock = threading.Lock()
_score_lock = threading.Lock()

# ── Area config ──────────────────────────────────────────────────────────────

AREAS_PATH = Path(__file__).parent / "areas.json"
AREAS = json.loads(AREAS_PATH.read_text(encoding="utf-8"))


# ── Base handler ─────────────────────────────────────────────────────────────

class BaseHandler(tornado.web.RequestHandler):

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")

    def options(self, *args, **kwargs):
        self.set_status(204)
        self.finish()

    def write_json(self, data, status: int = 200):
        self.set_status(status)
        self.write(json.dumps(data, ensure_ascii=False, default=str))

    def write_error_json(self, message: str, status: int = 400):
        self.set_status(status)
        self.write(json.dumps({"error": message}))

    def get_int_arg(self, name, default=None):
        val = self.get_argument(name, None)
        if val is None:
            return default
        try:
            return int(val)
        except ValueError:
            raise tornado.web.HTTPError(400, reason=f"Invalid value for '{name}': expected integer")

    def get_float_arg(self, name, default=None):
        val = self.get_argument(name, None)
        if val is None:
            return default
        try:
            return float(val)
        except ValueError:
            raise tornado.web.HTTPError(400, reason=f"Invalid value for '{name}': expected number")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_weights_arg(raw):
    """Parse a JSON-encoded {signal: weight} map from a query string. Returns
    None on missing/invalid input — caller falls back to persona defaults."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    out = {}
    for k, v in parsed.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f < 0:
            continue
        out[str(k)] = f
    return out or None


# ── Handlers ─────────────────────────────────────────────────────────────────

class ListingsHandler(BaseHandler):
    """GET /api/listings"""

    def get(self):
        listings = db.get_listings(
            neighborhood=self.get_argument("neighborhood", None),
            source=self.get_argument("source", None),
            min_price=self.get_float_arg("min_price"),
            max_price=self.get_float_arg("max_price"),
            min_sqm=self.get_float_arg("min_sqm"),
            max_sqm=self.get_float_arg("max_sqm"),
            rooms=self.get_int_arg("rooms"),
            listing_type=self.get_argument("listing_type", None),
            sold_after=self.get_argument("sold_after", None),
            sold_before=self.get_argument("sold_before", None),
            min_price_per_sqm=self.get_float_arg("min_price_per_sqm"),
            max_price_per_sqm=self.get_float_arg("max_price_per_sqm"),
            bedrooms=self.get_int_arg("bedrooms"),
            bathrooms=self.get_int_arg("bathrooms"),
            floor=self.get_argument("floor", None),
            property_type=self.get_argument("property_type", None),
            condition=self.get_argument("condition", None),
            parish=self.get_argument("parish", None),
            district=self.get_argument("district", None),
            city=self.get_argument("city", None),
            postal_code=self.get_argument("postal_code", None),
            grant_eligible=self.get_argument("grant_eligible", None) == "true" or None,
            min_deal_score=self.get_float_arg("min_deal_score"),
            min_rarity_score=self.get_float_arg("min_rarity_score"),
            min_flip_score=self.get_float_arg("min_flip_score"),
            min_rent_score=self.get_float_arg("min_rent_score"),
            region_profile=self.get_argument("region_profile", None),
            persona=self.get_argument("persona", None),
            weights_override=_parse_weights_arg(self.get_argument("weights", None)),
            bedrooms_min=self.get_int_arg("bedrooms_min"),
            style_primary=self.get_argument("style_primary", None),
            outdoor_required=self.get_argument("outdoor_required", None) == "true",
            max_renovation=self.get_argument("max_renovation", None),
            strict_tags=self.get_argument("strict_tags", None) == "true",
            limit=self.get_int_arg("limit", 10000),
            offset=self.get_int_arg("offset", 0),
        )
        self.write_json({"count": len(listings), "listings": listings})


class ListingDetailHandler(BaseHandler):
    """GET /api/listings/:id?listing_type=sale|rent"""

    def get(self, listing_id: str):
        listing_type = self.get_argument("listing_type", "sale")
        table = db._table_for(listing_type)
        conn = db.get_connection()
        row = conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (listing_id,)
        ).fetchone()
        if not row:
            # Try the other table as fallback
            other = "rentals" if table == "sales" else "sales"
            row = conn.execute(
                f"SELECT * FROM {other} WHERE id=?", (listing_id,)
            ).fetchone()
            if row:
                listing_type = "rent" if other == "rentals" else "sale"
        conn.close()
        if not row:
            self.write_error_json("Listing not found", 404)
            return
        result = dict(row)
        result["listing_type"] = listing_type
        result["cross_listings"] = db.get_cross_listings(
            result.get("hash_cross"), result["id"], listing_type
        )

        # Resolve previous listing price for re-list tracking
        prev_id = result.get("previous_listing_id")
        if prev_id:
            actual_table = db._table_for(listing_type)
            conn2 = db.get_connection()
            prev_row = conn2.execute(
                f"SELECT price_amount, price_per_sqm FROM {actual_table} WHERE id=?", (prev_id,)
            ).fetchone()
            conn2.close()
            if prev_row:
                result["previous_price_amount"] = prev_row["price_amount"]
                result["previous_price_per_sqm"] = prev_row["price_per_sqm"]

        # Grant eligibility
        grant = db.get_grant_details(result.get("city"), result.get("parish"))
        result["grant_eligible"] = grant is not None
        result["grant_details"] = grant

        self.write_json(result)


class NeighborhoodsHandler(BaseHandler):
    """GET /api/neighborhoods"""

    def get(self):
        neighborhoods = db.get_neighborhoods(
            district=self.get_argument("district", None)
        )
        # Compute color gradient values (0.0–1.0) relative to min/max price/sqm
        neighborhoods = _add_gradient_values(neighborhoods)
        self.write_json({"count": len(neighborhoods), "neighborhoods": neighborhoods})


class NeighborhoodDetailHandler(BaseHandler):
    """GET /api/neighborhoods/:name"""

    def get(self, name: str):
        conn = db.get_connection()
        row = conn.execute(
            "SELECT * FROM neighborhoods WHERE name=?", (name,)
        ).fetchone()
        conn.close()
        if not row:
            self.write_error_json("Neighborhood not found", 404)
            return
        self.write_json(dict(row))


class ScrapeRunsHandler(BaseHandler):
    """GET /api/scrape-runs"""

    def get(self):
        runs = db.get_scrape_runs(
            source=self.get_argument("source", None),
            limit=self.get_int_arg("limit", 20),
        )
        self.write_json({"runs": runs})


class ScrapeHandler(BaseHandler):
    """
    POST /api/scrape
    Body (optional JSON): {"source": "idealista", "max_pages": 5}

    Triggers a scrape in a background thread and returns immediately.
    Poll /api/scrape-runs to track progress.
    """

    def post(self):
        body = {}
        try:
            if self.request.body:
                body = json.loads(self.request.body)
        except json.JSONDecodeError:
            pass

        source = body.get("source", "idealista")
        max_pages = int(body.get("max_pages", 10))

        if source not in SCRAPER_MODULES:
            self.write_error_json(
                f"Unknown source '{source}'. Available: {sorted(SCRAPER_MODULES.keys())}"
            )
            return

        # Fire off in a daemon thread so the HTTP response returns immediately.
        # The scraper itself creates/updates its scrape_runs row; poll
        # /api/scrape-runs?source=<source>&limit=1 to track progress.
        thread = threading.Thread(
            target=_run_scrape,
            args=(source, max_pages),
            daemon=True,
        )
        thread.start()

        self.write_json({
            "message": f"Scrape started for '{source}'",
            "source": source,
        }, status=202)


class ProjectsHandler(BaseHandler):
    """GET /api/projects — construction projects from CML open data"""

    def get(self):
        projects = db.get_projects(
            layer=self.get_argument("layer", None),
            parish=self.get_argument("parish", None),
            limit=self.get_int_arg("limit", 5000),
        )
        self.write_json({"count": len(projects), "projects": projects})


class SecurityHandler(BaseHandler):
    """GET /api/security — police stations and CCTV cameras from CML open data"""

    def get(self):
        pois = db.get_security_pois(
            layer=self.get_argument("layer", None)
        )
        self.write_json({"count": len(pois), "pois": pois})


class IneStatsHandler(BaseHandler):
    """GET /api/ine-stats — INE aggregate transaction prices for Lisboa"""

    def get(self):
        geocod_param = self.get_argument("geocod", "1A01106")
        geocod = None if geocod_param == "all" else geocod_param
        latest_only = self.get_argument("latest_only", "true").lower() != "false"
        stats = db.get_ine_stats(geocod=geocod, latest_only=latest_only)
        self.write_json({"count": len(stats), "stats": stats})


class SoldTrendsHandler(BaseHandler):
    """GET /api/sold-trends — per-parish price change for sold transactions"""

    def get(self):
        start_date = self.get_argument("start", "2024-01-01")
        end_date = self.get_argument("end", "2026-03-01")
        trends = db.get_sold_trends(start_date=start_date, end_date=end_date)
        points = db.get_sold_points(start_date=start_date, end_date=end_date)
        self.write_json({
            "trends": trends,
            "points": points,
            "start": start_date,
            "end": end_date,
        })


class AmenityRatingHandler(BaseHandler):
    """GET /api/amenity-rating?lat=X&lon=Y — neighborhood amenity score"""

    async def get(self):
        lat = self.get_float_arg("lat")
        lon = self.get_float_arg("lon")
        if lat is None or lon is None:
            self.write_error_json("lat and lon are required", 400)
            return

        from amenity_rating import get_amenity_rating
        rating = await tornado.ioloop.IOLoop.current().run_in_executor(
            _executor, get_amenity_rating, lat, lon
        )
        self.write_json(rating)


class AreasHandler(BaseHandler):
    """GET /api/areas — available geographic areas"""

    def get(self):
        self.write_json(AREAS)


class ParishesHandler(BaseHandler):
    """GET /api/parishes — parish boundaries (GeoJSON FeatureCollection)"""

    _cache = {}

    def get(self):
        area = self.get_argument("area", "lisbon")
        area_cfg = AREAS.get(area, AREAS.get("lisbon", {}))
        geojson_file = area_cfg.get("parishes_geojson")
        if not geojson_file:
            self.write_json({"type": "FeatureCollection", "features": []})
            return
        if geojson_file not in ParishesHandler._cache:
            geojson_path = Path(__file__).parent / "data" / geojson_file
            if not geojson_path.exists():
                self.write_json({"type": "FeatureCollection", "features": []})
                return
            ParishesHandler._cache[geojson_file] = json.loads(geojson_path.read_text(encoding="utf-8"))
        self.write_json(ParishesHandler._cache[geojson_file])


class StatsHandler(BaseHandler):
    """GET /api/stats — overall summary"""

    def get(self):
        conn = db.get_connection()

        sales_count = conn.execute("SELECT COUNT(*) as c FROM sales").fetchone()["c"]
        rentals_count = conn.execute("SELECT COUNT(*) as c FROM rentals").fetchone()["c"]
        total = sales_count + rentals_count

        # Source breakdown across both tables
        by_source = {}
        for table in ("sales", "rentals"):
            for r in conn.execute(f"SELECT source, COUNT(*) as count FROM {table} GROUP BY source").fetchall():
                by_source[r["source"]] = by_source.get(r["source"], 0) + r["count"]

        import statistics

        # Median is robust to outliers from scraper glitches (e.g. prices
        # parsed as concatenated digits), which can blow AVG up by orders
        # of magnitude and make the banner unreadable.
        sale_prices = [r["p"] for r in conn.execute(
            "SELECT price_amount AS p FROM sales WHERE price_amount IS NOT NULL"
        ).fetchall()]
        sale_psqm = [r["p"] for r in conn.execute(
            "SELECT price_per_sqm AS p FROM sales WHERE price_per_sqm IS NOT NULL"
        ).fetchall()]
        rent_prices = [r["p"] for r in conn.execute(
            "SELECT price_amount AS p FROM rentals WHERE price_amount IS NOT NULL"
        ).fetchall()]

        median_price = statistics.median(sale_prices) if sale_prices else None
        median_psqm = statistics.median(sale_psqm) if sale_psqm else None
        median_rent = statistics.median(rent_prices) if rent_prices else None

        neighborhoods = conn.execute(
            "SELECT COUNT(*) as c FROM neighborhoods"
        ).fetchone()["c"]
        last_scrape = conn.execute(
            "SELECT MAX(started_at) as ts FROM scrape_runs WHERE status='completed'"
        ).fetchone()["ts"]
        conn.close()

        self.write_json({
            "total_listings": total,
            "sales_count": sales_count,
            "rentals_count": rentals_count,
            "by_source": by_source,
            "median_price_eur": round(median_price, 2) if median_price else None,
            "median_price_per_sqm": round(median_psqm, 2) if median_psqm else None,
            "median_rent_eur": round(median_rent, 2) if median_rent else None,
            "neighborhood_count": neighborhoods,
            "last_scrape": last_scrape,
        })


# ── Translation helper ────────────────────────────────────────────────────────

def _translate_pt_to_en(text):
    """Translate Portuguese text to English using Google Translate (free endpoint)."""
    # Split into chunks of ~4000 chars to stay within limits
    chunks = []
    while text:
        if len(text) <= 4000:
            chunks.append(text)
            break
        # Find a good split point (sentence boundary)
        split_at = text.rfind('. ', 0, 4000)
        if split_at == -1:
            split_at = text.rfind(' ', 0, 4000)
        if split_at == -1:
            split_at = 4000
        else:
            split_at += 1
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()

    translated_parts = []
    for chunk in chunks:
        encoded = urllib.parse.quote(chunk)
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=pt&tl=en&dt=t&q={encoded}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        # Response is nested array: [[["translated", "original", ...], ...], ...]
        translated_parts.append("".join(seg[0] for seg in data[0] if seg[0]))

    return " ".join(translated_parts)


class TranslateHandler(BaseHandler):
    """POST /api/translate — translate a listing description to English"""

    async def post(self):
        body = json.loads(self.request.body or "{}")
        listing_id = body.get("id")
        listing_type = body.get("listing_type", "sale")

        if not listing_id:
            self.write_error_json("id is required", 400)
            return

        table = db._table_for(listing_type)
        conn = db.get_connection()
        row = conn.execute(
            f"SELECT description, description_en FROM {table} WHERE id=?",
            (listing_id,)
        ).fetchone()

        if not row:
            conn.close()
            self.write_error_json("Listing not found", 404)
            return

        if not row["description"]:
            conn.close()
            self.write_json({"description_en": None})
            return

        # Return cached translation if available
        if row["description_en"]:
            conn.close()
            self.write_json({"description_en": row["description_en"]})
            return

        # Translate in thread pool to avoid blocking
        try:
            translated = await tornado.ioloop.IOLoop.current().run_in_executor(
                _executor, _translate_pt_to_en, row["description"]
            )
            # Cache in DB
            conn.execute(
                f"UPDATE {table} SET description_en=? WHERE id=?",
                (translated, listing_id)
            )
            conn.commit()
            conn.close()
            self.write_json({"description_en": translated})
        except Exception as e:
            conn.close()
            logger.error(f"Translation failed: {e}")
            self.write_error_json(f"Translation failed: {e}", 500)


# ── Background scrape logic ───────────────────────────────────────────────────

def _run_scrape(source: str, max_pages: int):
    """Runs in a background thread.

    Dispatches to the Playwright scraper module for `source`. Each module's
    `run_scraper()` creates its own scrape_runs row and writes the final
    status itself. We only write a fallback 'failed' row if the dispatch
    itself blows up before the scraper had a chance to do so (e.g. import
    error).
    """
    module_name = SCRAPER_MODULES.get(source)
    if module_name is None:
        logger.error(f"[scrape] Unknown source: {source}")
        return

    try:
        import importlib
        module = importlib.import_module(module_name)
        run_scraper = getattr(module, "run_scraper")
        logger.info(f"[scrape] Starting {source} (max_pages={max_pages})")
        run_scraper(max_pages=max_pages)
        logger.info(f"[scrape] {source} finished")
    except Exception as e:
        logger.exception(f"[scrape] {source} crashed in dispatcher: {e}")
        # Safety-net: the scraper either never wrote a row, or wrote a
        # `running` row but crashed before `finish_scrape_run`. In the
        # latter case, mark the orphaned row as failed; otherwise create
        # a fresh failed row so the UI sees *some* terminal state.
        try:
            latest = db.get_scrape_runs(source=source, limit=1)
            if latest and latest[0].get("status") == "running":
                db.finish_scrape_run(
                    latest[0]["id"],
                    ScrapeRun(source=source, status="failed", notes=f"dispatcher: {e}"),
                )
            else:
                run_id = db.start_scrape_run(source)
                db.finish_scrape_run(
                    run_id,
                    ScrapeRun(source=source, status="failed", notes=f"dispatcher: {e}"),
                )
        except Exception:
            pass


def _run_score():
    """Runs rarity + signal_batch scoring in a background thread."""
    run_id = db.start_scrape_run("scorer")
    try:
        import signal_batch
        logger.info("[score] Computing rarity scores")
        db.compute_rarity_scores()
        logger.info("[score] Running signal batch (only_stale=True)")
        signal_batch.run_batch(only_stale=True)
        db.finish_scrape_run(run_id, ScrapeRun(source="scorer", status="completed"))
        logger.info("[score] Done")
    except Exception as e:
        logger.exception(f"[score] Failed: {e}")
        try:
            db.finish_scrape_run(
                run_id, ScrapeRun(source="scorer", status="failed", notes=str(e)[:500])
            )
        except Exception:
            pass
    finally:
        _score_lock.release()


class ScoreHandler(BaseHandler):
    """
    POST /api/score

    Triggers rarity + flip/rent scoring for stale listings in a background
    thread and returns immediately. Poll /api/scrape-runs?source=scorer to
    track progress.
    """

    def post(self):
        if not _score_lock.acquire(blocking=False):
            self.set_status(409)
            self.write_json({"error": "A scoring run is already in progress"})
            return

        thread = threading.Thread(target=_run_score, daemon=True)
        thread.start()
        self.write_json({"message": "Scoring started"}, status=202)


# ── Gradient helper ───────────────────────────────────────────────────────────

def _add_gradient_values(neighborhoods: list) -> list:
    """
    Add a 'gradient_value' (0.0 = cheapest, 1.0 = most expensive) to each
    neighborhood, based on median price/m².
    The frontend uses this to drive the color scale.
    """
    prices = [n["median_price_per_sqm"] for n in neighborhoods if n["median_price_per_sqm"]]
    if not prices:
        for n in neighborhoods:
            n["gradient_value"] = None
        return neighborhoods

    min_p, max_p = min(prices), max(prices)
    rng = max_p - min_p if max_p != min_p else 1

    for n in neighborhoods:
        p = n.get("median_price_per_sqm")
        n["gradient_value"] = round((p - min_p) / rng, 4) if p is not None else None

    return neighborhoods


class ComparisonHandler(BaseHandler):
    """GET /api/listings/:id/compare"""

    def get(self, listing_id):
        listing_type = self.get_argument("listing_type", "sale")
        radius_m = self.get_int_arg("radius_m", 500)
        radius_m = max(100, min(3000, radius_m))
        filter_property_type = self.get_argument("property_type", None)
        filter_bedrooms = self.get_int_arg("bedrooms", None)

        result = db.get_radius_comparison(
            int(listing_id), listing_type, radius_m,
            filter_property_type, filter_bedrooms
        )
        self.write_json(result)


class CompareListingsHandler(BaseHandler):
    """POST /api/compare-listings
    Body: { a: {id, kind}, b: {id, kind}, persona?: str }
    Returns Claude-generated pros/cons + recommendation for both listings.
    """

    async def post(self):
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.write_error_json("invalid JSON body", 400); return

        def _fetch(lid, kind):
            table = db._table_for(kind)
            conn = db.get_connection()
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (lid,)).fetchone()
            conn.close()
            if not row:
                return None
            r = dict(row)
            r["listing_type"] = kind
            return r

        spec_a = body.get("a") or {}
        spec_b = body.get("b") or {}
        persona = body.get("persona")

        listing_a = _fetch(spec_a.get("id"), spec_a.get("kind", "sale")) if spec_a.get("id") else None
        listing_b = _fetch(spec_b.get("id"), spec_b.get("kind", "sale")) if spec_b.get("id") else None

        if not listing_a:
            self.write_error_json("listing A not found", 404); return
        if not listing_b:
            self.write_error_json("listing B not found", 404); return

        try:
            from scorers.listing_comparator import compare_listings
            result = await tornado.ioloop.IOLoop.current().run_in_executor(
                None, lambda: compare_listings(listing_a, listing_b, persona)
            )
        except Exception as e:
            self.write_error_json(f"comparison failed: {e}", 502); return

        self.write_json(result)


class AddressHistoryHandler(BaseHandler):
    """GET /api/listings/:id/address-history"""

    def get(self, listing_id):
        listing_type = self.get_argument("listing_type", "sale")
        matches = db.get_address_matches(int(listing_id), listing_type)
        self.write_json({"count": len(matches), "matches": matches})


class ReactionsHandler(BaseHandler):
    """GET /api/reactions — list all like/dislike reactions.
       Pass ?details=true to include the joined listing row."""

    def get(self):
        details = self.get_argument("details", "false").lower() in ("1", "true", "yes")
        reactions = db.get_reactions_with_listings() if details else db.get_reactions()
        self.write_json({"count": len(reactions), "reactions": reactions})


class AdminHealthHandler(BaseHandler):
    """GET /api/admin/health — aggregate DB/source health snapshot."""

    def get(self):
        import os
        import time
        from datetime import datetime, timedelta

        conn = db.get_connection()

        def count(table):
            try:
                return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            except Exception:
                return None

        tables = {t: count(t) for t in [
            "sales", "rentals", "neighborhoods", "construction_projects",
            "security_pois", "ine_stats", "listing_history",
            "sold_transactions", "listing_reactions", "listing_ratings", "scrape_runs",
            "amenity_ratings",
        ]}

        # DB file sizes (main + WAL + SHM)
        db_path = str(db.DB_PATH)
        def fsize(p):
            try: return os.path.getsize(p)
            except OSError: return 0
        db_info = {
            "path": db_path,
            "size_bytes": fsize(db_path),
            "wal_bytes":  fsize(db_path + "-wal"),
            "shm_bytes":  fsize(db_path + "-shm"),
        }

        # Last scraped_at per source for sales and rentals
        def per_source_last(table):
            rows = conn.execute(
                f"SELECT source, MAX(scraped_at) AS last_at, COUNT(*) AS n "
                f"FROM {table} WHERE source IS NOT NULL GROUP BY source ORDER BY last_at DESC"
            ).fetchall()
            return [{"source": r["source"], "last_at": r["last_at"], "count": r["n"]} for r in rows]

        last_updated = {
            "sales_by_source":   per_source_last("sales"),
            "rentals_by_source": per_source_last("rentals"),
        }

        # Construction + security POIs + INE latest
        try:
            row = conn.execute("SELECT MAX(fetched_at) AS t FROM construction_projects").fetchone()
            last_updated["construction_projects"] = row["t"] if row else None
        except Exception:
            last_updated["construction_projects"] = None

        try:
            row = conn.execute("SELECT MAX(fetched_at) AS t FROM security_pois").fetchone()
            last_updated["security_pois"] = row["t"] if row else None
        except Exception:
            last_updated["security_pois"] = None

        try:
            row = conn.execute(
                "SELECT period_label, MAX(fetched_at) AS t FROM ine_stats WHERE is_latest=1"
            ).fetchone()
            last_updated["ine_stats"] = {
                "period": row["period_label"] if row else None,
                "fetched_at": row["t"] if row else None,
            }
        except Exception:
            last_updated["ine_stats"] = None

        # Stale active listings: scraped_at older than N days, status='active'
        now = datetime.utcnow()
        stale = {"sales": {}, "rentals": {}}
        for table in ("sales", "rentals"):
            for days in (7, 14, 30):
                cutoff = (now - timedelta(days=days)).isoformat()
                try:
                    c = conn.execute(
                        f"SELECT COUNT(*) AS c FROM {table} "
                        f"WHERE (status IS NULL OR status='active') AND scraped_at < ?",
                        (cutoff,),
                    ).fetchone()["c"]
                except Exception:
                    c = None
                stale[table][f"gt_{days}d"] = c

        # Recent price changes from listing_history (price_amount only)
        try:
            rows = conn.execute(
                "SELECT listing_id, listing_type, source, field, old_value, new_value, changed_at "
                "FROM listing_history WHERE field='price_amount' "
                "ORDER BY changed_at DESC LIMIT 25"
            ).fetchall()
            price_changes = []
            for r in rows:
                try:
                    old_v = float(r["old_value"]) if r["old_value"] not in (None, "") else None
                    new_v = float(r["new_value"]) if r["new_value"] not in (None, "") else None
                    delta = (new_v - old_v) if (old_v is not None and new_v is not None) else None
                except (ValueError, TypeError):
                    old_v = new_v = delta = None
                price_changes.append({
                    **dict(r),
                    "old_value_num": old_v,
                    "new_value_num": new_v,
                    "delta": delta,
                })
        except Exception:
            price_changes = []

        # Recent sold transactions
        try:
            rows = conn.execute(
                "SELECT id, parish, price_amount, price_per_sqm, size_sqm, rooms, "
                "property_type, sold_date, created_at "
                "FROM sold_transactions "
                "ORDER BY sold_date DESC, created_at DESC LIMIT 25"
            ).fetchall()
            recent_sold = [dict(r) for r in rows]
        except Exception:
            recent_sold = []

        conn.close()

        self.write_json({
            "db": db_info,
            "tables": tables,
            "last_updated": last_updated,
            "stale": stale,
            "recent_price_changes": price_changes,
            "recent_sold": recent_sold,
            "generated_at": datetime.utcnow().isoformat(),
        })


DEFAULT_SCORE_BANDS = {
    "A": {"min": 60, "label": "Top ~10%"},
    "B": {"min": 45, "label": "Next ~25%"},
    "C": {"min": 30, "label": "Next ~40%"},
    "D": {"min":  0, "label": "Bottom ~25%"},
}


def _effective_score_bands():
    """Merge any saved override into the defaults. Always returns all 4 bands."""
    override = db.get_setting("score_bands") or {}
    out = {}
    for letter, cfg in DEFAULT_SCORE_BANDS.items():
        merged = dict(cfg)
        if isinstance(override.get(letter), dict) and "min" in override[letter]:
            try:
                merged["min"] = int(override[letter]["min"])
            except (TypeError, ValueError):
                pass
        out[letter] = merged
    return out


def _validate_score_bands(bands):
    """Ensure A_min > B_min > C_min >= 0, all integers in [0, 100]. Raises ValueError."""
    if not isinstance(bands, dict):
        raise ValueError("bands must be an object")
    mins = {}
    for letter in ("A", "B", "C"):
        cfg = bands.get(letter)
        if not isinstance(cfg, dict) or "min" not in cfg:
            raise ValueError(f"band {letter} must have a 'min'")
        try:
            v = int(cfg["min"])
        except (TypeError, ValueError):
            raise ValueError(f"band {letter}.min must be an integer")
        if v < 0 or v > 100:
            raise ValueError(f"band {letter}.min out of range [0,100]")
        mins[letter] = v
    if not (mins["A"] > mins["B"] > mins["C"] >= 0):
        raise ValueError(f"must satisfy A_min > B_min > C_min >= 0 (got {mins})")
    return {L: {"min": mins[L], "label": DEFAULT_SCORE_BANDS[L]["label"]} for L in ("A", "B", "C")}


class AdminTuningHandler(BaseHandler):
    """GET /api/admin/tuning — read-only snapshot of all scoring/tuning constants.

    Introspects the backend modules so the admin page never drifts from code.
    """

    def get(self):
        import region_profiles as rp
        import scoring_engine as se

        payload = {
            "score_bands": _effective_score_bands(),
            "rarity_weights": {
                "price_dev":      0.25,
                "typology":       0.15,
                "size_dev":       0.10,
                "condition":      0.20,
                "scarcity":       0.10,
                "prop_type":      0.05,
                "vs_sold":        0.10,
                "new_build_prox": 0.05,
            },
            "log1p_scale": {
                "k": se.LOG1P_K,
                "signals": sorted(list(se.LOG1P_SIGNALS)),
                "description": "Concave log1p transform applied to fat-tailed signals before weighting.",
            },
            "blocker_penalty_scale": se.BLOCKER_PENALTY_SCALE,
            "profiles": rp.PROFILES,
            "reno_cost_tiers": rp.RENO_COST_TIERS,
            "scraper_cooldown_sec": 3 * 60,
        }
        self.write_json(payload)


class ScoreBandsHandler(BaseHandler):
    """GET  /api/settings/score-bands — public, lightweight (frontend consumes this).
       PUT  /api/admin/tuning/score-bands — save override (admin).
       DELETE /api/admin/tuning/score-bands — reset to defaults (admin).
    """

    def get(self):
        self.write_json({"score_bands": _effective_score_bands()})

    def put(self):
        try:
            payload = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.write_error_json("invalid JSON body", 400)
            return
        bands = payload.get("score_bands") or payload
        try:
            cleaned = _validate_score_bands(bands)
        except ValueError as e:
            self.write_error_json(str(e), 400)
            return
        db.set_setting("score_bands", cleaned)
        self.write_json({"score_bands": _effective_score_bands()})

    def delete(self):
        db.delete_setting("score_bands")
        self.write_json({"score_bands": _effective_score_bands()})


class ReactionDetailHandler(BaseHandler):
    """PUT /api/reactions/:kind/:id   set like/dislike (+ optional comment)
       DELETE /api/reactions/:kind/:id  clear reaction"""

    def put(self, listing_kind, listing_id):
        try:
            payload = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.write_error_json("invalid JSON body", 400)
            return
        reaction = payload.get("reaction")
        comment = payload.get("comment")
        try:
            row = db.set_reaction(listing_kind, int(listing_id), reaction, comment)
        except ValueError as e:
            self.write_error_json(str(e), 400)
            return
        self.write_json(row)

    def delete(self, listing_kind, listing_id):
        if listing_kind not in ("sale", "rent"):
            self.write_error_json(f"invalid listing_kind: {listing_kind}", 400)
            return
        removed = db.delete_reaction(listing_kind, int(listing_id))
        self.write_json({"removed": removed})


class RatingsHandler(BaseHandler):
    """GET /api/ratings — admin-only per-persona score agreement.

    Query params:
      ?details=true   include joined listing columns
      ?persona=flip|rent   filter to one persona
      ?agree=agree|disagree   filter to one side
    """

    def get(self):
        details = self.get_argument("details", "false").lower() in ("1", "true", "yes")
        persona = self.get_argument("persona", None) or None
        agree = self.get_argument("agree", None) or None
        if persona and persona not in db.RATING_PERSONAS:
            self.write_error_json(f"invalid persona: {persona}", 400)
            return
        if agree and agree not in ("agree", "disagree"):
            self.write_error_json(f"invalid agree: {agree}", 400)
            return
        rows = (
            db.get_ratings_with_listings(persona=persona, agree=agree)
            if details else db.get_ratings(persona=persona, agree=agree)
        )
        self.write_json({"count": len(rows), "ratings": rows})


class RatingDetailHandler(BaseHandler):
    """PUT /api/ratings/:kind/:id/:persona   set agree/disagree (+ optional comment)
       DELETE /api/ratings/:kind/:id/:persona  clear rating"""

    def put(self, listing_kind, listing_id, persona):
        try:
            payload = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.write_error_json("invalid JSON body", 400)
            return
        agree = payload.get("agree")
        comment = payload.get("comment")
        try:
            row = db.set_rating(listing_kind, int(listing_id), persona, agree, comment)
        except ValueError as e:
            self.write_error_json(str(e), 400)
            return
        self.write_json(row)

    def delete(self, listing_kind, listing_id, persona):
        if persona not in db.RATING_PERSONAS:
            self.write_error_json(f"invalid persona: {persona}", 400)
            return
        removed = db.delete_rating(listing_kind, int(listing_id), persona)
        self.write_json({"removed": removed})


class NeighbourhoodTypologiesHandler(BaseHandler):
    """GET /api/neighbourhood-typologies"""

    def get(self):
        typologies = db.get_neighbourhood_typologies()
        self.write_json({"typologies": typologies})


def _point_in_polygon(lat, lon, polygon_coords):
    """Ray-casting point-in-polygon test. coords = list of [lon, lat] rings."""
    ring = polygon_coords[0]  # outer ring
    n = len(ring)
    inside = False
    px, py = lon, lat
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _find_parish(lat, lon, features):
    """Find which parish feature contains the point."""
    for f in features:
        geom = f.get("geometry", {})
        gtype = geom.get("type", "")
        coords = geom.get("coordinates", [])
        if gtype == "Polygon":
            if _point_in_polygon(lat, lon, coords):
                return f
        elif gtype == "MultiPolygon":
            for poly in coords:
                if _point_in_polygon(lat, lon, poly):
                    return f
    return None


class NearbyProjectsHandler(BaseHandler):
    """GET /api/nearby-projects?lat=...&lon=...&radius_m=500 — count construction projects near a point"""

    def get(self):
        import math
        lat = self.get_float_arg("lat")
        lon = self.get_float_arg("lon")
        radius_m = self.get_int_arg("radius_m", 500)
        if lat is None or lon is None:
            self.write_error_json("lat and lon required", 400)
            return

        # Approximate degree offset for bounding box
        dlat = radius_m / 111_320
        dlon = radius_m / (111_320 * math.cos(math.radians(lat)))

        conn = db.get_connection()
        rows = conn.execute(
            """SELECT source_id, layer, operation, subject, address,
                      centroid_lat, centroid_lon
               FROM construction_projects
               WHERE centroid_lat BETWEEN ? AND ?
                 AND centroid_lon BETWEEN ? AND ?""",
            (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
        ).fetchall()
        conn.close()

        # Haversine filter for exact radius
        projects = []
        for r in rows:
            plat, plon = r["centroid_lat"], r["centroid_lon"]
            if plat is None or plon is None:
                continue
            a = math.sin(math.radians(plat - lat) / 2) ** 2 + \
                math.cos(math.radians(lat)) * math.cos(math.radians(plat)) * \
                math.sin(math.radians(plon - lon) / 2) ** 2
            d = 6_371_000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            if d <= radius_m:
                projects.append({
                    "layer": r["layer"],
                    "operation": r["operation"],
                    "subject": r["subject"],
                    "address": r["address"],
                    "distance_m": round(d),
                })

        # Sort by distance
        projects.sort(key=lambda p: p["distance_m"])

        # Summary counts
        issued = sum(1 for p in projects if p["layer"] == "issued")
        pending = sum(1 for p in projects if p["layer"] == "pending")

        self.write_json({
            "count": len(projects),
            "issued": issued,
            "pending": pending,
            "projects": projects[:20],  # Top 20 closest
        })


class ParishStatsHandler(BaseHandler):
    """GET /api/parish-stats — per-parish statistics computed via point-in-polygon"""

    _cache = {}

    def get(self):
        import statistics
        area = self.get_argument("area", "aml")
        cache_key = area

        # Use cache if available (recompute on restart)
        if cache_key in ParishStatsHandler._cache:
            self.write_json(ParishStatsHandler._cache[cache_key])
            return

        # Load parish GeoJSON
        area_cfg = AREAS.get(area, AREAS.get("lisbon", {}))
        geojson_file = area_cfg.get("parishes_geojson")
        if not geojson_file:
            self.write_json({"stats": {}})
            return
        geojson_path = Path(__file__).parent / "data" / geojson_file
        if not geojson_path.exists():
            self.write_json({"stats": {}})
            return
        fc = json.loads(geojson_path.read_text(encoding="utf-8"))
        features = fc.get("features", [])

        # Get listings with coords
        listings = db.get_active_sales_with_coords()

        # Assign each listing to a parish
        parish_listings = {}  # parish_name -> list of listings
        for ls in listings:
            f = _find_parish(ls["lat"], ls["lon"], features)
            if f:
                name = f.get("properties", {}).get("Freguesia") or f.get("properties", {}).get("name", "")
                if name:
                    parish_listings.setdefault(name, []).append(ls)

        # Compute stats per parish
        result = {}
        for name, pls in parish_listings.items():
            prices_sqm = [l["price_per_sqm"] for l in pls if l.get("price_per_sqm")]
            prices = [l["price_amount"] for l in pls if l.get("price_amount")]
            rooms_list = [l["rooms"] for l in pls if l.get("rooms") is not None]
            sizes = [l["size_sqm"] for l in pls if l.get("size_sqm")]

            # Most common room count
            most_common_rooms = None
            rooms_dist = {}
            for r in rooms_list:
                rooms_dist[r] = rooms_dist.get(r, 0) + 1
            if rooms_dist:
                most_common_rooms = max(rooms_dist, key=rooms_dist.get)

            result[name] = {
                "listing_count": len(pls),
                "avg_price": round(statistics.mean(prices), 0) if prices else None,
                "median_price": round(statistics.median(prices), 0) if prices else None,
                "avg_price_per_sqm": round(statistics.mean(prices_sqm), 0) if prices_sqm else None,
                "median_price_per_sqm": round(statistics.median(prices_sqm), 0) if prices_sqm else None,
                "min_price_per_sqm": round(min(prices_sqm), 0) if prices_sqm else None,
                "max_price_per_sqm": round(max(prices_sqm), 0) if prices_sqm else None,
                "avg_size": round(statistics.mean(sizes), 0) if sizes else None,
                "most_common_rooms": most_common_rooms,
                "rooms_distribution": {str(k): v for k, v in sorted(rooms_dist.items())},
            }

        ParishStatsHandler._cache[cache_key] = {"stats": result}
        self.write_json({"stats": result})


class AddressLookupHandler(BaseHandler):
    """POST /api/address-lookup — find or scrape listings near an address"""

    async def post(self):
        try:
            body = json.loads(self.request.body or "{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.write_json({"error": "Invalid JSON"})
            return

        address = body.get("address", "").strip()
        lat = body.get("lat")
        lon = body.get("lon")
        listing_type = body.get("listing_type", "sale")
        radius_m = int(body.get("radius_m", 100))

        if not address or lat is None or lon is None:
            self.set_status(400)
            self.write_json({"error": "address, lat, lon are required"})
            return

        try:
            lat = float(lat)
            lon = float(lon)
        except (ValueError, TypeError):
            self.set_status(400)
            self.write_json({"error": "lat/lon must be numbers"})
            return

        # Step 1: check DB for nearby listings
        nearby = await tornado.ioloop.IOLoop.current().run_in_executor(
            _executor, db.find_nearby_listings, lat, lon, radius_m, address, listing_type
        )
        if nearby:
            # Compute deal scores for each found listing
            deal_scores = {}
            for item in nearby:
                if item["listing_type"] == "sale":
                    try:
                        ds = await tornado.ioloop.IOLoop.current().run_in_executor(
                            _executor, db.compute_deal_score, item["id"], "sale", 500
                        )
                        deal_scores[item["id"]] = ds
                    except Exception:
                        pass
            self.write_json({
                "source": "database",
                "listings": nearby,
                "deal_scores": deal_scores,
            })
            return

        # Step 2: search Idealista
        if not _address_lookup_lock.acquire(blocking=False):
            self.set_status(429)
            self.write_json({"error": "Another address lookup is in progress. Please try again in a moment."})
            return

        try:
            from scrapers.idealista_address_lookup import search_idealista_by_address
            listings = await tornado.ioloop.IOLoop.current().run_in_executor(
                _executor,
                search_idealista_by_address,
                address, lat, lon, listing_type, True, 5, 300,
            )

            if not listings:
                self.write_json({
                    "source": "idealista",
                    "listings": [],
                    "deal_scores": {},
                })
                return

            # Upsert scraped listings
            inserted = []
            for listing in listings:
                try:
                    lid, is_new = db.upsert_listing(listing)
                    lt = listing.listing_type or listing_type
                    inserted.append({"id": lid, "listing_type": lt, "is_new": is_new})
                except Exception as e:
                    logger.warning("Failed to upsert listing: %s", e)

            # Recompute rarity scores for new sales listings
            has_new_sales = any(
                item["is_new"] and item["listing_type"] == "sale" for item in inserted
            )
            if has_new_sales:
                try:
                    await tornado.ioloop.IOLoop.current().run_in_executor(
                        _executor, db.compute_rarity_scores
                    )
                except Exception as e:
                    logger.warning("Failed to recompute rarity scores: %s", e)

            # Re-query the DB for the now-inserted listings with full columns
            result_listings = await tornado.ioloop.IOLoop.current().run_in_executor(
                _executor, db.find_nearby_listings, lat, lon, 300, address, None
            )

            deal_scores = {}
            for item in inserted:
                if item["listing_type"] == "sale":
                    try:
                        ds = await tornado.ioloop.IOLoop.current().run_in_executor(
                            _executor, db.compute_deal_score, item["id"], "sale", 500
                        )
                        deal_scores[item["id"]] = ds
                    except Exception:
                        pass

            self.write_json({
                "source": "idealista",
                "listings": result_listings,
                "deal_scores": deal_scores,
            })
        except Exception as e:
            logger.exception("Address lookup scrape failed: %s", e)
            self.set_status(500)
            self.write_json({"error": "Scrape failed: {}".format(str(e))})
        finally:
            _address_lookup_lock.release()


# ── App setup ─────────────────────────────────────────────────────────────────

class FlipRentPreviewHandler(BaseHandler):  # noqa
    """GET /api/listings/:id/score-preview

    Re-roll flip & rent scores for a listing with some signals disabled and/or
    a reno-cost override. Does NOT write to the DB — used by the UI toggle
    panel and reno-cost slider.

    Query params:
        listing_type = sale | rent   (default: auto)
        disable      = comma-separated signal keys
        reno_cost_per_sqm = float    (€/m² override)
        reno_tier    = cosmetic | mid | gut
    """

    def get(self, listing_id: str):
        import json as _json
        from scoring_engine import SignalBundle, compute_both
        from region_profiles import estimate_reno_cost

        listing_type = self.get_argument("listing_type", None)
        disable_raw = self.get_argument("disable", "")
        disabled = {s.strip() for s in disable_raw.split(",") if s.strip()}
        reno_cost_per_sqm = self.get_float_arg("reno_cost_per_sqm")
        reno_tier = self.get_argument("reno_tier", None) or None

        # Find the listing row in sales or rentals.
        conn = db.get_connection()
        row = None
        table_used = None
        candidates = (
            ("sales",) if listing_type == "sale" else
            ("rentals",) if listing_type == "rent" else
            ("sales", "rentals")
        )
        for tbl in candidates:
            r = conn.execute(
                f"SELECT * FROM {tbl} WHERE id=?", (listing_id,)
            ).fetchone()
            if r:
                row = dict(r); table_used = tbl; break

        if not row:
            conn.close()
            self.write_error_json("Listing not found", 404)
            return

        profile = row.get("region_profile") or "urban_dense"
        flip_factors_raw = row.get("flip_factors")
        if not flip_factors_raw:
            conn.close()
            self.write_error_json(
                "Listing has no scored signals yet (run signal_batch.py)", 400,
            )
            return

        try:
            flip_factors = _json.loads(flip_factors_raw)
        except _json.JSONDecodeError:
            conn.close()
            self.write_error_json("Corrupt flip_factors JSON", 500); return

        bundle_json = flip_factors.get("bundle") or {}
        positives = bundle_json.get("positives") or {}
        blockers = bundle_json.get("blockers") or {}

        bundle = SignalBundle(
            positives={k: (float(v) if v is not None else None)
                       for k, v in positives.items()},
            blockers={k: (float(v) if v is not None else None)
                      for k, v in blockers.items()},
            comparables_count=bundle_json.get("comparables_count") or 0,
            market_median_eur_sqm=bundle_json.get("market_median_eur_sqm"),
            expected_resale_eur_sqm=bundle_json.get("expected_resale_eur_sqm"),
            expected_rent_eur_sqm=bundle_json.get("expected_rent_eur_sqm"),
            notes=bundle_json.get("notes") or {},
        )

        # Reno-cost override: recomputes estimate AND market_discount/resale
        # derived signals since they depend on reno_cost.
        override_reno = estimate_reno_cost(
            size_sqm=row.get("size_sqm"),
            condition=row.get("condition"),
            profile_name=profile,
            tier_override=reno_tier,
            cost_per_sqm_override=reno_cost_per_sqm,
        ) if (reno_cost_per_sqm is not None or reno_tier is not None) else None

        if override_reno is not None and bundle.expected_resale_eur_sqm and row.get("size_sqm"):
            from scoring_engine import compute_expected_resale_signal
            new_resale = compute_expected_resale_signal(
                row.get("price_amount"), row.get("size_sqm"),
                bundle.expected_resale_eur_sqm, override_reno,
            )
            bundle.positives["expected_resale"] = new_resale

        results = compute_both(bundle, profile, disabled_signals=disabled)

        self.write_json({
            "listing_id": row["id"],
            "listing_type": "rent" if table_used == "rentals" else "sale",
            "profile": profile,
            "disabled": sorted(disabled),
            "reno_cost_estimate": override_reno if override_reno is not None else row.get("reno_cost_estimate"),
            "reno_cost_per_sqm_override": reno_cost_per_sqm,
            "reno_tier_override": reno_tier,
            "flip": results["flip"].to_dict(),
            "rent": results["rent"].to_dict(),
            "bundle": {
                "positives": bundle.positives,
                "blockers": bundle.blockers,
                "comparables_count": bundle.comparables_count,
                "market_median_eur_sqm": bundle.market_median_eur_sqm,
                "expected_resale_eur_sqm": bundle.expected_resale_eur_sqm,
                "expected_rent_eur_sqm": bundle.expected_rent_eur_sqm,
            },
        })
        conn.close()


class ExtractImageTagsHandler(BaseHandler):
    """POST /api/extract-image-tags  (multipart: file=<image>)

    Returns structured style tags extracted by Claude vision.
    Used by the onboarding wizard to auto-fill preference chips from a
    user-supplied reference image (mood board, magazine clip, etc.).
    """

    MAX_BYTES = 6 * 1024 * 1024  # 6 MB cap

    async def post(self):
        files = self.request.files.get("file") or []
        if not files:
            self.write_error_json("missing 'file' upload", 400); return
        f = files[0]
        if len(f.body) > self.MAX_BYTES:
            self.write_error_json(
                f"image too large (>{self.MAX_BYTES // (1024*1024)} MB)", 413
            ); return

        ctype = (f.content_type or "image/jpeg").split(";")[0].strip().lower()
        # Lazy import — keeps module load light when feature unused.
        from scorers.style_extractor import extract_style_tags
        result = extract_style_tags(f.body, media_type=ctype)
        if result.error:
            status = 502 if result.error.startswith("api_error") else 400
            self.write_error_json(result.error, status); return
        self.write_json(result.to_dict())


class PreferenceDeckHandler(BaseHandler):
    """GET /api/preference-deck

    Returns a stratified list of listings for the onboarding swipe deck.
    Persona drives both the sale/rent table choice and the axes used to
    maximise variety across consecutive cards.
    """

    MAX_N = 20
    DEFAULT_N = 12

    def get(self):
        from preference_deck import build_deck
        n = self.get_int_arg("n", self.DEFAULT_N) or self.DEFAULT_N
        n = max(4, min(n, self.MAX_N))
        items = build_deck(
            persona=self.get_argument("persona", None),
            n=n,
            city=self.get_argument("city", None),
            parish=self.get_argument("parish", None),
            min_price=self.get_float_arg("min_price"),
            max_price=self.get_float_arg("max_price"),
            min_sqm=self.get_float_arg("min_sqm"),
            max_sqm=self.get_float_arg("max_sqm"),
            seed=self.get_int_arg("seed"),
        )
        self.write_json({"count": len(items), "items": items})


class ExtractPreferencesHandler(BaseHandler):
    """POST /api/extract-preferences  body: {message: str, current_prefs?: dict}

    Returns only the preference fields the user explicitly mentioned, plus a
    one-line summary echo. The frontend shallow-merges these into the existing
    wizard state so the user can keep refining.
    """

    def post(self):
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.write_error_json("invalid JSON body", 400); return

        message = (body.get("message") or "").strip()
        if not message:
            self.write_error_json("missing 'message'", 400); return

        from scorers.preference_chat import extract_preferences
        result = extract_preferences(message, current_prefs=body.get("current_prefs"))
        if result.error:
            status = 502 if result.error.startswith("api_error") else 400
            self.write_error_json(result.error, status); return
        self.write_json(result.to_dict())


def make_app() -> tornado.web.Application:
    return tornado.web.Application(
        [
            (r"/api/compare-listings",       CompareListingsHandler),
            (r"/api/listings",              ListingsHandler),
            (r"/api/extract-image-tags",    ExtractImageTagsHandler),
            (r"/api/extract-preferences",   ExtractPreferencesHandler),
            (r"/api/preference-deck",       PreferenceDeckHandler),
            (r"/api/listings/(\d+)/score-preview",   FlipRentPreviewHandler),
            (r"/api/listings/(\d+)/compare",         ComparisonHandler),
            (r"/api/listings/(\d+)/address-history",  AddressHistoryHandler),
            (r"/api/listings/(\d+)",        ListingDetailHandler),
            (r"/api/neighborhoods",         NeighborhoodsHandler),
            (r"/api/neighborhoods/(.+)",    NeighborhoodDetailHandler),
            (r"/api/scrape-runs",           ScrapeRunsHandler),
            (r"/api/scrape",                ScrapeHandler),
            (r"/api/score",                 ScoreHandler),
            (r"/api/projects",              ProjectsHandler),
            (r"/api/security",               SecurityHandler),
            (r"/api/ine-stats",             IneStatsHandler),
            (r"/api/stats",                 StatsHandler),
            (r"/api/sold-trends",           SoldTrendsHandler),
            (r"/api/amenity-rating",        AmenityRatingHandler),
            (r"/api/areas",                 AreasHandler),
            (r"/api/parishes",              ParishesHandler),
            (r"/api/translate",              TranslateHandler),
            (r"/api/neighbourhood-typologies", NeighbourhoodTypologiesHandler),
            (r"/api/parish-stats",          ParishStatsHandler),
            (r"/api/nearby-projects",       NearbyProjectsHandler),
            (r"/api/address-lookup",        AddressLookupHandler),
            (r"/api/reactions",             ReactionsHandler),
            (r"/api/reactions/(sale|rent)/(\d+)", ReactionDetailHandler),
            (r"/api/ratings",               RatingsHandler),
            (r"/api/ratings/(sale|rent)/(\d+)/(flip|rent)", RatingDetailHandler),
            (r"/api/admin/tuning",          AdminTuningHandler),
            (r"/api/admin/health",          AdminHealthHandler),
            (r"/api/admin/tuning/score-bands", ScoreBandsHandler),
            (r"/api/settings/score-bands",  ScoreBandsHandler),
        ],
        debug=False,
    )


if __name__ == "__main__":
    db.init_db()
    app = make_app()
    app.listen(PORT)
    logger.info(f"API server running on http://localhost:{PORT}")
    logger.info("Endpoints: /api/listings  /api/neighborhoods  /api/scrape  /api/stats")
    tornado.ioloop.IOLoop.current().start()
