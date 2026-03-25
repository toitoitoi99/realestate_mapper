"""
SQLite database layer for the Lisbon Real Estate app.
Handles schema creation and all CRUD operations.
"""

import json
import math
import sqlite3
import logging
import statistics
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from models import Listing, Neighborhood, ScrapeRun

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "lisbon_realestate.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS listings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source          TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                url             TEXT NOT NULL,
                price_amount    REAL,
                price_per_sqm   REAL,
                size_sqm        REAL,
                rooms           INTEGER,
                bedrooms        INTEGER,
                bathrooms       INTEGER,
                floor           TEXT,
                property_type   TEXT,
                condition       TEXT,
                title           TEXT,
                address         TEXT,
                postal_code     TEXT,
                neighborhood    TEXT,
                parish          TEXT,
                district        TEXT,
                city            TEXT,
                lat             REAL,
                lon             REAL,
                images          TEXT,
                hash_dedupe     TEXT,
                description     TEXT,
                scraped_at      TEXT NOT NULL,
                UNIQUE(source, source_id)
            );

            CREATE INDEX IF NOT EXISTS idx_listings_neighborhood
                ON listings(neighborhood);
            CREATE INDEX IF NOT EXISTS idx_listings_source
                ON listings(source);
            CREATE INDEX IF NOT EXISTS idx_listings_price_sqm
                ON listings(price_per_sqm);
            CREATE INDEX IF NOT EXISTS idx_listings_hash
                ON listings(hash_dedupe);
            CREATE INDEX IF NOT EXISTS idx_listings_latlon
                ON listings(lat, lon);

            CREATE TABLE IF NOT EXISTS neighborhoods (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                name                    TEXT NOT NULL,
                district                TEXT NOT NULL,
                listing_count           INTEGER DEFAULT 0,
                avg_price               REAL,
                median_price            REAL,
                avg_price_per_sqm       REAL,
                median_price_per_sqm    REAL,
                min_price_per_sqm       REAL,
                max_price_per_sqm       REAL,
                geometry                TEXT,
                updated_at              TEXT NOT NULL,
                UNIQUE(name, district)
            );

            CREATE TABLE IF NOT EXISTS scrape_runs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                source              TEXT NOT NULL,
                started_at          TEXT NOT NULL,
                finished_at         TEXT,
                listings_found      INTEGER DEFAULT 0,
                listings_new        INTEGER DEFAULT 0,
                listings_updated    INTEGER DEFAULT 0,
                errors              INTEGER DEFAULT 0,
                status              TEXT DEFAULT 'running',
                notes               TEXT
            );

            CREATE TABLE IF NOT EXISTS construction_projects (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id       TEXT NOT NULL,          -- N_PROCESSO
                layer           TEXT NOT NULL,          -- 'permit' | 'application'
                address         TEXT,                   -- MORADA
                parish          TEXT,                   -- FREGUESIA
                operation       TEXT,                   -- OP_URBANISTICA
                subject         TEXT,                   -- ASSUNTO
                procedure       TEXT,                   -- PROCEDIMENTO
                typology        TEXT,                   -- TIPOLOGIA
                date_submitted  TEXT,                   -- DATA_ENTRADA (epoch ms → ISO)
                permit_number   TEXT,                   -- N_ALVARA (permits only)
                date_permit     TEXT,                   -- DATA_ALVARA (permits only)
                permit_type     TEXT,                   -- TIPO_ALVARA (permits only)
                geometry        TEXT,                   -- GeoJSON geometry string
                centroid_lat    REAL,
                centroid_lon    REAL,
                fetched_at      TEXT NOT NULL,
                UNIQUE(source_id, layer)
            );

            CREATE INDEX IF NOT EXISTS idx_projects_layer
                ON construction_projects(layer);
            CREATE INDEX IF NOT EXISTS idx_projects_parish
                ON construction_projects(parish);
            CREATE INDEX IF NOT EXISTS idx_projects_latlon
                ON construction_projects(centroid_lat, centroid_lon);

            CREATE INDEX IF NOT EXISTS idx_neighborhoods_district
                ON neighborhoods(district);
            CREATE INDEX IF NOT EXISTS idx_scrape_runs_status
                ON scrape_runs(status);
            CREATE INDEX IF NOT EXISTS idx_scrape_runs_source
                ON scrape_runs(source);
        """)

    # security_pois table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS security_pois (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id   TEXT NOT NULL,
            layer       TEXT NOT NULL,
            name        TEXT,
            address     TEXT,
            parish      TEXT,
            phone       TEXT,
            lat         REAL NOT NULL,
            lon         REAL NOT NULL,
            fetched_at  TEXT NOT NULL,
            UNIQUE(source_id, layer)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_security_pois_layer
            ON security_pois(layer)
    """)
    conn.commit()

    # ine_stats table (not in original executescript to keep migrations additive)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ine_stats (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            period_label         TEXT NOT NULL,
            geocod               TEXT NOT NULL,
            geodsg               TEXT NOT NULL,
            category             TEXT NOT NULL,
            category_label       TEXT NOT NULL,
            median_price_per_sqm REAL,
            fetched_at           TEXT NOT NULL,
            is_latest            INTEGER DEFAULT 0,
            UNIQUE(period_label, geocod, category)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ine_stats_geocod
            ON ine_stats(geocod, category)
    """)
    conn.commit()

    # Additive column migrations for existing databases
    for col in [
        "ALTER TABLE listings ADD COLUMN listing_type TEXT NOT NULL DEFAULT 'sale'",
        "ALTER TABLE listings ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
        "ALTER TABLE neighborhoods ADD COLUMN avg_rent_per_sqm REAL",
        "ALTER TABLE neighborhoods ADD COLUMN median_rent_per_sqm REAL",
        "ALTER TABLE neighborhoods ADD COLUMN avg_sold_price_per_sqm REAL",
        "ALTER TABLE neighborhoods ADD COLUMN count_sold INTEGER DEFAULT 0",
        "ALTER TABLE listings ADD COLUMN rarity_score REAL",
        "ALTER TABLE listings ADD COLUMN rarity_factors TEXT",
    ]:
        try:
            conn.execute(col); conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    conn.close()
    logger.info(f"[DB] Initialised at {DB_PATH}")


# ── Listings ─────────────────────────────────────────────────────────────────

def upsert_listing(listing: Listing) -> tuple[int, bool]:
    """
    Insert or update a listing. Returns (id, is_new).
    Natural key: (source, source_id).
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM listings WHERE source=? AND source_id=?",
            (listing.source, listing.source_id)
        ).fetchone()

        ts = listing.scraped_at.isoformat() if listing.scraped_at else datetime.utcnow().isoformat()

        if row:
            with conn:
                conn.execute("""
                    UPDATE listings SET
                        url=?, listing_type=?, status=?, price_amount=?, price_per_sqm=?,
                        size_sqm=?, rooms=?, bedrooms=?, bathrooms=?, floor=?,
                        property_type=?, condition=?, title=?, address=?, postal_code=?,
                        neighborhood=?, parish=?, district=?, city=?, lat=?, lon=?,
                        images=?, hash_dedupe=?, description=?, scraped_at=?
                    WHERE source=? AND source_id=?
                """, (
                    listing.url, listing.listing_type, listing.status,
                    listing.price_amount, listing.price_per_sqm,
                    listing.size_sqm, listing.rooms, listing.bedrooms,
                    listing.bathrooms, listing.floor, listing.property_type,
                    listing.condition, listing.title, listing.address,
                    listing.postal_code, listing.neighborhood, listing.parish,
                    listing.district, listing.city, listing.lat, listing.lon,
                    listing.images, listing.hash_dedupe, listing.description,
                    ts, listing.source, listing.source_id
                ))
            return row["id"], False
        else:
            with conn:
                cur = conn.execute("""
                    INSERT INTO listings (
                        source, source_id, url, listing_type, status,
                        price_amount, price_per_sqm, size_sqm, rooms, bedrooms,
                        bathrooms, floor, property_type, condition, title,
                        address, postal_code, neighborhood, parish, district,
                        city, lat, lon, images, hash_dedupe, description, scraped_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    listing.source, listing.source_id, listing.url,
                    listing.listing_type, listing.status,
                    listing.price_amount, listing.price_per_sqm,
                    listing.size_sqm, listing.rooms, listing.bedrooms,
                    listing.bathrooms, listing.floor, listing.property_type,
                    listing.condition, listing.title, listing.address,
                    listing.postal_code, listing.neighborhood, listing.parish,
                    listing.district, listing.city, listing.lat, listing.lon,
                    listing.images, listing.hash_dedupe, listing.description, ts
                ))
            return cur.lastrowid, True
    finally:
        conn.close()


def get_listings(
    neighborhood: Optional[str] = None,
    source: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_sqm: Optional[float] = None,
    max_sqm: Optional[float] = None,
    rooms: Optional[int] = None,
    has_coords: Optional[bool] = None,
    listing_type: Optional[str] = None,
    sold_after: Optional[str] = None,
    sold_before: Optional[str] = None,
    min_price_per_sqm: Optional[float] = None,
    max_price_per_sqm: Optional[float] = None,
    bedrooms: Optional[int] = None,
    bathrooms: Optional[int] = None,
    floor: Optional[str] = None,
    property_type: Optional[str] = None,
    condition: Optional[str] = None,
    parish: Optional[str] = None,
    district: Optional[str] = None,
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
) -> List[dict]:
    conn = get_connection()
    clauses, params = [], []

    if neighborhood:
        clauses.append("neighborhood=?"); params.append(neighborhood)
    if source:
        clauses.append("source=?"); params.append(source)
    if min_price is not None:
        clauses.append("price_amount>=?"); params.append(min_price)
    if max_price is not None:
        clauses.append("price_amount<=?"); params.append(max_price)
    if min_sqm is not None:
        clauses.append("size_sqm>=?"); params.append(min_sqm)
    if max_sqm is not None:
        clauses.append("size_sqm<=?"); params.append(max_sqm)
    if rooms is not None:
        clauses.append("rooms=?"); params.append(rooms)
    if has_coords:
        clauses.append("lat IS NOT NULL AND lon IS NOT NULL")
    if listing_type is not None:
        clauses.append("COALESCE(listing_type, 'sale')=?"); params.append(listing_type)
    if sold_after:
        clauses.append("status IN ('sold','reserved') AND scraped_at >= ?"); params.append(sold_after)
    if sold_before:
        clauses.append("status IN ('sold','reserved') AND scraped_at <= ?"); params.append(sold_before + "T23:59:59")
    if min_price_per_sqm is not None:
        clauses.append("price_per_sqm>=?"); params.append(min_price_per_sqm)
    if max_price_per_sqm is not None:
        clauses.append("price_per_sqm<=?"); params.append(max_price_per_sqm)
    if bedrooms is not None:
        clauses.append("bedrooms>=?" if bedrooms >= 5 else "bedrooms=?"); params.append(bedrooms)
    if bathrooms is not None:
        clauses.append("bathrooms>=?" if bathrooms >= 4 else "bathrooms=?"); params.append(bathrooms)
    if floor:
        clauses.append("floor=?"); params.append(floor)
    if property_type:
        clauses.append("property_type=?"); params.append(property_type)
    if condition:
        clauses.append("condition=?"); params.append(condition)
    if parish:
        clauses.append("parish=?"); params.append(parish)
    if district:
        clauses.append("district=?"); params.append(district)
    if city:
        clauses.append("city=?"); params.append(city)
    if postal_code:
        clauses.append("postal_code=?"); params.append(postal_code)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params += [limit, offset]

    rows = conn.execute(
        f"SELECT * FROM listings {where} ORDER BY scraped_at DESC LIMIT ? OFFSET ?",
        params
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        row = dict(r)
        if not row.get("listing_type"):
            row["listing_type"] = "sale"
        if not row.get("status"):
            row["status"] = "active"
        result.append(row)
    return result


# ── Neighborhoods ─────────────────────────────────────────────────────────────

def rebuild_neighborhoods():
    """Recompute neighborhood stats from current listings."""
    conn = get_connection()

    neighborhoods = conn.execute("""
        SELECT neighborhood, district
        FROM listings
        WHERE neighborhood IS NOT NULL
        GROUP BY neighborhood, district
    """).fetchall()

    updated = 0
    for n in neighborhoods:
        name = n["neighborhood"]
        district = n["district"] or "Lisboa"

        prices = [
            r["price_amount"] for r in conn.execute(
                "SELECT price_amount FROM listings WHERE neighborhood=? AND price_amount IS NOT NULL AND COALESCE(listing_type,'sale')='sale'",
                (name,)
            ).fetchall()
        ]
        psqm = [
            r["price_per_sqm"] for r in conn.execute(
                "SELECT price_per_sqm FROM listings WHERE neighborhood=? AND price_per_sqm IS NOT NULL AND COALESCE(listing_type,'sale')='sale'",
                (name,)
            ).fetchall()
        ]
        rent_psqm = [
            r["price_per_sqm"] for r in conn.execute(
                "SELECT price_per_sqm FROM listings WHERE neighborhood=? AND price_per_sqm IS NOT NULL AND listing_type='rent'",
                (name,)
            ).fetchall()
        ]
        sold_psqm = [
            r["price_per_sqm"] for r in conn.execute(
                "SELECT price_per_sqm FROM listings WHERE neighborhood=? AND status='sold' AND COALESCE(listing_type,'sale')='sale' AND price_per_sqm IS NOT NULL",
                (name,)
            ).fetchall()
        ]
        count_sold = len(sold_psqm)
        avg_sold_psqm = (sum(sold_psqm) / count_sold) if count_sold else None
        count = conn.execute(
            "SELECT COUNT(*) as c FROM listings WHERE neighborhood=?", (name,)
        ).fetchone()["c"]

        now = datetime.utcnow().isoformat()

        conn.execute("""
            INSERT INTO neighborhoods (
                name, district, listing_count,
                avg_price, median_price,
                avg_price_per_sqm, median_price_per_sqm,
                min_price_per_sqm, max_price_per_sqm,
                avg_rent_per_sqm, median_rent_per_sqm,
                avg_sold_price_per_sqm, count_sold,
                updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(name, district) DO UPDATE SET
                listing_count=excluded.listing_count,
                avg_price=excluded.avg_price,
                median_price=excluded.median_price,
                avg_price_per_sqm=excluded.avg_price_per_sqm,
                median_price_per_sqm=excluded.median_price_per_sqm,
                min_price_per_sqm=excluded.min_price_per_sqm,
                max_price_per_sqm=excluded.max_price_per_sqm,
                avg_rent_per_sqm=excluded.avg_rent_per_sqm,
                median_rent_per_sqm=excluded.median_rent_per_sqm,
                avg_sold_price_per_sqm=excluded.avg_sold_price_per_sqm,
                count_sold=excluded.count_sold,
                updated_at=excluded.updated_at
        """, (
            name, district, count,
            (sum(prices) / len(prices)) if prices else None,
            statistics.median(prices) if prices else None,
            (sum(psqm) / len(psqm)) if psqm else None,
            statistics.median(psqm) if psqm else None,
            min(psqm) if psqm else None,
            max(psqm) if psqm else None,
            (sum(rent_psqm) / len(rent_psqm)) if rent_psqm else None,
            statistics.median(rent_psqm) if rent_psqm else None,
            avg_sold_psqm, count_sold,
            now,
        ))
        updated += 1

    conn.commit()
    conn.close()
    logger.info(f"[DB] Rebuilt stats for {updated} neighborhoods")
    compute_rarity_scores()


def compute_rarity_scores():
    """
    Compute a rarity score (0–100) for every listing based on how unusual it is
    compared to other listings in the same neighborhood.

    Sub-scores (each 0–1):
      price_dev      (0.25) – |z-score| of price_per_sqm vs neighborhood μ/σ
      typology       (0.15) – 1 minus frequency of this room count in neighborhood
      size_dev       (0.10) – |z-score| of size_sqm vs neighborhood μ/σ
      condition      (0.20) – new condition in old-stock area, or to-renovate in new area
      scarcity       (0.10) – few listings in neighborhood → each is rarer
      prop_type      (0.05) – 1 minus frequency of property_type in neighborhood
      vs_sold        (0.10) – listing priced below neighborhood avg sold price → value rarity
      new_build_prox (0.05) – new-construction CML permit within ~400m of listing
    """
    conn = get_connection()

    # ── Per-neighborhood stats ──────────────────────────────────────────────────
    nbhd_rows = conn.execute(
        "SELECT neighborhood FROM listings WHERE neighborhood IS NOT NULL GROUP BY neighborhood"
    ).fetchall()

    stats = {}
    for row in nbhd_rows:
        name = row["neighborhood"]

        # Price/sqm distribution (sale only, active)
        psqm_vals = [r["price_per_sqm"] for r in conn.execute(
            "SELECT price_per_sqm FROM listings WHERE neighborhood=? AND price_per_sqm IS NOT NULL AND COALESCE(listing_type,'sale')='sale' AND status='active'",
            (name,)
        ).fetchall()]

        # Size distribution
        size_vals = [r["size_sqm"] for r in conn.execute(
            "SELECT size_sqm FROM listings WHERE neighborhood=? AND size_sqm IS NOT NULL AND status='active'",
            (name,)
        ).fetchall()]

        # Room count distribution
        rooms_rows = conn.execute(
            "SELECT rooms, COUNT(*) as c FROM listings WHERE neighborhood=? AND rooms IS NOT NULL AND status='active' GROUP BY rooms",
            (name,)
        ).fetchall()
        rooms_dist = {r["rooms"]: r["c"] for r in rooms_rows}
        rooms_total = sum(rooms_dist.values())

        # Condition distribution
        cond_rows = conn.execute(
            "SELECT condition, COUNT(*) as c FROM listings WHERE neighborhood=? AND condition IS NOT NULL AND status='active' GROUP BY condition",
            (name,)
        ).fetchall()
        cond_dist = {r["condition"]: r["c"] for r in cond_rows}
        cond_total = sum(cond_dist.values())

        # Property type distribution
        ptype_rows = conn.execute(
            "SELECT property_type, COUNT(*) as c FROM listings WHERE neighborhood=? AND property_type IS NOT NULL AND status='active' GROUP BY property_type",
            (name,)
        ).fetchall()
        ptype_dist = {r["property_type"]: r["c"] for r in ptype_rows}
        ptype_total = sum(ptype_dist.values())

        # Active listing count
        active_count = conn.execute(
            "SELECT COUNT(*) as c FROM listings WHERE neighborhood=? AND status='active'",
            (name,)
        ).fetchone()["c"]

        # Avg sold price/sqm (from neighborhoods table)
        nbhd_row = conn.execute(
            "SELECT avg_sold_price_per_sqm FROM neighborhoods WHERE name=?", (name,)
        ).fetchone()
        avg_sold = nbhd_row["avg_sold_price_per_sqm"] if nbhd_row else None

        def _mean(vals):
            return sum(vals) / len(vals) if vals else None

        def _std(vals, mean):
            if not vals or len(vals) < 2 or mean is None:
                return None
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            return math.sqrt(variance) if variance > 0 else None

        psqm_mean = _mean(psqm_vals)
        psqm_std  = _std(psqm_vals, psqm_mean)
        size_mean = _mean(size_vals)
        size_std  = _std(size_vals, size_mean)
        new_frac  = (cond_dist.get("new", 0) / cond_total) if cond_total else 0.33

        stats[name] = {
            "psqm_mean":   psqm_mean,
            "psqm_std":    psqm_std,
            "size_mean":   size_mean,
            "size_std":    size_std,
            "rooms_dist":  rooms_dist,
            "rooms_total": rooms_total,
            "cond_dist":   cond_dist,
            "cond_total":  cond_total,
            "new_frac":    new_frac,
            "ptype_dist":  ptype_dist,
            "ptype_total": ptype_total,
            "active_count": active_count,
            "avg_sold":    avg_sold,
        }

    # ── New-construction project centroids ──────────────────────────────────────
    # Only issued permits for new construction within the last 8 years to avoid
    # saturating every listing in dense Lisbon (the city has 45k+ projects total).
    cutoff_year = str(datetime.utcnow().year - 8)
    new_build_projects = conn.execute("""
        SELECT centroid_lat, centroid_lon FROM construction_projects
        WHERE layer='permit'
          AND operation = 'Construção Nova'
          AND centroid_lat IS NOT NULL AND centroid_lon IS NOT NULL
          AND date_permit >= ?
    """, (cutoff_year,)).fetchall()
    nb_pts = [(r["centroid_lat"], r["centroid_lon"]) for r in new_build_projects]
    logger.info(f"[rarity] {len(nb_pts)} recent new-construction permits loaded")

    def _haversine_deg(lat1, lon1, lat2, lon2):
        """Approximate great-circle distance in metres (fast, good enough for ~400 m threshold)."""
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return 6371000 * 2 * math.asin(math.sqrt(a))

    def _near_new_build(lat, lon, threshold_m=400):
        if lat is None or lon is None:
            return False
        for (plat, plon) in nb_pts:
            if abs(plat - lat) > 0.01 or abs(plon - lon) > 0.01:
                continue  # fast bbox pre-filter
            if _haversine_deg(lat, lon, plat, plon) <= threshold_m:
                return True
        return False

    # ── Sub-score helpers ───────────────────────────────────────────────────────

    def _z_score_component(value, mean, std, cap=3.0):
        """Normalised |z-score|, capped at cap σ → returns 0–1."""
        if value is None or mean is None or std is None or std == 0:
            return 0.0
        return min(abs(value - mean) / std, cap) / cap

    def _frequency_rarity(value, dist, total):
        """1 minus frequency of value in dist → rare values score higher."""
        if value is None or total == 0:
            return 0.0
        freq = dist.get(value, 0) / total
        return 1.0 - freq

    def _condition_score(condition, new_frac):
        """
        New condition in an area with few new builds = high rarity.
        Used / to-renovate in an area dominated by new builds = high rarity.
        """
        if condition is None:
            return 0.0
        if condition == "new":
            return 1.0 - new_frac         # rarer when new_frac is low
        elif condition in ("used", "renovated"):
            return new_frac               # rarer when new_frac is high
        return 0.3

    def _scarcity_score(active_count):
        """Exponential decay: ≤3 listings → 1.0, 30 listings → ~0.1."""
        if active_count <= 0:
            return 1.0
        return math.exp(-active_count / 10.0)

    def _vs_sold_score(price_psqm, avg_sold, listing_type):
        """
        For sale listings only: if listed price/sqm is below avg sold price → value rarity.
        Score = how far below sold avg (capped at 30% below → 1.0).
        Rental price_per_sqm is monthly and incomparable — excluded.
        Also guard against bad data (price_psqm must be plausibly a sale €/m², i.e. > 500).
        """
        if listing_type != "sale":
            return 0.0
        if price_psqm is None or price_psqm < 500 or avg_sold is None or avg_sold == 0:
            return 0.0
        ratio = price_psqm / avg_sold
        if ratio >= 1.0:
            return 0.0
        return min((1.0 - ratio) / 0.30, 1.0)

    # ── Weights ──────────────────────────────────────────────────────────────────
    W = {
        "price_dev":     0.25,
        "typology":      0.15,
        "size_dev":      0.10,
        "condition":     0.20,
        "scarcity":      0.10,
        "prop_type":     0.05,
        "vs_sold":       0.10,
        "new_build_prox": 0.05,
    }

    # ── Compute and store ────────────────────────────────────────────────────────
    listings = conn.execute(
        "SELECT id, neighborhood, price_per_sqm, size_sqm, rooms, condition, property_type, lat, lon, listing_type FROM listings"
    ).fetchall()

    updated = 0
    for l in listings:
        nbhd = l["neighborhood"]
        s = stats.get(nbhd)
        if not s:
            continue

        factors = {
            "price_dev":      round(_z_score_component(l["price_per_sqm"], s["psqm_mean"], s["psqm_std"]), 3),
            "typology":       round(_frequency_rarity(l["rooms"], s["rooms_dist"], s["rooms_total"]), 3),
            "size_dev":       round(_z_score_component(l["size_sqm"], s["size_mean"], s["size_std"]), 3),
            "condition":      round(_condition_score(l["condition"], s["new_frac"]), 3),
            "scarcity":       round(_scarcity_score(s["active_count"]), 3),
            "prop_type":      round(_frequency_rarity(l["property_type"], s["ptype_dist"], s["ptype_total"]), 3),
            "vs_sold":        round(_vs_sold_score(l["price_per_sqm"], s["avg_sold"], l["listing_type"] or "sale"), 3),
            "new_build_prox": round(1.0 if _near_new_build(l["lat"], l["lon"]) else 0.0, 3),
        }

        score = round(sum(W[k] * factors[k] for k in W) * 100, 1)

        conn.execute(
            "UPDATE listings SET rarity_score=?, rarity_factors=? WHERE id=?",
            (score, json.dumps(factors), l["id"])
        )
        updated += 1

    conn.commit()
    conn.close()
    logger.info(f"[DB] Computed rarity scores for {updated} listings")


def get_neighborhoods(district: Optional[str] = None) -> List[dict]:
    conn = get_connection()
    if district:
        rows = conn.execute(
            "SELECT * FROM neighborhoods WHERE district=? ORDER BY avg_price_per_sqm",
            (district,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM neighborhoods ORDER BY avg_price_per_sqm"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Scrape Runs ───────────────────────────────────────────────────────────────

def start_scrape_run(source: str) -> int:
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO scrape_runs (source, started_at, status) VALUES (?,?,?)",
                (source, datetime.utcnow().isoformat(), "running")
            )
        return cur.lastrowid
    finally:
        conn.close()


def finish_scrape_run(run_id: int, run: ScrapeRun):
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                UPDATE scrape_runs SET
                    finished_at=?, listings_found=?, listings_new=?,
                    listings_updated=?, errors=?, status=?, notes=?
                WHERE id=?
            """, (
                datetime.utcnow().isoformat(),
                run.listings_found, run.listings_new,
                run.listings_updated, run.errors,
                run.status, run.notes, run_id
            ))
    finally:
        conn.close()


def get_scrape_runs(source: Optional[str] = None, limit: int = 20) -> List[dict]:
    conn = get_connection()
    try:
        if source:
            rows = conn.execute(
                "SELECT * FROM scrape_runs WHERE source=? ORDER BY started_at DESC LIMIT ?",
                (source, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Construction Projects ─────────────────────────────────────────────────────

def upsert_project(p: dict) -> bool:
    """Insert or update a construction project. Returns True if new."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM construction_projects WHERE source_id=? AND layer=?",
            (p["source_id"], p["layer"])
        ).fetchone()
        if row:
            with conn:
                conn.execute("""
                    UPDATE construction_projects SET
                        address=?, parish=?, operation=?, subject=?, procedure=?,
                        typology=?, date_submitted=?, permit_number=?, date_permit=?,
                        permit_type=?, geometry=?, centroid_lat=?, centroid_lon=?, fetched_at=?
                    WHERE source_id=? AND layer=?
                """, (
                    p.get("address"), p.get("parish"), p.get("operation"),
                    p.get("subject"), p.get("procedure"), p.get("typology"),
                    p.get("date_submitted"), p.get("permit_number"), p.get("date_permit"),
                    p.get("permit_type"), p.get("geometry"),
                    p.get("centroid_lat"), p.get("centroid_lon"),
                    datetime.utcnow().isoformat(),
                    p["source_id"], p["layer"]
                ))
            return False
        else:
            with conn:
                conn.execute("""
                    INSERT INTO construction_projects (
                        source_id, layer, address, parish, operation, subject,
                        procedure, typology, date_submitted, permit_number,
                        date_permit, permit_type, geometry, centroid_lat,
                        centroid_lon, fetched_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    p["source_id"], p["layer"], p.get("address"), p.get("parish"),
                    p.get("operation"), p.get("subject"), p.get("procedure"),
                    p.get("typology"), p.get("date_submitted"), p.get("permit_number"),
                    p.get("date_permit"), p.get("permit_type"), p.get("geometry"),
                    p.get("centroid_lat"), p.get("centroid_lon"),
                    datetime.utcnow().isoformat()
                ))
            return True
    finally:
        conn.close()


def get_projects(
    layer: Optional[str] = None,
    parish: Optional[str] = None,
    limit: int = 5000,
) -> List[dict]:
    conn = get_connection()
    clauses, params = [], []
    if layer:
        clauses.append("layer=?"); params.append(layer)
    if parish:
        clauses.append("parish=?"); params.append(parish)
    clauses.append("centroid_lat IS NOT NULL")
    where = "WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = conn.execute(
        f"SELECT id, source_id, layer, address, parish, operation, subject, "
        f"procedure, typology, date_submitted, permit_number, date_permit, "
        f"permit_type, centroid_lat, centroid_lon "
        f"FROM construction_projects {where} ORDER BY date_submitted DESC LIMIT ?",
        params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Security POIs ─────────────────────────────────────────────────────────────

def upsert_security_poi(poi: dict):
    """Insert or update a security POI (police station or CCTV camera)."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO security_pois (
                    source_id, layer, name, address, parish, phone, lat, lon, fetched_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_id, layer) DO UPDATE SET
                    name=excluded.name,
                    address=excluded.address,
                    parish=excluded.parish,
                    phone=excluded.phone,
                    lat=excluded.lat,
                    lon=excluded.lon,
                    fetched_at=excluded.fetched_at
            """, (
                poi["source_id"], poi["layer"], poi.get("name"),
                poi.get("address"), poi.get("parish"), poi.get("phone"),
                poi["lat"], poi["lon"], poi["fetched_at"],
            ))
    finally:
        conn.close()


def get_security_pois(layer: Optional[str] = None) -> List[dict]:
    """Return security POIs, optionally filtered by layer type."""
    conn = get_connection()
    try:
        if layer:
            rows = conn.execute(
                "SELECT * FROM security_pois WHERE layer=? ORDER BY name",
                (layer,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM security_pois ORDER BY layer, name"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── INE Stats ─────────────────────────────────────────────────────────────────

def upsert_ine_stat(stat: dict):
    """Insert or update one INE stat record."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO ine_stats (
                    period_label, geocod, geodsg, category, category_label,
                    median_price_per_sqm, fetched_at, is_latest
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(period_label, geocod, category) DO UPDATE SET
                    geodsg=excluded.geodsg,
                    category_label=excluded.category_label,
                    median_price_per_sqm=excluded.median_price_per_sqm,
                    fetched_at=excluded.fetched_at,
                    is_latest=excluded.is_latest
            """, (
                stat["period_label"], stat["geocod"], stat["geodsg"],
                stat["category"], stat["category_label"],
                stat["median_price_per_sqm"], stat["fetched_at"],
                1 if stat.get("is_latest") else 0,
            ))
    finally:
        conn.close()


def get_ine_stats(geocod: Optional[str] = "1A01106", latest_only: bool = True) -> List[dict]:
    """
    Return INE housing transaction stats.
    geocod=None returns all stored municipalities.
    latest_only=True returns only the most recent period per municipality.
    """
    conn = get_connection()
    try:
        if latest_only:
            if geocod is None:
                rows = conn.execute(
                    "SELECT * FROM ine_stats WHERE is_latest=1 ORDER BY geodsg, category"
                ).fetchall()
                if not rows:
                    # Fallback: derive latest period globally
                    all_rows = conn.execute(
                        "SELECT * FROM ine_stats ORDER BY period_label"
                    ).fetchall()
                    if all_rows:
                        from scrapers.ine_housing import _period_sort_key
                        latest_label = max(
                            set(r["period_label"] for r in all_rows),
                            key=_period_sort_key
                        )
                        rows = [r for r in all_rows if r["period_label"] == latest_label]
            else:
                rows = conn.execute(
                    "SELECT * FROM ine_stats WHERE geocod=? AND is_latest=1 ORDER BY category",
                    (geocod,)
                ).fetchall()
                if not rows:
                    all_rows = conn.execute(
                        "SELECT * FROM ine_stats WHERE geocod=? ORDER BY period_label",
                        (geocod,)
                    ).fetchall()
                    if all_rows:
                        from scrapers.ine_housing import _period_sort_key
                        latest_label = max(
                            set(r["period_label"] for r in all_rows),
                            key=_period_sort_key
                        )
                        rows = [r for r in all_rows if r["period_label"] == latest_label]
        else:
            if geocod is None:
                rows = conn.execute(
                    "SELECT * FROM ine_stats ORDER BY geodsg, period_label, category"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ine_stats WHERE geocod=? ORDER BY period_label, category",
                    (geocod,)
                ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
