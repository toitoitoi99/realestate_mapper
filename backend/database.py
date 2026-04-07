"""
SQLite database layer for the Lisboa Real Estate app.
Handles schema creation and all CRUD operations.

Listings are stored in two separate tables: `sales` and `rentals`.
"""

import hashlib
import json
import math
import re
import sqlite3
import logging
import statistics
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from models import Listing, Neighborhood, ScrapeRun

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "lisboa_realestate.db"

# -- Listing exclusion filters -------------------------------------------------
# Keywords that indicate non-property listings (timeshares, hotel weeks, etc.)
# Checked in upsert_listing() so all scrapers benefit automatically.
EXCLUDED_TITLE_KEYWORDS = [
    "time sharing", "timesharing", "time-sharing", "timeshare",
    "direito de habitação periódica",  # Portuguese legal term for timeshare
    "habitação periódica",
    "multipropriedade",               # fractional ownership
]
EXCLUDED_DESCRIPTION_KEYWORDS = [
    "time sharing", "timesharing", "time-sharing", "timeshare",
    "direito de habitação periódica",
    "habitação periódica",
    "multipropriedade",
]


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _compute_hash_cross(address, city, price, size) -> str:
    """Source-agnostic hash for cross-site matching."""
    addr = (address or "").lower().strip()
    price_r = str(round(price / 1000) * 1000) if price else ""
    size_r = str(round(size)) if size else ""
    raw = f"{addr}|{(city or '').lower()}|{price_r}|{size_r}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _compute_hash_location(address, city, size, rooms=None) -> str:
    """Price-independent hash for re-list detection.
    Same property at a different price will still match."""
    addr = (address or "").lower().strip()
    size_r = str(round(size)) if size else ""
    rooms_r = str(rooms) if rooms is not None else ""
    raw = f"{addr}|{(city or '').lower()}|{size_r}|{rooms_r}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


_LISTING_COLS = """
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    price_amount    REAL,
    price_per_sqm   REAL,
    size_sqm        REAL,
    gross_area_sqm  REAL,
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
    hash_cross      TEXT,
    hash_location   TEXT,
    missing_since   TEXT,
    previous_listing_id INTEGER,
    description     TEXT,
    scraped_at      TEXT NOT NULL,
"""


def _table_for(listing_type):
    """Return the table name for a listing type."""
    return "rentals" if listing_type == "rent" else "sales"


