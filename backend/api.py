"""
REST API server for the Lisbon Real Estate app.
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
import threading
import sys
from pathlib import Path

import tornado.ioloop
import tornado.web

# Make sure the backend directory is on the path
sys.path.insert(0, str(Path(__file__).parent))

import database as db
from scrapers.idealista import IdealistaScraper
from models import ScrapeRun

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PORT = 8000


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
            limit=self.get_int_arg("limit", 500),
            offset=self.get_int_arg("offset", 0),
        )
        self.write_json({"count": len(listings), "listings": listings})


class ListingDetailHandler(BaseHandler):
    """GET /api/listings/:id"""

    def get(self, listing_id: str):
        conn = db.get_connection()
        row = conn.execute(
            "SELECT * FROM listings WHERE id=?", (listing_id,)
        ).fetchone()
        conn.close()
        if not row:
            self.write_error_json("Listing not found", 404)
            return
        self.write_json(dict(row))


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

        if source != "idealista":
            self.write_error_json(f"Unknown source '{source}'. Only 'idealista' is supported so far.")
            return

        run_id = db.start_scrape_run(source)

        # Fire off in a daemon thread so the HTTP response returns immediately
        thread = threading.Thread(
            target=_run_scrape,
            args=(source, run_id, max_pages),
            daemon=True,
        )
        thread.start()

        self.write_json({
            "message": f"Scrape started for '{source}' (run_id={run_id})",
            "run_id": run_id,
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


class ParishesHandler(BaseHandler):
    """GET /api/parishes — official Lisboa parish boundaries (GeoJSON FeatureCollection)"""

    _cache = None

    def get(self):
        if ParishesHandler._cache is None:
            geojson_path = Path(__file__).parent / "data" / "lisbon_parishes.geojson"
            if not geojson_path.exists():
                self.write_error_json("Parish boundaries file not found. Run scrapers/cml_parishes.py first.", 404)
                return
            ParishesHandler._cache = json.loads(geojson_path.read_text(encoding="utf-8"))
        self.write_json(ParishesHandler._cache)


class StatsHandler(BaseHandler):
    """GET /api/stats — overall summary"""

    def get(self):
        conn = db.get_connection()

        total = conn.execute("SELECT COUNT(*) as c FROM listings").fetchone()["c"]
        by_source = conn.execute(
            "SELECT source, COUNT(*) as count FROM listings GROUP BY source"
        ).fetchall()
        avg_price = conn.execute(
            "SELECT AVG(price_amount) as avg FROM listings WHERE price_amount IS NOT NULL"
        ).fetchone()["avg"]
        avg_psqm = conn.execute(
            "SELECT AVG(price_per_sqm) as avg FROM listings WHERE price_per_sqm IS NOT NULL"
        ).fetchone()["avg"]
        neighborhoods = conn.execute(
            "SELECT COUNT(*) as c FROM neighborhoods"
        ).fetchone()["c"]
        last_scrape = conn.execute(
            "SELECT MAX(started_at) as ts FROM scrape_runs WHERE status='completed'"
        ).fetchone()["ts"]
        conn.close()

        self.write_json({
            "total_listings": total,
            "by_source": {r["source"]: r["count"] for r in by_source},
            "avg_price_eur": round(avg_price, 2) if avg_price else None,
            "avg_price_per_sqm": round(avg_psqm, 2) if avg_psqm else None,
            "neighborhood_count": neighborhoods,
            "last_scrape": last_scrape,
        })


# ── Background scrape logic ───────────────────────────────────────────────────

def _run_scrape(source: str, run_id: int, max_pages: int):
    """Runs in a background thread."""
    try:
        scraper = IdealistaScraper(max_pages=max_pages)
        listings = scraper.scrape()

        new_count = updated_count = 0
        for listing in listings:
            _, is_new = db.upsert_listing(listing)
            if is_new:
                new_count += 1
            else:
                updated_count += 1

        db.rebuild_neighborhoods()

        run = ScrapeRun(
            source=source,
            listings_found=len(listings),
            listings_new=new_count,
            listings_updated=updated_count,
            status="completed",
        )
        db.finish_scrape_run(run_id, run)
        logger.info(
            f"[scrape] Run {run_id} complete: "
            f"{len(listings)} found, {new_count} new, {updated_count} updated"
        )
    except Exception as e:
        run = ScrapeRun(
            source=source,
            status="failed",
            notes=str(e),
        )
        db.finish_scrape_run(run_id, run)
        logger.error(f"[scrape] Run {run_id} failed: {e}")


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


# ── App setup ─────────────────────────────────────────────────────────────────

def make_app() -> tornado.web.Application:
    return tornado.web.Application(
        [
            (r"/api/listings",              ListingsHandler),
            (r"/api/listings/(\d+)",        ListingDetailHandler),
            (r"/api/neighborhoods",         NeighborhoodsHandler),
            (r"/api/neighborhoods/(.+)",    NeighborhoodDetailHandler),
            (r"/api/scrape-runs",           ScrapeRunsHandler),
            (r"/api/scrape",                ScrapeHandler),
            (r"/api/projects",              ProjectsHandler),
            (r"/api/security",               SecurityHandler),
            (r"/api/ine-stats",             IneStatsHandler),
            (r"/api/stats",                 StatsHandler),
            (r"/api/parishes",              ParishesHandler),
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
