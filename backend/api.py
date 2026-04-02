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
from scrapers.idealista import IdealistaScraper
from models import ScrapeRun
from chat_handler import ChatHandler

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8000))
_executor = ThreadPoolExecutor(max_workers=4)

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

        avg_price = conn.execute(
            "SELECT AVG(price_amount) as avg FROM sales WHERE price_amount IS NOT NULL"
        ).fetchone()["avg"]
        avg_psqm = conn.execute(
            "SELECT AVG(price_per_sqm) as avg FROM sales WHERE price_per_sqm IS NOT NULL"
        ).fetchone()["avg"]
        avg_rent = conn.execute(
            "SELECT AVG(price_amount) as avg FROM rentals WHERE price_amount IS NOT NULL"
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
            "sales_count": sales_count,
            "rentals_count": rentals_count,
            "by_source": by_source,
            "avg_price_eur": round(avg_price, 2) if avg_price else None,
            "avg_price_per_sqm": round(avg_psqm, 2) if avg_psqm else None,
            "avg_rent_eur": round(avg_rent, 2) if avg_rent else None,
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


class AddressHistoryHandler(BaseHandler):
    """GET /api/listings/:id/address-history"""

    def get(self, listing_id):
        listing_type = self.get_argument("listing_type", "sale")
        matches = db.get_address_matches(int(listing_id), listing_type)
        self.write_json({"count": len(matches), "matches": matches})


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


# ── App setup ─────────────────────────────────────────────────────────────────

def make_app() -> tornado.web.Application:
    return tornado.web.Application(
        [
            (r"/api/listings",              ListingsHandler),
            (r"/api/listings/(\d+)/compare",         ComparisonHandler),
            (r"/api/listings/(\d+)/address-history",  AddressHistoryHandler),
            (r"/api/listings/(\d+)",        ListingDetailHandler),
            (r"/api/neighborhoods",         NeighborhoodsHandler),
            (r"/api/neighborhoods/(.+)",    NeighborhoodDetailHandler),
            (r"/api/scrape-runs",           ScrapeRunsHandler),
            (r"/api/scrape",                ScrapeHandler),
            (r"/api/projects",              ProjectsHandler),
            (r"/api/security",               SecurityHandler),
            (r"/api/ine-stats",             IneStatsHandler),
            (r"/api/stats",                 StatsHandler),
            (r"/api/sold-trends",           SoldTrendsHandler),
            (r"/api/amenity-rating",        AmenityRatingHandler),
            (r"/api/areas",                 AreasHandler),
            (r"/api/parishes",              ParishesHandler),
            (r"/api/translate",              TranslateHandler),
            (r"/api/chat",                  ChatHandler),
            (r"/api/neighbourhood-typologies", NeighbourhoodTypologiesHandler),
            (r"/api/parish-stats",          ParishStatsHandler),
            (r"/api/nearby-projects",       NearbyProjectsHandler),
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