def init_db():
    """Create tables if they don't exist. Migrate old `listings` table if present."""
    conn = get_connection()

    # -- Sales table (includes rarity columns) --------------------------------
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS sales (
            {_LISTING_COLS}
            rarity_score    REAL,
            rarity_factors  TEXT,
            building_geojson TEXT,
            UNIQUE(source, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sales_neighborhood ON sales(neighborhood);
        CREATE INDEX IF NOT EXISTS idx_sales_source       ON sales(source);
        CREATE INDEX IF NOT EXISTS idx_sales_price_sqm    ON sales(price_per_sqm);
        CREATE INDEX IF NOT EXISTS idx_sales_hash         ON sales(hash_dedupe);
        CREATE INDEX IF NOT EXISTS idx_sales_latlon       ON sales(lat, lon);
        CREATE INDEX IF NOT EXISTS idx_sales_status       ON sales(status);
    """)

    # -- Rentals table (no rarity columns) ------------------------------------
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS rentals (
            {_LISTING_COLS}
            UNIQUE(source, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_rentals_neighborhood ON rentals(neighborhood);
        CREATE INDEX IF NOT EXISTS idx_rentals_source       ON rentals(source);
        CREATE INDEX IF NOT EXISTS idx_rentals_price_sqm    ON rentals(price_per_sqm);
        CREATE INDEX IF NOT EXISTS idx_rentals_hash         ON rentals(hash_dedupe);
        CREATE INDEX IF NOT EXISTS idx_rentals_latlon       ON rentals(lat, lon);
        CREATE INDEX IF NOT EXISTS idx_rentals_status       ON rentals(status);
    """)

    # -- Migrate old `listings` table if it exists ----------------------------
    has_old = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
    ).fetchone()
    if has_old:
        logger.info("[DB] Migrating old `listings` table → `sales` + `rentals`…")
        # Check which columns exist in old table (some may be from additive migrations)
        old_cols = {row[1] for row in conn.execute("PRAGMA table_info(listings)").fetchall()}

        # Common columns present in both new tables
        shared = [
            "source", "source_id", "url", "status", "price_amount", "price_per_sqm",
            "size_sqm", "gross_area_sqm", "rooms", "bedrooms", "bathrooms", "floor", "property_type",
            "condition", "title", "address", "postal_code", "neighborhood", "parish",
            "district", "city", "lat", "lon", "images", "hash_dedupe", "description",
            "scraped_at",
        ]
        sale_extra = ["rarity_score", "rarity_factors", "building_geojson"]

        # Only copy columns that exist in the old table
        shared_present = [c for c in shared if c in old_cols]
        sale_extra_present = [c for c in sale_extra if c in old_cols]

        cols_for_sale = ", ".join(shared_present + sale_extra_present)
        cols_for_rent = ", ".join(shared_present)

        lt_col = "listing_type" if "listing_type" in old_cols else None
        if lt_col:
            sale_where = "COALESCE(listing_type, 'sale') != 'rent'"
            rent_where = "listing_type = 'rent'"
        else:
            sale_where = "1=1"
            rent_where = "1=0"  # no rentals if column never existed

        conn.execute(f"""
            INSERT OR IGNORE INTO sales ({cols_for_sale})
            SELECT {cols_for_sale} FROM listings WHERE {sale_where}
        """)
        conn.execute(f"""
            INSERT OR IGNORE INTO rentals ({cols_for_rent})
            SELECT {cols_for_rent} FROM listings WHERE {rent_where}
        """)
        conn.execute("ALTER TABLE listings RENAME TO listings_backup")
        conn.commit()

        sale_count = conn.execute("SELECT COUNT(*) as c FROM sales").fetchone()["c"]
        rent_count = conn.execute("SELECT COUNT(*) as c FROM rentals").fetchone()["c"]
        logger.info(f"[DB] Migration complete: {sale_count} sales, {rent_count} rentals")

    # -- Other tables ---------------------------------------------------------
    conn.executescript("""
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
            avg_rent_per_sqm        REAL,
            median_rent_per_sqm     REAL,
            avg_sold_price_per_sqm  REAL,
            count_sold              INTEGER DEFAULT 0,
            geometry                TEXT,
            updated_at              TEXT NOT NULL,
            UNIQUE(name, district)
        );
        CREATE INDEX IF NOT EXISTS idx_neighborhoods_district ON neighborhoods(district);

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
        CREATE INDEX IF NOT EXISTS idx_scrape_runs_status ON scrape_runs(status);
        CREATE INDEX IF NOT EXISTS idx_scrape_runs_source ON scrape_runs(source);

        CREATE TABLE IF NOT EXISTS construction_projects (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id       TEXT NOT NULL,
            layer           TEXT NOT NULL,
            address         TEXT,
            parish          TEXT,
            operation       TEXT,
            subject         TEXT,
            procedure       TEXT,
            typology        TEXT,
            date_submitted  TEXT,
            permit_number   TEXT,
            date_permit     TEXT,
            permit_type     TEXT,
            geometry        TEXT,
            centroid_lat    REAL,
            centroid_lon    REAL,
            fetched_at      TEXT NOT NULL,
            UNIQUE(source_id, layer)
        );
        CREATE INDEX IF NOT EXISTS idx_projects_layer  ON construction_projects(layer);
        CREATE INDEX IF NOT EXISTS idx_projects_parish ON construction_projects(parish);
        CREATE INDEX IF NOT EXISTS idx_projects_latlon ON construction_projects(centroid_lat, centroid_lon);

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
        );
        CREATE INDEX IF NOT EXISTS idx_security_pois_layer ON security_pois(layer);

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
        );
        CREATE INDEX IF NOT EXISTS idx_ine_stats_geocod ON ine_stats(geocod, category);

        CREATE TABLE IF NOT EXISTS amenity_ratings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            lat_key         REAL NOT NULL,
            lon_key         REAL NOT NULL,
            overall_score   REAL NOT NULL,
            classification  TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            fetched_at      TEXT NOT NULL,
            UNIQUE(lat_key, lon_key)
        );
        CREATE INDEX IF NOT EXISTS idx_amenity_ratings_latlon ON amenity_ratings(lat_key, lon_key);

        CREATE TABLE IF NOT EXISTS listing_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            listing_type TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_listing_history_listing ON listing_history(listing_id, listing_type);
        CREATE INDEX IF NOT EXISTS idx_listing_history_source ON listing_history(source, source_id);
    """)

    # -- Additive column migrations ---------------------------------------------
    for tbl in ("sales", "rentals"):
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
        if "gross_area_sqm" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN gross_area_sqm REAL")
            logger.info(f"[DB] Added gross_area_sqm column to {tbl}")
        if "description_en" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN description_en TEXT")
            logger.info(f"[DB] Added description_en column to {tbl}")
        if "hash_cross" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN hash_cross TEXT")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tbl}_hash_cross ON {tbl}(hash_cross)")
            logger.info(f"[DB] Added hash_cross column to {tbl}")
        if "hash_location" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN hash_location TEXT")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tbl}_hash_location ON {tbl}(hash_location)")
            logger.info(f"[DB] Added hash_location column to {tbl}")
        if "missing_since" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN missing_since TEXT")
            logger.info(f"[DB] Added missing_since column to {tbl}")
        if "previous_listing_id" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN previous_listing_id INTEGER")
            logger.info(f"[DB] Added previous_listing_id column to {tbl}")
    conn.commit()

    # -- Backfill hash_cross for existing rows ------------------------------------
    for tbl in ("sales", "rentals"):
        rows = conn.execute(
            f"SELECT id, address, city, price_amount, size_sqm FROM {tbl} WHERE hash_cross IS NULL AND address IS NOT NULL"
        ).fetchall()
        if rows:
            for r in rows:
                hc = _compute_hash_cross(r["address"], r["city"], r["price_amount"], r["size_sqm"])
                conn.execute(f"UPDATE {tbl} SET hash_cross=? WHERE id=?", (hc, r["id"]))
            conn.commit()
            logger.info(f"[DB] Backfilled hash_cross for {len(rows)} rows in {tbl}")

    # -- Backfill hash_location for existing rows --------------------------------
    for tbl in ("sales", "rentals"):
        rows = conn.execute(
            f"SELECT id, address, city, size_sqm, rooms FROM {tbl} WHERE hash_location IS NULL AND address IS NOT NULL"
        ).fetchall()
        if rows:
            for r in rows:
                hl = _compute_hash_location(r["address"], r["city"], r["size_sqm"], r["rooms"])
                conn.execute(f"UPDATE {tbl} SET hash_location=? WHERE id=?", (hl, r["id"]))
            conn.commit()
            logger.info(f"[DB] Backfilled hash_location for {len(rows)} rows in {tbl}")

    conn.close()
    logger.info(f"[DB] Initialised at {DB_PATH}")


# ── Listings ─────────────────────────────────────────────────────────────────

MISSING_GRACE_DAYS = 7


def _detect_relist(listing: Listing, table: str, conn, hash_location: Optional[str]) -> Optional[int]:
    """Check if a new listing is a re-list of a previous one at a different price.

    Match criteria: same hash_location + same source + different source_id.
    If found, marks the old listing as 'delisted' and returns its id.
    """
    if not hash_location or not listing.address:
        return None

    rows = conn.execute(
        f"SELECT id, source_id, price_amount, status FROM {table} "
        f"WHERE hash_location=? AND source=? AND source_id!=? AND status IN ('active','reserved') "
        f"ORDER BY scraped_at DESC LIMIT 5",
        (hash_location, listing.source, listing.source_id)
    ).fetchall()

    for old in rows:
        old_price = old["price_amount"]
        new_price = listing.price_amount
        # Only link if price actually differs (or one is missing)
        if old_price and new_price and round(old_price / 1000) == round(new_price / 1000):
            continue  # Same price band — not a re-list, likely a duplicate

        lt = "rent" if table == "rentals" else "sale"
        now = datetime.utcnow().isoformat()
        conn.execute(
            f"UPDATE {table} SET status='delisted', missing_since=NULL WHERE id=?",
            (old["id"],)
        )
        conn.execute(
            "INSERT INTO listing_history "
            "(listing_id, listing_type, source, source_id, field, old_value, new_value, changed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (old["id"], lt, listing.source, old["source_id"],
             "status", old["status"], "delisted", now)
        )
        logger.info(
            f"[DB] Re-list detected: old #{old['id']} ({old_price}) → new {listing.source_id} ({new_price}), "
            f"marked old as delisted"
        )
        return old["id"]

    return None


def process_missing_listings(
    source: str,
    listing_type: str,
    seen_source_ids: set,
    run_id: Optional[int] = None,
) -> dict:
    """After a scrape run, detect listings that have gone missing.

    - Newly missing: set missing_since = now
    - Missing > MISSING_GRACE_DAYS days: transition to 'sold'
    - Reappeared during grace period: clear missing_since
    """
    table = _table_for(listing_type)
    lt = "rent" if table == "rentals" else "sale"
    conn = get_connection()
    now = datetime.utcnow()
    now_iso = now.isoformat()
    cutoff = (now - timedelta(days=MISSING_GRACE_DAYS)).isoformat()

    rows = conn.execute(
        f"SELECT id, source_id, missing_since FROM {table} "
        f"WHERE source=? AND status='active'",
        (source,)
    ).fetchall()

    newly_missing = 0
    marked_sold = 0
    reappeared = 0

    with conn:
        for r in rows:
            sid = r["source_id"]
            ms = r["missing_since"]

            if sid in seen_source_ids:
                # Still on the site
                if ms is not None:
                    conn.execute(
                        f"UPDATE {table} SET missing_since=NULL WHERE id=?",
                        (r["id"],)
                    )
                    reappeared += 1
            else:
                # Not found on site
                if ms is None:
                    conn.execute(
                        f"UPDATE {table} SET missing_since=? WHERE id=?",
                        (now_iso, r["id"])
                    )
                    newly_missing += 1
                elif ms <= cutoff:
                    conn.execute(
                        f"UPDATE {table} SET status='sold', missing_since=NULL WHERE id=?",
                        (r["id"],)
                    )
                    conn.execute(
                        "INSERT INTO listing_history "
                        "(listing_id, listing_type, source, source_id, field, old_value, new_value, changed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (r["id"], lt, source, sid, "status", "active", "sold", now_iso)
                    )
                    marked_sold += 1

    conn.close()

    result = {"newly_missing": newly_missing, "marked_sold": marked_sold, "reappeared": reappeared}
    logger.info(f"[DB] Missing detection for {source}/{listing_type}: {result}")
    return result


def upsert_listing(listing: Listing) -> tuple:
    """
    Insert or update a listing. Returns (id, is_new).
    Natural key: (source, source_id).
    Routes to the correct table based on listing_type.
    Returns (None, False) if listing is excluded (timeshare, etc.).
    """
    # Filter out non-property listings (timeshares, hotel weeks, etc.)
    title_lower = (listing.title or "").lower()
    desc_lower = (listing.description or "").lower()
    if any(kw in title_lower for kw in EXCLUDED_TITLE_KEYWORDS):
        logger.info(f"[DB] Excluded non-property listing (title): {listing.source_id} — {listing.title}")
        return (None, False)
    if any(kw in desc_lower for kw in EXCLUDED_DESCRIPTION_KEYWORDS):
        logger.info(f"[DB] Excluded non-property listing (description): {listing.source_id}")
        return (None, False)

    table = _table_for(getattr(listing, "listing_type", None) or "sale")
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT id, status, price_amount, price_per_sqm FROM {table} WHERE source=? AND source_id=?",
            (listing.source, listing.source_id)
        ).fetchone()

        ts = listing.scraped_at.isoformat() if listing.scraped_at else datetime.utcnow().isoformat()

        if row:
            # Track changes to key fields
            _TRACKED = ['status', 'price_amount', 'price_per_sqm']
            lt = "rent" if table == "rentals" else "sale"
            now = datetime.utcnow().isoformat()
            history_records = []
            for field_name in _TRACKED:
                old_val = row[field_name]
                new_val = getattr(listing, field_name)
                if old_val is None and new_val is None:
                    continue
                if str(old_val) != str(new_val):
                    history_records.append((
                        row["id"], lt, listing.source, listing.source_id,
                        field_name,
                        str(old_val) if old_val is not None else None,
                        str(new_val) if new_val is not None else None,
                        now
                    ))
            with conn:
                for rec in history_records:
                    conn.execute("""
                        INSERT INTO listing_history
                            (listing_id, listing_type, source, source_id, field, old_value, new_value, changed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, rec)
                hl = _compute_hash_location(listing.address, listing.city, listing.size_sqm, listing.rooms)
                conn.execute(f"""
                    UPDATE {table} SET
                        url=?, status=?, price_amount=?, price_per_sqm=?,
                        size_sqm=?, gross_area_sqm=?, rooms=?, bedrooms=?, bathrooms=?, floor=?,
                        property_type=?, condition=?, title=?, address=?, postal_code=?,
                        neighborhood=?, parish=?, district=?, city=?, lat=?, lon=?,
                        images=?, hash_dedupe=?, hash_cross=?, hash_location=?,
                        missing_since=NULL, description=?, scraped_at=?
                    WHERE source=? AND source_id=?
                """, (
                    listing.url, listing.status,
                    listing.price_amount, listing.price_per_sqm,
                    listing.size_sqm, listing.gross_area_sqm, listing.rooms, listing.bedrooms,
                    listing.bathrooms, listing.floor, listing.property_type,
                    listing.condition, listing.title, listing.address,
                    listing.postal_code, listing.neighborhood, listing.parish,
                    listing.district, listing.city, listing.lat, listing.lon,
                    listing.images, listing.hash_dedupe, listing.hash_cross, hl,
                    listing.description,
                    ts, listing.source, listing.source_id
                ))
            return row["id"], False
        else:
            hl = _compute_hash_location(listing.address, listing.city, listing.size_sqm, listing.rooms)
            prev_id = _detect_relist(listing, table, conn, hl)
            with conn:
                cur = conn.execute(f"""
                    INSERT INTO {table} (
                        source, source_id, url, status,
                        price_amount, price_per_sqm, size_sqm, gross_area_sqm, rooms, bedrooms,
                        bathrooms, floor, property_type, condition, title,
                        address, postal_code, neighborhood, parish, district,
                        city, lat, lon, images, hash_dedupe, hash_cross,
                        hash_location, previous_listing_id, description, scraped_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    listing.source, listing.source_id, listing.url,
                    listing.status,
                    listing.price_amount, listing.price_per_sqm,
                    listing.size_sqm, listing.gross_area_sqm, listing.rooms, listing.bedrooms,
                    listing.bathrooms, listing.floor, listing.property_type,
                    listing.condition, listing.title, listing.address,
                    listing.postal_code, listing.neighborhood, listing.parish,
                    listing.district, listing.city, listing.lat, listing.lon,
                    listing.images, listing.hash_dedupe, listing.hash_cross,
                    hl, prev_id, listing.description, ts
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
    table = _table_for(listing_type) if listing_type else None
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

    # Shared columns for union queries (excludes sales-only rarity columns)
    _shared_cols = (
        "id, source, source_id, url, status, price_amount, price_per_sqm, "
        "size_sqm, gross_area_sqm, rooms, bedrooms, bathrooms, floor, property_type, condition, "
        "title, address, postal_code, neighborhood, parish, district, city, "
        "lat, lon, images, hash_dedupe, hash_cross, hash_location, "
        "missing_since, previous_listing_id, description, scraped_at"
    )

    if table:
        # Query a single table — add listing_type to results
        lt = "rent" if table == "rentals" else "sale"
        rows = conn.execute(
            f"SELECT *, '{lt}' as listing_type FROM {table} {where} ORDER BY scraped_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
    else:
        # Union both tables using shared columns
        rows = conn.execute(
            f"SELECT {_shared_cols}, rarity_score, rarity_factors, building_geojson, 'sale' as listing_type FROM sales {where} "
            f"UNION ALL "
            f"SELECT {_shared_cols}, NULL as rarity_score, NULL as rarity_factors, NULL as building_geojson, 'rent' as listing_type FROM rentals {where} "
            f"ORDER BY scraped_at DESC LIMIT ? OFFSET ?",
            params + params + [limit, offset]
        ).fetchall()

    conn.close()
    result = []
    for r in rows:
        row = dict(r)
        if not row.get("status"):
            row["status"] = "active"
        result.append(row)
    return result


def get_cross_listings(hash_cross: Optional[str], exclude_id: int, listing_type: str = "sale") -> list:
    """Find listings from other sources that match the same property via hash_cross."""
    if not hash_cross:
        return []
    table = _table_for(listing_type)
    conn = get_connection()
    rows = conn.execute(
        f"SELECT id, source, source_id, url, price_amount, price_per_sqm, "
        f"size_sqm, scraped_at, status FROM {table} "
        f"WHERE hash_cross=? AND id!=? ORDER BY scraped_at DESC",
        (hash_cross, exclude_id)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_listing_history(listing_id: int, listing_type: str = "sale") -> List[dict]:
    """Return change history for a listing."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM listing_history WHERE listing_id=? AND listing_type=? ORDER BY changed_at DESC",
        (listing_id, listing_type)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_radius_comparison(
    listing_id: int,
    listing_type: str = "sale",
    radius_m: int = 500,
    filter_property_type: Optional[str] = None,
    filter_bedrooms: Optional[int] = None,
) -> dict:
    """Compare a listing's price/m² to nearby sales and rentals within a radius."""
    conn = get_connection()
    table = _table_for(listing_type)

    # Fetch target listing
    target = conn.execute(
        f"SELECT id, lat, lon, price_per_sqm, price_amount, size_sqm, property_type, bedrooms FROM {table} WHERE id=?",
        (listing_id,)
    ).fetchone()
    if not target or target["lat"] is None or target["lon"] is None:
        conn.close()
        return {"error": "Listing not found or missing coordinates"}

    lat, lon = target["lat"], target["lon"]
    # Bounding box (at ~38.7N: 1 lat ~ 111km, 1 lon ~ 87km)
    dlat = radius_m / 111000
    dlon = radius_m / 87000

    # --- Comparable sales ---
    where = ["lat BETWEEN ? AND ?", "lon BETWEEN ? AND ?", "price_per_sqm IS NOT NULL", "id != ?"]
    params = [lat - dlat, lat + dlat, lon - dlon, lon + dlon, listing_id]

    if filter_property_type:
        where.append("property_type = ?")
        params.append(filter_property_type)
    if filter_bedrooms is not None:
        where.append("bedrooms = ?")
        params.append(filter_bedrooms)

    sales_rows = conn.execute(
        f"SELECT id, price_per_sqm, price_amount, size_sqm, property_type, bedrooms, "
        f"address, lat, lon, status, source, scraped_at "
        f"FROM {table} WHERE {' AND '.join(where)}",
        params
    ).fetchall()

    # Haversine refinement
    comparables = []
    for r in sales_rows:
        dist = _haversine(lat, lon, r["lat"], r["lon"])
        if dist <= radius_m:
            d = dict(r)
            d["distance_m"] = round(dist)
            comparables.append(d)

    comparables.sort(key=lambda x: x["distance_m"])

    # Stats
    psqm_values = [c["price_per_sqm"] for c in comparables if c["price_per_sqm"]]
    comp_stats = {}
    if psqm_values:
        psqm_sorted = sorted(psqm_values)
        n = len(psqm_sorted)
        comp_stats = {
            "median_price_per_sqm": round(statistics.median(psqm_sorted), 2),
            "avg_price_per_sqm": round(statistics.mean(psqm_sorted), 2),
            "min_price_per_sqm": round(psqm_sorted[0], 2),
            "max_price_per_sqm": round(psqm_sorted[-1], 2),
            "p25_price_per_sqm": round(psqm_sorted[max(0, n // 4 - 1)], 2),
            "p75_price_per_sqm": round(psqm_sorted[min(n - 1, 3 * n // 4)], 2),
        }
        # Where does the target listing fall?
        tp = target["price_per_sqm"]
        if tp is not None:
            below = sum(1 for v in psqm_sorted if v < tp)
            comp_stats["listing_percentile"] = round(below / n * 100, 1)

    # --- Nearby rentals ---
    rent_table = "rentals" if table == "sales" else "sales"
    rent_rows = conn.execute(
        f"SELECT id, price_per_sqm, price_amount, size_sqm, bedrooms, address, lat, lon, source "
        f"FROM {rent_table} WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND price_per_sqm IS NOT NULL",
        (lat - dlat, lat + dlat, lon - dlon, lon + dlon)
    ).fetchall()

    rentals = []
    for r in rent_rows:
        dist = _haversine(lat, lon, r["lat"], r["lon"])
        if dist <= radius_m:
            d = dict(r)
            d["distance_m"] = round(dist)
            rentals.append(d)

    rentals.sort(key=lambda x: x["distance_m"])

    rent_psqm = [r["price_per_sqm"] for r in rentals if r["price_per_sqm"]]
    rent_stats = {}
    if rent_psqm:
        avg_rent = statistics.mean(rent_psqm)
        med_rent = statistics.median(rent_psqm)
        est_monthly = round(avg_rent * (target["size_sqm"] or 0), 2)
        gross_yield = round((avg_rent * 12 / target["price_per_sqm"]) * 100, 2) if target["price_per_sqm"] else None
        rent_stats = {
            "avg_rent_per_sqm": round(avg_rent, 2),
            "median_rent_per_sqm": round(med_rent, 2),
            "estimated_monthly_rent": est_monthly,
            "gross_yield_pct": gross_yield,
        }

    # --- History ---
    history = get_listing_history(listing_id, listing_type if table == "sales" else "rent")

    conn.close()
    return {
        "radius_m": radius_m,
        "comparables": {
            "count": len(comparables),
            "stats": comp_stats,
            "listings": comparables[:20],
        },
        "rentals": {
            "count": len(rentals),
            "stats": rent_stats,
            "listings": rentals[:10],
        },
        "history": history,
    }


_STRIP_PREFIXES = re.compile(
    r'^(rua|avenida|av\.|travessa|tv\.|largo|praça|praca|beco|calçada|calc\.|estrada|estr\.)\s+',
    re.IGNORECASE
)

def _normalize_address(address: Optional[str]) -> Optional[str]:
    """Normalize a Portuguese address for fuzzy matching."""
    if not address:
        return None
    nfkd = unicodedata.normalize('NFKD', address)
    ascii_str = ''.join(c for c in nfkd if not unicodedata.combining(c))
    s = ascii_str.lower().strip()
    s = _STRIP_PREFIXES.sub('', s)
    s = re.sub(r'[,.\-/]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _address_similarity(a: str, b: str) -> float:
    """Simple similarity: ratio of common words."""
    if not a or not b:
        return 0.0
    wa = set(a.split())
    wb = set(b.split())
    if not wa or not wb:
        return 0.0
    common = wa & wb
    return len(common) / max(len(wa), len(wb))


def get_address_matches(listing_id: int, listing_type: str = "sale") -> List[dict]:
    """Find other listings at the same or similar address."""
    conn = get_connection()
    table = _table_for(listing_type)

    target = conn.execute(
        f"SELECT id, address, postal_code, lat, lon, hash_cross FROM {table} WHERE id=?",
        (listing_id,)
    ).fetchone()
    if not target:
        conn.close()
        return []

    norm_addr = _normalize_address(target["address"])
    results = {}  # keyed by (table, id) to dedupe

    # Tier 1: Same postal code prefix
    if target["postal_code"]:
        prefix = target["postal_code"][:4]
        for tbl in ("sales", "rentals"):
            lt = "rent" if tbl == "rentals" else "sale"
            rows = conn.execute(
                f"SELECT id, source, price_amount, price_per_sqm, status, address, scraped_at, postal_code "
                f"FROM {tbl} WHERE postal_code LIKE ? AND NOT (id=? AND ?=?)",
                (prefix + "%", listing_id, tbl, table)
            ).fetchall()
            for r in rows:
                r_norm = _normalize_address(r["address"])
                sim = _address_similarity(norm_addr, r_norm) if norm_addr and r_norm else 0
                if sim >= 0.6:
                    key = (tbl, r["id"])
                    if key not in results:
                        d = dict(r)
                        d["listing_type"] = lt
                        d["match_type"] = "postal_address"
                        d["similarity"] = round(sim, 2)
                        results[key] = d

    # Tier 2: Proximity (50m) + address match
    if target["lat"] and target["lon"]:
        dlat = 50 / 111000
        dlon = 50 / 87000
        for tbl in ("sales", "rentals"):
            lt = "rent" if tbl == "rentals" else "sale"
            rows = conn.execute(
                f"SELECT id, source, price_amount, price_per_sqm, status, address, scraped_at, lat, lon "
                f"FROM {tbl} WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND NOT (id=? AND ?=?)",
                (target["lat"] - dlat, target["lat"] + dlat,
                 target["lon"] - dlon, target["lon"] + dlon,
                 listing_id, tbl, table)
            ).fetchall()
            for r in rows:
                key = (tbl, r["id"])
                if key in results:
                    continue
                dist = _haversine(target["lat"], target["lon"], r["lat"], r["lon"])
                if dist <= 50:
                    r_norm = _normalize_address(r["address"])
                    sim = _address_similarity(norm_addr, r_norm) if norm_addr and r_norm else 0
                    if sim >= 0.4:
                        d = dict(r)
                        d["listing_type"] = lt
                        d["match_type"] = "proximity"
                        d["similarity"] = round(sim, 2)
                        d["distance_m"] = round(dist)
                        results[key] = d

    # Tier 3: Cross-hash match
    if target["hash_cross"]:
        for tbl in ("sales", "rentals"):
            lt = "rent" if tbl == "rentals" else "sale"
            rows = conn.execute(
                f"SELECT id, source, price_amount, price_per_sqm, status, address, scraped_at "
                f"FROM {tbl} WHERE hash_cross=? AND NOT (id=? AND ?=?)",
                (target["hash_cross"], listing_id, tbl, table)
            ).fetchall()
            for r in rows:
                key = (tbl, r["id"])
                if key not in results:
                    d = dict(r)
                    d["listing_type"] = lt
                    d["match_type"] = "cross_hash"
                    results[key] = d

    conn.close()
    matches = sorted(results.values(), key=lambda x: x.get("scraped_at", ""), reverse=True)
    return matches


# ── Neighborhoods ─────────────────────────────────────────────────────────────

def rebuild_neighborhoods():
    """Recompute neighborhood stats from sales + rentals tables."""
    conn = get_connection()

    # Gather distinct neighborhoods from both tables
    neighborhoods = conn.execute("""
        SELECT neighborhood, district FROM sales WHERE neighborhood IS NOT NULL
        UNION
        SELECT neighborhood, district FROM rentals WHERE neighborhood IS NOT NULL
    """).fetchall()

    updated = 0
    for n in neighborhoods:
        name = n["neighborhood"]
        district = n["district"] or "Lisboa"

        # Sale prices from the sales table
        prices = [
            r["price_amount"] for r in conn.execute(
                "SELECT price_amount FROM sales WHERE neighborhood=? AND price_amount IS NOT NULL",
                (name,)
            ).fetchall()
        ]
        psqm = [
            r["price_per_sqm"] for r in conn.execute(
                "SELECT price_per_sqm FROM sales WHERE neighborhood=? AND price_per_sqm IS NOT NULL",
                (name,)
            ).fetchall()
        ]
        # Rental prices from the rentals table
        rent_psqm = [
            r["price_per_sqm"] for r in conn.execute(
                "SELECT price_per_sqm FROM rentals WHERE neighborhood=? AND price_per_sqm IS NOT NULL",
                (name,)
            ).fetchall()
        ]
        # Sold listings from the sales table
        sold_psqm = [
            r["price_per_sqm"] for r in conn.execute(
                "SELECT price_per_sqm FROM sales WHERE neighborhood=? AND status='sold' AND price_per_sqm IS NOT NULL",
                (name,)
            ).fetchall()
        ]
        count_sold = len(sold_psqm)
        avg_sold_psqm = (sum(sold_psqm) / count_sold) if count_sold else None

        # Total count across both tables
        sale_count = conn.execute(
            "SELECT COUNT(*) as c FROM sales WHERE neighborhood=?", (name,)
        ).fetchone()["c"]
        rent_count = conn.execute(
            "SELECT COUNT(*) as c FROM rentals WHERE neighborhood=?", (name,)
        ).fetchone()["c"]
        count = sale_count + rent_count

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
      new_build_prox (0.05) – new-construction permit within ~400m of listing
    """
    conn = get_connection()

    # ── Per-neighborhood stats (sales table only) ────────────────────────────────
    nbhd_rows = conn.execute(
        "SELECT neighborhood FROM sales WHERE neighborhood IS NOT NULL GROUP BY neighborhood"
    ).fetchall()

    stats = {}
    for row in nbhd_rows:
        name = row["neighborhood"]

        # Price/sqm distribution (active sales)
        psqm_vals = [r["price_per_sqm"] for r in conn.execute(
            "SELECT price_per_sqm FROM sales WHERE neighborhood=? AND price_per_sqm IS NOT NULL AND status='active'",
            (name,)
        ).fetchall()]

        # Size distribution
        size_vals = [r["size_sqm"] for r in conn.execute(
            "SELECT size_sqm FROM sales WHERE neighborhood=? AND size_sqm IS NOT NULL AND status='active'",
            (name,)
        ).fetchall()]

        # Room count distribution
        rooms_rows = conn.execute(
            "SELECT rooms, COUNT(*) as c FROM sales WHERE neighborhood=? AND rooms IS NOT NULL AND status='active' GROUP BY rooms",
            (name,)
        ).fetchall()
        rooms_dist = {r["rooms"]: r["c"] for r in rooms_rows}
        rooms_total = sum(rooms_dist.values())

        # Condition distribution
        cond_rows = conn.execute(
            "SELECT condition, COUNT(*) as c FROM sales WHERE neighborhood=? AND condition IS NOT NULL AND status='active' GROUP BY condition",
            (name,)
        ).fetchall()
        cond_dist = {r["condition"]: r["c"] for r in cond_rows}
        cond_total = sum(cond_dist.values())

        # Property type distribution
        ptype_rows = conn.execute(
            "SELECT property_type, COUNT(*) as c FROM sales WHERE neighborhood=? AND property_type IS NOT NULL AND status='active' GROUP BY property_type",
            (name,)
        ).fetchall()
        ptype_dist = {r["property_type"]: r["c"] for r in ptype_rows}
        ptype_total = sum(ptype_dist.values())

        # Active listing count
        active_count = conn.execute(
            "SELECT COUNT(*) as c FROM sales WHERE neighborhood=? AND status='active'",
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

    # ── Compute and store (sales table only) ─────────────────────────────────────
    sale_rows = conn.execute(
        "SELECT id, neighborhood, price_per_sqm, size_sqm, rooms, condition, property_type, lat, lon FROM sales"
    ).fetchall()

    updated = 0
    for l in sale_rows:
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
            "vs_sold":        round(_vs_sold_score(l["price_per_sqm"], s["avg_sold"], "sale"), 3),
            "new_build_prox": round(1.0 if _near_new_build(l["lat"], l["lon"]) else 0.0, 3),
        }

        score = round(sum(W[k] * factors[k] for k in W) * 100, 1)

        conn.execute(
            "UPDATE sales SET rarity_score=?, rarity_factors=? WHERE id=?",
            (score, json.dumps(factors), l["id"])
        )
        updated += 1

    conn.commit()
    conn.close()
    logger.info(f"[DB] Computed rarity scores for {updated} sales listings")


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


# ── Sold Transactions ────────────────────────────────────────────────────────

def get_sold_trends(
    start_date: str,
    end_date: str,
) -> List[dict]:
    """
    Compute per-parish price trends for sold transactions within a date range.
    Splits the range in half: compares avg price/sqm in the first half vs second half.
    Returns a list of dicts with parish, pct_change, avg_early, avg_late, count, etc.
    """
    conn = get_connection()
    try:
        # Ensure the table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sold_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parish TEXT NOT NULL,
                lat REAL NOT NULL, lon REAL NOT NULL,
                price_amount REAL NOT NULL,
                price_per_sqm REAL NOT NULL,
                size_sqm REAL NOT NULL,
                rooms INTEGER, property_type TEXT,
                sold_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # Get midpoint of the date range
        from datetime import datetime as dt
        d_start = dt.strptime(start_date, "%Y-%m-%d")
        d_end = dt.strptime(end_date, "%Y-%m-%d")
        d_mid = d_start + (d_end - d_start) / 2
        mid_date = d_mid.strftime("%Y-%m-%d")

        # Get per-parish stats for early and late halves
        rows = conn.execute("""
            SELECT
                parish,
                AVG(CASE WHEN sold_date < ? THEN price_per_sqm END) AS avg_early,
                COUNT(CASE WHEN sold_date < ? THEN 1 END) AS count_early,
                AVG(CASE WHEN sold_date >= ? THEN price_per_sqm END) AS avg_late,
                COUNT(CASE WHEN sold_date >= ? THEN 1 END) AS count_late,
                COUNT(*) AS total_count,
                AVG(price_per_sqm) AS avg_price_per_sqm
            FROM sold_transactions
            WHERE sold_date >= ? AND sold_date <= ?
            GROUP BY parish
            HAVING count_early > 0 AND count_late > 0
        """, (mid_date, mid_date, mid_date, mid_date, start_date, end_date)).fetchall()

        results = []
        for r in rows:
            avg_early = r["avg_early"]
            avg_late = r["avg_late"]
            pct_change = ((avg_late - avg_early) / avg_early) * 100 if avg_early else 0
            results.append({
                "parish": r["parish"],
                "avg_early": round(avg_early, 2) if avg_early else None,
                "avg_late": round(avg_late, 2) if avg_late else None,
                "pct_change": round(pct_change, 1),
                "count": r["total_count"],
                "count_early": r["count_early"],
                "count_late": r["count_late"],
                "avg_price_per_sqm": round(r["avg_price_per_sqm"], 2),
            })

        return results
    finally:
        conn.close()


def get_sold_points(
    start_date: str,
    end_date: str,
) -> List[dict]:
    """Return individual sold transaction points within a date range."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sold_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parish TEXT NOT NULL,
                lat REAL NOT NULL, lon REAL NOT NULL,
                price_amount REAL NOT NULL,
                price_per_sqm REAL NOT NULL,
                size_sqm REAL NOT NULL,
                rooms INTEGER, property_type TEXT,
                sold_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        rows = conn.execute("""
            SELECT parish, lat, lon, price_amount, price_per_sqm,
                   size_sqm, rooms, property_type, sold_date
            FROM sold_transactions
            WHERE sold_date >= ? AND sold_date <= ?
            ORDER BY sold_date
        """, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Amenity ratings cache ────────────────────────────────────────────────────

def get_cached_amenity_rating(lat_key: float, lon_key: float) -> Optional[dict]:
    """Return cached rating or None if not found / expired (>30 days)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT overall_score, classification, categories_json, fetched_at "
            "FROM amenity_ratings WHERE lat_key=? AND lon_key=?",
            (lat_key, lon_key),
        ).fetchone()
        if not row:
            return None
        fetched = datetime.fromisoformat(row["fetched_at"])
        if datetime.utcnow() - fetched > timedelta(days=30):
            conn.execute(
                "DELETE FROM amenity_ratings WHERE lat_key=? AND lon_key=?",
                (lat_key, lon_key),
            )
            conn.commit()
            return None
        return {
            "overall_score": row["overall_score"],
            "classification": row["classification"],
            "categories": json.loads(row["categories_json"]),
        }
    finally:
        conn.close()


def cache_amenity_rating(lat_key: float, lon_key: float, rating: dict) -> None:
    """Insert or replace cached rating."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO amenity_ratings "
            "(lat_key, lon_key, overall_score, classification, categories_json, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                lat_key,
                lon_key,
                rating["overall_score"],
                rating["classification"],
                json.dumps(rating["categories"]),
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_active_sales_with_coords():
    """Return active sales listings with lat/lon for point-in-polygon matching."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT lat, lon, price_amount, price_per_sqm, size_sqm, rooms "
            "FROM sales WHERE status = 'active' AND lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_neighbourhood_typologies():
    """Room-count distribution per neighbourhood for active sales."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT neighborhood, rooms, COUNT(*) as cnt "
            "FROM sales "
            "WHERE neighborhood IS NOT NULL AND rooms IS NOT NULL AND status = 'active' "
            "GROUP BY neighborhood, rooms "
            "ORDER BY neighborhood, cnt DESC"
        ).fetchall()
    finally:
        conn.close()

    result = {}
    for r in rows:
        name = r["neighborhood"]
        if name not in result:
            result[name] = {"most_common_rooms": r["rooms"], "distribution": {}, "total": 0}
        result[name]["distribution"][str(r["rooms"])] = r["cnt"]
        result[name]["total"] += r["cnt"]
    return result


if __name__ == "__main__":
    init_db()
