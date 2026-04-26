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
LOW_DENSITY_PATH = Path(__file__).parent / "data" / "low_density_territories.json"

# -- Grant eligibility: low-density territory lookup ---------------------------
_low_density_municipalities = set()  # normalized city names
_low_density_parishes = {}           # normalized municipality -> set of parish names
_grant_programs = []

def _normalize(s):
    """Normalize a string for fuzzy matching: lowercase, strip accents."""
    if not s:
        return ""
    s = s.lower().strip()
    # Decompose unicode and strip combining marks (accents)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s

def _load_low_density_territories():
    global _low_density_municipalities, _low_density_parishes, _grant_programs
    if _low_density_municipalities:
        return  # already loaded
    if not LOW_DENSITY_PATH.exists():
        logger.warning("low_density_territories.json not found")
        return
    data = json.loads(LOW_DENSITY_PATH.read_text(encoding="utf-8"))
    _grant_programs = data.get("programs", [])
    # Group 1: all parishes in municipality are low-density
    for district, municipalities in data.get("group_1_municipalities", {}).items():
        if district.startswith("_"):
            continue
        for m in municipalities:
            _low_density_municipalities.add(_normalize(m))
    # Group 2: only specific parishes
    for municipality, parishes in data.get("group_2_parishes", {}).items():
        if municipality.startswith("_"):
            continue
        norm_mun = _normalize(municipality)
        _low_density_parishes[norm_mun] = {_normalize(p) for p in parishes}

def is_grant_eligible(city, parish):
    """Check if a listing's city/parish falls in a low-density territory."""
    _load_low_density_territories()
    norm_city = _normalize(city)
    norm_parish = _normalize(parish)
    # Group 1: entire municipality is low-density
    if norm_city in _low_density_municipalities:
        return True
    # Group 2: check specific parishes within partially-classified municipalities
    if norm_city in _low_density_parishes:
        eligible_parishes = _low_density_parishes[norm_city]
        # Try exact match first, then substring match (parish names may be abbreviated)
        if norm_parish in eligible_parishes:
            return True
        for ep in eligible_parishes:
            if norm_parish and (norm_parish in ep or ep in norm_parish):
                return True
    return False

def get_grant_details(city, parish):
    """Return detailed grant program info for an eligible listing."""
    _load_low_density_territories()
    if not is_grant_eligible(city, parish):
        return None
    return {
        "in_low_density": True,
        "programs": _grant_programs,
    }

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


def _reclassify_rentals(conn):
    """Move listings that are clearly rentals from sales → rentals.

    Detection: low price (< 5000 €) combined with rental keywords in
    the URL or title (arrend*, alug*).  High-price listings mentioning
    'arrendamento' alongside 'venda' are left in sales.
    """
    rental_cols = [
        "source", "source_id", "url", "status", "price_amount",
        "price_per_sqm", "size_sqm", "gross_area_sqm", "rooms", "bedrooms",
        "bathrooms", "floor", "property_type", "condition", "title",
        "address", "postal_code", "neighborhood", "parish", "district",
        "city", "lat", "lon", "images", "hash_dedupe", "hash_cross",
        "hash_location", "missing_since", "previous_listing_id",
        "description", "scraped_at",
    ]
    # Only include columns that exist in rentals table
    existing = {r[1] for r in conn.execute("PRAGMA table_info(rentals)").fetchall()}
    cols = [c for c in rental_cols if c in existing]
    col_list = ", ".join(cols)

    where = """
        price_amount > 0 AND price_amount < 5000
        AND (
            url LIKE '%arrend%' OR url LIKE '%alug%'
            OR title LIKE '%arrend%' OR title LIKE '%alug%'
        )
    """
    moved = conn.execute(f"""
        INSERT OR IGNORE INTO rentals ({col_list})
        SELECT {col_list} FROM sales WHERE {where}
    """).rowcount
    if moved:
        conn.execute(f"DELETE FROM sales WHERE {where}")
        conn.commit()
        logger.info(f"[DB] Reclassified {moved} rental(s) from sales → rentals")


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

    # -- Reclassify misplaced rentals in the sales table ----------------------
    _reclassify_rentals(conn)

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
        if "deal_score" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN deal_score REAL")
            logger.info(f"[DB] Added deal_score column to {tbl}")
        if "property_score" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN property_score REAL")
            logger.info(f"[DB] Added property_score column to {tbl}")
        if "property_features" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN property_features TEXT")
            logger.info(f"[DB] Added property_features column to {tbl}")
        if "feature_chips" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN feature_chips TEXT")
            logger.info(f"[DB] Added feature_chips column to {tbl}")

        # -- Flip/Rent scoring engine columns (April 2026 redesign) ---------
        # Top-level outputs
        if "flip_score" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN flip_score REAL")
            logger.info(f"[DB] Added flip_score column to {tbl}")
        if "flip_factors" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN flip_factors TEXT")  # JSON
            logger.info(f"[DB] Added flip_factors column to {tbl}")
        if "rent_score" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN rent_score REAL")
            logger.info(f"[DB] Added rent_score column to {tbl}")
        if "rent_factors" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN rent_factors TEXT")  # JSON
            logger.info(f"[DB] Added rent_factors column to {tbl}")
        if "region_profile" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN region_profile TEXT")
            logger.info(f"[DB] Added region_profile column to {tbl}")
        if "reno_cost_estimate" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN reno_cost_estimate REAL")
            logger.info(f"[DB] Added reno_cost_estimate column to {tbl}")
        if "score_computed_at" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN score_computed_at TEXT")
            logger.info(f"[DB] Added score_computed_at column to {tbl}")

        # Cached signal values (avoid recomputing from raw sources each score)
        if "noise_score" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN noise_score REAL")
            logger.info(f"[DB] Added noise_score column to {tbl}")
        if "light_score" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN light_score REAL")
            logger.info(f"[DB] Added light_score column to {tbl}")
        if "layout_openness_score" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN layout_openness_score REAL")
            logger.info(f"[DB] Added layout_openness_score column to {tbl}")
        if "social_housing_adj_score" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN social_housing_adj_score REAL")
            logger.info(f"[DB] Added social_housing_adj_score column to {tbl}")
        if "dev_momentum_score" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN dev_momentum_score REAL")
            logger.info(f"[DB] Added dev_momentum_score column to {tbl}")

        # Text-extracted signals
        if "building_year" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN building_year INTEGER")
            logger.info(f"[DB] Added building_year column to {tbl}")
        if "orientation" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN orientation TEXT")
            logger.info(f"[DB] Added orientation column to {tbl}")
        if "condominium_fee" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN condominium_fee REAL")
            logger.info(f"[DB] Added condominium_fee column to {tbl}")
        if "energy_class" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN energy_class TEXT")
            logger.info(f"[DB] Added energy_class column to {tbl}")
        if "days_on_market" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN days_on_market INTEGER")
            logger.info(f"[DB] Added days_on_market column to {tbl}")
        if "price_drop_count" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN price_drop_count INTEGER")
            logger.info(f"[DB] Added price_drop_count column to {tbl}")

        # Vision enrichment (Phase 3) — stored as JSON blob
        if "photo_analysis" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN photo_analysis TEXT")
            logger.info(f"[DB] Added photo_analysis column to {tbl}")

        # Renovation classifier columns (PR #85)
        if "renovation_class" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN renovation_class TEXT")
            logger.info(f"[DB] Added renovation_class column to {tbl}")
        if "renovation_confidence" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN renovation_confidence REAL")
            logger.info(f"[DB] Added renovation_confidence column to {tbl}")
        if "renovation_cost_estimate_eur_per_sqm" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN renovation_cost_estimate_eur_per_sqm INTEGER")
            logger.info(f"[DB] Added renovation_cost_estimate_eur_per_sqm column to {tbl}")
        if "renovation_evidence" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN renovation_evidence TEXT")
            logger.info(f"[DB] Added renovation_evidence column to {tbl}")
        if "renovation_needs" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN renovation_needs TEXT")
            logger.info(f"[DB] Added renovation_needs column to {tbl}")
        if "renovation_classified_at" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN renovation_classified_at TEXT")
            logger.info(f"[DB] Added renovation_classified_at column to {tbl}")
        if "renovation_model" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN renovation_model TEXT")
            logger.info(f"[DB] Added renovation_model column to {tbl}")

        # building_stage: approved_project / full_remodel / needs_reno / turnkey (from description text)
        if "building_stage" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN building_stage TEXT")
            logger.info(f"[DB] Added building_stage column to {tbl}")

        # Photo style/feature tags (persona feature, April 2026) — populated by
        # backend/scorers/photo_tagger.py in the same Haiku call as renovation.
        if "style_primary" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN style_primary TEXT")
            logger.info(f"[DB] Added style_primary column to {tbl}")
        if "style_secondary" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN style_secondary TEXT")
            logger.info(f"[DB] Added style_secondary column to {tbl}")
        if "light_level" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN light_level TEXT")
            logger.info(f"[DB] Added light_level column to {tbl}")
        if "color_palette" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN color_palette TEXT")
            logger.info(f"[DB] Added color_palette column to {tbl}")
        if "outdoor_type" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN outdoor_type TEXT")
            logger.info(f"[DB] Added outdoor_type column to {tbl}")
        if "floor_material" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN floor_material TEXT")
            logger.info(f"[DB] Added floor_material column to {tbl}")
        if "standout_features" not in existing:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN standout_features TEXT")  # JSON array
            logger.info(f"[DB] Added standout_features column to {tbl}")
    conn.commit()

    # Classification for construction_projects (private_dev / public_dev / renovation / minor)
    proj_cols = {r[1] for r in conn.execute("PRAGMA table_info(construction_projects)").fetchall()}
    if "classification" not in proj_cols:
        conn.execute("ALTER TABLE construction_projects ADD COLUMN classification TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_classification "
                     "ON construction_projects(classification)")
        logger.info("[DB] Added classification column to construction_projects")
    conn.commit()

    # -- New signal-layer tables (Phase 1) --------------------------------------
    # Social housing estates — imported one-off from CML / municipal data.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS social_housing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            name TEXT,
            municipality TEXT,
            parish TEXT,
            category TEXT,            -- e.g. 'bairro_municipal', 'phr', 'private_social'
            lat REAL,
            lon REAL,
            polygon_geojson TEXT,     -- optional footprint
            fetched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_social_housing_coords
            ON social_housing(lat, lon);

        -- Major road / rail segments extracted from OSM, used for noise proxy.
        CREATE TABLE IF NOT EXISTS noise_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,     -- 'osm_road', 'osm_rail', 'airport_corridor'
            osm_id TEXT,
            kind TEXT,                -- road class ('motorway','trunk',...) / rail / airport
            geometry_geojson TEXT NOT NULL,
            fetched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_noise_sources_source ON noise_sources(source);

        -- User like/dislike + free-text note per listing.
        -- Single-user app: no user_id column.
        CREATE TABLE IF NOT EXISTS listing_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_kind TEXT NOT NULL,           -- 'sale' | 'rent'
            listing_id INTEGER NOT NULL,
            reaction TEXT NOT NULL,               -- 'like' | 'dislike'
            comment TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(listing_kind, listing_id)
        );
        CREATE INDEX IF NOT EXISTS idx_reactions_kind_id
            ON listing_reactions(listing_kind, listing_id);
        CREATE INDEX IF NOT EXISTS idx_reactions_reaction
            ON listing_reactions(reaction);

        -- Admin-only per-persona agreement/disagreement with the model's score
        -- for a given listing. One row per (listing, persona). Used to tune
        -- the ranking model — distinct from user `listing_reactions`.
        CREATE TABLE IF NOT EXISTS listing_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_kind TEXT NOT NULL,           -- 'sale' | 'rent'
            listing_id INTEGER NOT NULL,
            persona TEXT NOT NULL,                -- 'flip' | 'rent'
            agree TEXT NOT NULL,                  -- 'agree' | 'disagree'
            comment TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(listing_kind, listing_id, persona)
        );
        CREATE INDEX IF NOT EXISTS idx_ratings_kind_id
            ON listing_ratings(listing_kind, listing_id);
        CREATE INDEX IF NOT EXISTS idx_ratings_persona_agree
            ON listing_ratings(persona, agree);

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
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

    # Sanitise price sentinels and obviously corrupt values
    if listing.price_amount is not None:
        if listing.price_amount <= 0 or listing.price_amount > 500_000_000:
            listing.price_amount = None
            listing.price_per_sqm = None

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
                        missing_since=NULL, description=?, feature_chips=?, scraped_at=?
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
                    listing.description, listing.feature_chips,
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
                        hash_location, previous_listing_id, description, feature_chips, scraped_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    hl, prev_id, listing.description, listing.feature_chips, ts
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
    grant_eligible: Optional[bool] = None,
    min_deal_score: Optional[float] = None,
    min_rarity_score: Optional[float] = None,
    min_flip_score: Optional[float] = None,
    min_rent_score: Optional[float] = None,
    region_profile: Optional[str] = None,
    persona: Optional[str] = None,
    weights_override: Optional[dict] = None,
    bedrooms_min: Optional[int] = None,
    style_primary: Optional[str] = None,
    outdoor_required: bool = False,
    max_renovation: Optional[str] = None,
    strict_tags: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> List[dict]:
    table = _table_for(listing_type) if listing_type else None
    conn = get_connection()
    clauses, params = [], []

    if neighborhood:
        clauses.append("(neighborhood=? OR parish=?)"); params.extend([neighborhood, neighborhood])
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
    if min_deal_score is not None:
        clauses.append("deal_score>=?"); params.append(min_deal_score)
    if min_rarity_score is not None:
        clauses.append("rarity_score>=?"); params.append(min_rarity_score)
    if min_flip_score is not None:
        clauses.append("flip_score>=?"); params.append(min_flip_score)
    if min_rent_score is not None:
        clauses.append("rent_score>=?"); params.append(min_rent_score)
    if region_profile:
        clauses.append("region_profile=?"); params.append(region_profile)

    # --- Persona-preference filters ---
    # Default: NULL-tolerant — untagged listings stay visible until the photo
    # tagger reaches them. When `strict_tags` is set, drop the IS NULL escape
    # hatch so only listings we have positive evidence for show up. Useful
    # once tag coverage is high enough that "untagged" is the exception.
    if bedrooms_min is not None:
        clauses.append("bedrooms>=?"); params.append(bedrooms_min)
    if style_primary:
        if strict_tags:
            clauses.append("style_primary=?")
        else:
            clauses.append("(style_primary IS NULL OR style_primary=?)")
        params.append(style_primary)
    if outdoor_required:
        if strict_tags:
            # Must be explicitly tagged with a non-"none" outdoor type.
            clauses.append("(outdoor_type IS NOT NULL AND outdoor_type<>'none')")
        else:
            # Only exclude listings explicitly tagged as having no outdoor space.
            clauses.append("(outdoor_type IS NULL OR outdoor_type<>'none')")
    if max_renovation:
        # Allow turnkey, then cosmetic, then full_renovation in order.
        order = ["turnkey", "cosmetic", "full_renovation"]
        if max_renovation in order:
            allowed = order[: order.index(max_renovation) + 1]
            placeholders = ",".join("?" * len(allowed))
            if strict_tags:
                clauses.append(f"renovation_class IN ({placeholders})")
            else:
                clauses.append(f"(renovation_class IS NULL OR renovation_class IN ({placeholders}))")
            params.extend(allowed)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    # Shared columns for union queries (excludes sales-only rarity columns).
    # flip_factors/rent_factors are pulled so the persona engine can read the
    # bundled positives; they're shipped to the client as well so the
    # scorecard's signal panel has a fallback before its preview resolves.
    _shared_cols = (
        "id, source, source_id, url, status, price_amount, price_per_sqm, "
        "size_sqm, gross_area_sqm, rooms, bedrooms, bathrooms, floor, property_type, condition, "
        "title, address, postal_code, neighborhood, parish, district, city, "
        "lat, lon, images, hash_dedupe, hash_cross, hash_location, "
        "missing_since, previous_listing_id, description, scraped_at, "
        "flip_score, rent_score, flip_factors, rent_factors, "
        "region_profile, reno_cost_estimate, "
        "noise_score, light_score, layout_openness_score, "
        "social_housing_adj_score, dev_momentum_score, "
        "orientation, building_year, condominium_fee, energy_class, "
        "days_on_market, price_drop_count, photo_analysis, "
        "renovation_class, renovation_confidence, "
        "renovation_cost_estimate_eur_per_sqm, renovation_evidence, "
        "renovation_needs, building_stage, "
        # Photo style tags (persona feature) — needed client-side so cards
        # can render "Modern \u2713 / Terrace \u2713" match badges.
        "style_primary, style_secondary, light_level, color_palette, "
        "outdoor_type, floor_material, standout_features"
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
            f"SELECT {_shared_cols}, rarity_score, rarity_factors, building_geojson, deal_score, property_score, property_features, 'sale' as listing_type FROM sales {where} "
            f"UNION ALL "
            f"SELECT {_shared_cols}, NULL as rarity_score, NULL as rarity_factors, NULL as building_geojson, deal_score, property_score, property_features, 'rent' as listing_type FROM rentals {where} "
            f"ORDER BY scraped_at DESC LIMIT ? OFFSET ?",
            params + params + [limit, offset]
        ).fetchall()

    conn.close()
    _load_low_density_territories()

    # Compute per-listing persona_score if a known persona was passed.
    # Imported lazily so this module stays free of the dependency for callers
    # that don't use persona ranking.
    persona_compute = None
    if persona:
        from persona_engine import is_known_persona, compute_persona_score
        if is_known_persona(persona):
            persona_compute = compute_persona_score

    result = []
    for r in rows:
        row = dict(r)
        if not row.get("status"):
            row["status"] = "active"
        row["grant_eligible"] = is_grant_eligible(row.get("city"), row.get("parish"))
        if grant_eligible and not row["grant_eligible"]:
            continue
        if persona_compute is not None:
            row["persona_score"] = persona_compute(row, persona, weights_override=weights_override)
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



def get_setting(key: str, default=None):
    """Return the JSON-decoded value for `key`, or `default` if absent."""
    conn = get_connection()
    row = conn.execute("SELECT value_json FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row is None:
        return default
    try:
        return json.loads(row["value_json"])
    except (json.JSONDecodeError, TypeError):
        return default


def set_setting(key: str, value) -> dict:
    """Upsert a setting. `value` is JSON-serialized."""
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_at=excluded.updated_at
            """,
            (key, json.dumps(value), now),
        )
    conn.close()
    return {"key": key, "value": value, "updated_at": now}


def delete_setting(key: str) -> bool:
    conn = get_connection()
    with conn:
        cur = conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
    conn.close()
    return cur.rowcount > 0



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
        # flip_factors / rent_factors / reno_cost_estimate are needed so the
        # frontend can compute per-comparable persona KPIs (yield / margin /
        # lifestyle / €-per-m²-month) for the comparison sidebar.
        f"SELECT id, price_per_sqm, price_amount, size_sqm, property_type, bedrooms, "
        f"address, lat, lon, status, source, scraped_at, "
        f"flip_factors, rent_factors, reno_cost_estimate "
        f"FROM {table} WHERE {' AND '.join(where)}",
        params
    ).fetchall()

    # Haversine refinement
    comp_listing_type = "rent" if table == "rentals" else "sale"
    comparables = []
    for r in sales_rows:
        dist = _haversine(lat, lon, r["lat"], r["lon"])
        if dist <= radius_m:
            d = dict(r)
            d["distance_m"] = round(dist)
            d["listing_type"] = comp_listing_type
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

    # --- Cross-reference: nearby rentals (for sales) or nearby sales (for rentals) ---
    cross_table = "rentals" if table == "sales" else "sales"
    cross_rows = conn.execute(
        f"SELECT id, price_per_sqm, price_amount, size_sqm, bedrooms, address, lat, lon, source "
        f"FROM {cross_table} WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND price_per_sqm IS NOT NULL",
        (lat - dlat, lat + dlat, lon - dlon, lon + dlon)
    ).fetchall()

    cross_listings = []
    for r in cross_rows:
        dist = _haversine(lat, lon, r["lat"], r["lon"])
        if dist <= radius_m:
            d = dict(r)
            d["distance_m"] = round(dist)
            cross_listings.append(d)

    cross_listings.sort(key=lambda x: x["distance_m"])

    cross_psqm = [r["price_per_sqm"] for r in cross_listings if r["price_per_sqm"]]
    rent_stats = {}
    if cross_psqm:
        avg_cross = statistics.mean(cross_psqm)
        med_cross = statistics.median(cross_psqm)
        if table == "sales":
            # Viewing a sale: show nearby rental prices and yield = rent*12/sale_price
            est_monthly = round(avg_cross * (target["size_sqm"] or 0), 2)
            gross_yield = round((avg_cross * 12 / target["price_per_sqm"]) * 100, 2) if target["price_per_sqm"] else None
            rent_stats = {
                "avg_rent_per_sqm": round(avg_cross, 2),
                "median_rent_per_sqm": round(med_cross, 2),
                "estimated_monthly_rent": est_monthly,
                "gross_yield_pct": gross_yield,
            }
        else:
            # Viewing a rental: show nearby sale prices and yield = this_rent*12/avg_sale_price
            target_rent_psqm = target["price_per_sqm"]
            gross_yield = round((target_rent_psqm * 12 / avg_cross) * 100, 2) if avg_cross else None
            est_purchase = round(avg_cross * (target["size_sqm"] or 0), 2)
            rent_stats = {
                "avg_sale_per_sqm": round(avg_cross, 2),
                "median_sale_per_sqm": round(med_cross, 2),
                "estimated_purchase_price": est_purchase,
                "gross_yield_pct": gross_yield,
                "source": "sales",
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
            "count": len(cross_listings),
            "stats": rent_stats,
            "listings": cross_listings[:10],
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


def find_nearby_listings(lat, lon, radius_m=100, address=None, listing_type=None):
    # type: (float, float, int, Optional[str], Optional[str]) -> List[dict]
    """Find listings near a coordinate, optionally filtered by address similarity.

    Uses a two-pass strategy: first searches within radius_m, then if an address
    is provided and no results found, does a wider search (up to 1km) requiring
    high address similarity. This handles cases where geocoded coordinates point
    to one part of a long street but listings are on another part.
    """
    conn = get_connection()
    norm_addr = _normalize_address(address)

    tables = []
    if listing_type == "rent":
        tables = [("rentals", "rent")]
    elif listing_type == "sale":
        tables = [("sales", "sale")]
    else:
        tables = [("sales", "sale"), ("rentals", "rent")]

    select_cols = (
        "id, source, source_id, url, status, price_amount, price_per_sqm, "
        "size_sqm, rooms, bedrooms, property_type, condition, "
        "title, address, neighborhood, parish, lat, lon, scraped_at"
    )

    # Pass 1: proximity search within requested radius
    dlat = radius_m / 111000.0
    dlon = radius_m / 87000.0
    results = []
    for tbl, lt in tables:
        rows = conn.execute(
            f"SELECT {select_cols} FROM {tbl} "
            f"WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
            f"AND status='active'",
            (lat - dlat, lat + dlat, lon - dlon, lon + dlon)
        ).fetchall()
        for r in rows:
            dist = _haversine(lat, lon, r["lat"], r["lon"])
            if dist > radius_m:
                continue
            if norm_addr:
                r_norm = _normalize_address(r["address"])
                sim = _address_similarity(norm_addr, r_norm) if r_norm else 0
                if sim < 0.3:
                    continue
            d = dict(r)
            d["listing_type"] = lt
            d["distance_m"] = round(dist)
            results.append(d)

    # Pass 2: if no results and we have an address, widen to 1km but require
    # high address similarity (>=0.5). Handles long streets where the geocoded
    # point is far from the actual listing.
    if not results and norm_addr:
        wide_radius = 1000
        dlat_w = wide_radius / 111000.0
        dlon_w = wide_radius / 87000.0
        seen_ids = set()
        for tbl, lt in tables:
            rows = conn.execute(
                f"SELECT {select_cols} FROM {tbl} "
                f"WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
                f"AND status='active'",
                (lat - dlat_w, lat + dlat_w, lon - dlon_w, lon + dlon_w)
            ).fetchall()
            for r in rows:
                if r["id"] in seen_ids:
                    continue
                r_norm = _normalize_address(r["address"])
                sim = _address_similarity(norm_addr, r_norm) if r_norm else 0
                if sim < 0.5:
                    continue
                dist = _haversine(lat, lon, r["lat"], r["lon"])
                if dist > wide_radius:
                    continue
                seen_ids.add(r["id"])
                d = dict(r)
                d["listing_type"] = lt
                d["distance_m"] = round(dist)
                results.append(d)

    conn.close()
    results.sort(key=lambda x: x["distance_m"])
    return results


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
            "SELECT * FROM neighborhoods WHERE district=? ORDER BY name",
            (district,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM neighborhoods ORDER BY name"
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


# ── Deal Score ───────────────────────────────────────────────────────────────

def _deal_rating(score):
    """Letter rating for a 0-100 score."""
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def compute_deal_score(listing_id, listing_type="sale", radius_m=500):
    """
    Composite deal score (0-100) combining value, location, yield, scarcity, growth, and risk.
    Returns dict with deal_score, deal_rating, and per-dimension breakdown.
    """
    conn = get_connection()
    table = _table_for(listing_type)
    # Rentals table has no rarity columns — select them only for sales
    rarity_cols = "rarity_score, rarity_factors, " if table == "sales" else ""
    try:
        row = conn.execute(
            f"SELECT id, lat, lon, price_per_sqm, price_amount, size_sqm, "
            f"parish, neighborhood, {rarity_cols}"
            f"condition, scraped_at, property_type, bedrooms "
            f"FROM {table} WHERE id=?",
            (listing_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"error": "Listing not found"}
    if row["lat"] is None or row["lon"] is None:
        return {"error": "Listing missing coordinates"}

    lat, lon = row["lat"], row["lon"]
    price_psm = row["price_per_sqm"]

    # ── 1. VALUE (discount-to-market: how far below estimated fair value) ──
    # Tiered comparables: prefer same property type, fall back to all types
    prop_type = row["property_type"]
    bedrooms = row["bedrooms"]
    comparison = get_radius_comparison(listing_id, listing_type, radius_m,
                                       filter_property_type=prop_type)
    comp_count = 0
    comp_stats = {}
    comp_type_label = prop_type or "all"
    if "comparables" in comparison:
        comp_count = comparison["comparables"].get("count", 0)
        comp_stats = comparison["comparables"].get("stats", {})

    # If fewer than 5 same-type comps, widen to all property types
    if comp_count < 5:
        comparison_all = get_radius_comparison(listing_id, listing_type, radius_m)
        all_count = comparison_all.get("comparables", {}).get("count", 0)
        if all_count > comp_count:
            comparison = comparison_all
            comp_count = all_count
            comp_stats = comparison["comparables"].get("stats", {})
            comp_type_label = "all types"

    # Primary signal: discount vs local median price/sqm (comparables)
    discount_pct = None
    local_median = comp_stats.get("median_price_per_sqm")
    if local_median and price_psm and local_median > 0:
        discount_pct = ((local_median - price_psm) / local_median) * 100
        # Score: -20% above = 0, +20% below = 100 (linear)
        discount_component = max(0.0, min(100.0, (discount_pct + 20) / 40 * 100))
    else:
        discount_component = 50.0

    # Secondary signal: INE municipal benchmark (macro check)
    ine_component = 50.0
    ine_detail = ""
    conn2 = get_connection()
    try:
        ine_row = conn2.execute(
            "SELECT median_price_per_sqm FROM ine_stats "
            "WHERE is_latest=1 AND (category='Total' OR category LIKE '%otal%') "
            "ORDER BY median_price_per_sqm DESC LIMIT 1"
        ).fetchone()
    finally:
        conn2.close()

    if ine_row and ine_row["median_price_per_sqm"] and price_psm:
        ine_median = ine_row["median_price_per_sqm"]
        ine_diff_pct = ((price_psm - ine_median) / ine_median) * 100
        ine_component = max(0, min(100, (1 - (ine_diff_pct + 30) / 60) * 100))
        ine_detail = ", {:.0f}% vs INE median".format(ine_diff_pct)

    # Blend: 70% local discount, 30% INE benchmark
    value_score = discount_component * 0.7 + ine_component * 0.3

    if discount_pct is not None:
        type_note = " [vs {}]".format(comp_type_label) if comp_type_label != "all types" else ""
        if discount_pct > 0:
            value_detail = "{:.0f}% below median ({}/m\u00b2 vs {}{}){}".format(
                discount_pct, int(price_psm), int(local_median), type_note, ine_detail
            )
        else:
            value_detail = "{:.0f}% above median ({}/m\u00b2 vs {}{}){}".format(
                abs(discount_pct), int(price_psm), int(local_median), type_note, ine_detail
            )
    else:
        value_detail = "No nearby comparables" + ine_detail

    # ── 2. LOCATION (amenity score) ──
    from amenity_rating import get_amenity_rating
    amenity = get_amenity_rating(lat, lon)
    location_score = float(amenity.get("overall_score", 50))
    location_class = amenity.get("classification", "?")
    location_detail = "Class {} amenity area (score {:.0f})".format(location_class, location_score)

    # ── 3. YIELD (gross rental yield) ──
    yield_score = 0.0
    yield_detail = "N/A"
    if listing_type == "sale":
        rent_stats = comparison.get("rentals", {}).get("stats", {})
        gross_yield = rent_stats.get("gross_yield_pct")
        est_rent = rent_stats.get("estimated_monthly_rent")
        if gross_yield is not None:
            yield_score = min(100.0, (gross_yield / 6.0) * 100)
            yield_detail = "{:.1f}% gross yield".format(gross_yield)
            if est_rent:
                yield_detail += ", est. \u20ac{:,.0f}/mo rent".format(est_rent)
        else:
            yield_detail = "No nearby rentals for yield calc"

    # ── 4. SCARCITY (rarity score) ──
    # Rentals don't have rarity scores — use neutral default
    row_keys = row.keys() if hasattr(row, "keys") else []
    scarcity_score = float(row["rarity_score"] or 0) if "rarity_score" in row_keys else 50.0
    scarcity_detail = "Rarity score {:.0f}".format(scarcity_score) if "rarity_score" in row_keys else "N/A for rentals"
    factors_raw = row["rarity_factors"] if "rarity_factors" in row_keys else None
    if factors_raw:
        try:
            factors = json.loads(factors_raw) if isinstance(factors_raw, str) else factors_raw
            if factors:
                factor_labels = {
                    "price_dev": "unusual price",
                    "typology": "uncommon typology",
                    "size_dev": "unusual size",
                    "condition": "condition contrast",
                    "scarcity": "low supply",
                    "prop_type": "rare property type",
                    "vs_sold": "below sold prices",
                    "new_build_prox": "near new builds",
                }
                top_factor = max(factors.items(), key=lambda x: x[1])
                scarcity_detail += " \u2014 {}".format(factor_labels.get(top_factor[0], top_factor[0]))
        except (json.JSONDecodeError, TypeError):
            pass

    # ── 5. GROWTH (parish sold trend — 24-month lookback, min sample) ──
    growth_score = 50.0
    growth_detail = "No sold trend data"
    parish = row["parish"]
    if parish:
        # Use 24-month lookback for more robust trend
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
        trends = get_sold_trends(start_date, end_date)
        parish_trend = None
        for t in trends:
            if t["parish"] and parish.lower() in t["parish"].lower():
                parish_trend = t
                break
        if parish_trend and parish_trend["count"] >= 6:
            pct = parish_trend["pct_change"]
            growth_score = max(0, min(100, (pct + 10) / 20 * 100))
            growth_detail = "{:+.1f}% parish trend ({} txns, 24mo)".format(pct, parish_trend["count"])
        elif parish_trend and parish_trend["count"] < 6:
            # Insufficient sample — dampen toward neutral
            pct = parish_trend["pct_change"]
            dampened = pct * (parish_trend["count"] / 6.0)
            growth_score = max(0, min(100, (dampened + 10) / 20 * 100))
            growth_detail = "{:+.1f}% trend (low confidence, {} txns)".format(pct, parish_trend["count"])
        else:
            # Fallback: use INE municipal-level quarterly data
            conn_ine = get_connection()
            try:
                ine_rows = conn_ine.execute(
                    "SELECT median_price_per_sqm, period_label FROM ine_stats "
                    "WHERE (category='Total' OR category LIKE '%otal%') "
                    "AND median_price_per_sqm IS NOT NULL "
                    "ORDER BY period_label DESC LIMIT 4"
                ).fetchall()
            finally:
                conn_ine.close()
            if len(ine_rows) >= 2:
                latest = ine_rows[0]["median_price_per_sqm"]
                oldest = ine_rows[-1]["median_price_per_sqm"]
                if oldest and oldest > 0:
                    ine_pct = ((latest - oldest) / oldest) * 100
                    growth_score = max(0, min(100, (ine_pct + 10) / 20 * 100))
                    growth_detail = "{:+.1f}% INE municipal trend ({}>{})".format(
                        ine_pct, ine_rows[-1]["period_label"], ine_rows[0]["period_label"]
                    )

    # ── 6. RISK (red flags — inverted: 100 = no risk, 0 = high risk) ──
    risk_flags = []
    risk_penalty = 0  # accumulates 0-100 of penalty

    # 6a. Days on market — long time suggests overpricing or issues
    scraped_at = row["scraped_at"]
    days_on_market = None
    if scraped_at:
        try:
            first_seen = datetime.fromisoformat(scraped_at.replace("Z", "+00:00"))
            days_on_market = (datetime.now(first_seen.tzinfo) - first_seen).days
        except (ValueError, TypeError):
            try:
                first_seen = datetime.strptime(scraped_at[:10], "%Y-%m-%d")
                days_on_market = (datetime.now() - first_seen).days
            except (ValueError, TypeError):
                pass

    if days_on_market is not None:
        if days_on_market > 180:
            risk_penalty += 30
            risk_flags.append("{} days on market (stale)".format(days_on_market))
        elif days_on_market > 90:
            risk_penalty += 15
            risk_flags.append("{} days on market".format(days_on_market))

    # 6b. Price reductions — multiple cuts signal overpricing
    history = comparison.get("history", [])
    price_cuts = [h for h in history if h.get("field") == "price_amount"
                  and h.get("old_value") and h.get("new_value")]
    cut_count = 0
    total_cut_pct = 0
    for h in price_cuts:
        try:
            old_val = float(h["old_value"])
            new_val = float(h["new_value"])
            if new_val < old_val:
                cut_count += 1
                total_cut_pct += ((old_val - new_val) / old_val) * 100
        except (ValueError, TypeError):
            pass

    if cut_count >= 3:
        risk_penalty += 25
        risk_flags.append("{} price cuts ({:.0f}% total reduction)".format(cut_count, total_cut_pct))
    elif cut_count >= 1:
        risk_penalty += 10
        risk_flags.append("{} price cut(s) ({:.0f}% reduction)".format(cut_count, total_cut_pct))

    # 6c. Overpriced vs comparables — significantly above local median
    if discount_pct is not None and discount_pct < -15:
        overprice = abs(discount_pct)
        risk_penalty += min(30, overprice)
        risk_flags.append("{:.0f}% above local median".format(overprice))

    # 6d. Low comparable count — thin market means uncertain valuation
    if comp_count < 5:
        risk_penalty += 10
        risk_flags.append("Few comparables ({})".format(comp_count))

    risk_score = max(0.0, 100.0 - risk_penalty)
    if risk_flags:
        risk_detail = "; ".join(risk_flags)
    else:
        risk_detail = "No red flags detected"

    # ── Composite ──
    if listing_type == "sale":
        weights = {"value": 25, "location": 20, "yield": 20, "scarcity": 10, "growth": 10, "risk": 15}
    else:
        weights = {"value": 30, "location": 25, "yield": 0, "scarcity": 15, "growth": 15, "risk": 15}

    scores = {
        "value": value_score,
        "location": location_score,
        "yield": yield_score,
        "scarcity": scarcity_score,
        "growth": growth_score,
        "risk": risk_score,
    }

    total_weight = sum(weights.values())
    deal_score = sum(scores[k] * weights[k] for k in scores) / total_weight

    details = {
        "value": value_detail,
        "location": location_detail,
        "yield": yield_detail,
        "scarcity": scarcity_detail,
        "growth": growth_detail,
        "risk": risk_detail,
    }

    dim_keys = ["value", "location", "yield", "scarcity", "growth", "risk"]
    dimensions = {}
    for k in dim_keys:
        s = round(scores[k], 1)
        dimensions[k] = {
            "score": s,
            "rating": _deal_rating(s),
            "weight": weights[k],
            "detail": details[k],
        }

    return {
        "deal_score": round(deal_score, 1),
        "deal_rating": _deal_rating(deal_score),
        "dimensions": dimensions,
        "comparables_count": comp_count,
        "listing_type": listing_type,
    }


def compute_all_deal_scores():
    """Batch-compute and store deal_score for all active listings with coordinates."""
    conn = get_connection()
    try:
        sales_rows = conn.execute(
            "SELECT id FROM sales WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "AND (status='active' OR status IS NULL)"
        ).fetchall()
        rental_rows = conn.execute(
            "SELECT id FROM rentals WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "AND (status='active' OR status IS NULL)"
        ).fetchall()
    finally:
        conn.close()

    updated = 0
    for row in sales_rows:
        try:
            result = compute_deal_score(row["id"], "sale", 500)
            if "deal_score" in result:
                c = get_connection()
                try:
                    c.execute("UPDATE sales SET deal_score=? WHERE id=?",
                              (result["deal_score"], row["id"]))
                    c.commit()
                    updated += 1
                finally:
                    c.close()
        except Exception as e:
            logger.debug("Deal score failed for sale %s: %s", row["id"], e)

    for row in rental_rows:
        try:
            result = compute_deal_score(row["id"], "rent", 500)
            if "deal_score" in result:
                c = get_connection()
                try:
                    c.execute("UPDATE rentals SET deal_score=? WHERE id=?",
                              (result["deal_score"], row["id"]))
                    c.commit()
                    updated += 1
                finally:
                    c.close()
        except Exception as e:
            logger.debug("Deal score failed for rental %s: %s", row["id"], e)

    logger.info("[DB] Computed deal scores for %d listings", updated)
    return updated


# ── Property Feature Score ──────────────────────────────────────────────────

# Each feature: (key, patterns, apt_weight, house_weight)
# Patterns are checked case-insensitively against description + title.
# Scoring model: score = min(100, sum of detected feature weights).
# Weights are absolute points (not percentages), calibrated so a well-equipped
# listing reaches ~80-100 and a bare listing scores ~0-20.
_PROPERTY_FEATURES = [
    ("outdoor_space", [r"\bvaranda\b", r"\bvarandas\b", r"\bmarquise\b", r"\bbalcony\b",
                       r"\bterra[cç]o\b", r"\brooftop\b", r"\bterrace\b", r"\bterra[cç]os?\b"], 20, 12),
    ("elevator",      [r"\belevador\b", r"\bascensor\b", r"\belevator\b", r"\blift\b"], 18, 0),
    ("parking",       [r"\bgarage[ms]?\b", r"\bestacionamento\b", r"\blugar de garagem\b",
                       r"\bparqueamento\b", r"\bparking\b", r"\bbox\b"], 18, 20),
    ("view",          [r"\bvista rio\b", r"\bvista mar\b", r"\bvista cidade\b",
                       r"\bpanor[aâ]mic[oa]\b", r"\briver view\b", r"\bsea view\b",
                       r"\bvista desafogada\b", r"\bvista frontal\b"], 18, 18),
    ("pool",          [r"\bpiscina\b", r"\bpool\b", r"\bswimming\b"], 12, 20),
    ("air_cond",      [r"\bar condicionado\b", r"\bclimatiza[çc][aã]o\b",
                       r"\bair\s*condition", r"\bac\s*instalado\b"], 12, 12),
    ("garden",        [r"\bjardim\b", r"\bquintal\b", r"\blogradouro\b", r"\bgarden\b"], 8, 20),
    ("renovated",     [r"\bremodelad[oa]\b", r"\brenovad[oa]\b", r"\breconstru[ií]d[oa]\b",
                       r"\brenovated\b", r"\brefurbished\b", r"\btotalmente novo\b"], 5, 5),
    ("energy_a_b_c",  [r"\bclasse energ[eé]tica\s*[abc]\b", r"\bcertificado\s*[abc]\b",
                       r"\benergy\s*(class|rating)\s*[abc]\b", r"\bclasse\s*[abc][+]?\b",
                       r"energ[eé]tica[:\s]+[abc][+-]?\b",
                       r"energ[eé]tico\s+classe\s+[abc]\b",
                       r"efici[eê]ncia\s+energ[eé]tica\s+classe\s+[abc]\b"], 5, 5),
    ("storage",       [r"\barreca?da[çc][aã]o\b", r"\barrumos?\b", r"\bstorage\b"], 2, 2),
    ("suite",         [r"\bsu[ií]te\b", r"\ben\s*suite\b", r"\bensuite\b"], 2, 2),
]

# Compiled regex for each feature
_FEATURE_PATTERNS = [
    (key, [re.compile(p, re.IGNORECASE) for p in pats], aw, hw)
    for key, pats, aw, hw in _PROPERTY_FEATURES
]


def _normalize_text(text):
    """Normalize accented characters for more robust matching."""
    if not text:
        return ""
    # NFD decomposition then strip combining marks — gives us base characters
    # but we also keep the original for accent-specific patterns
    return text.lower()


_NEGATION_RE = re.compile(r"\b(sem|n[aã]o\s+(tem|possui|dispõe|dispoe)|inexistente|no)\b", re.IGNORECASE)


def extract_property_features(description, title=None, property_type=None, condition=None,
                               bathrooms=None, floor=None, feature_chips=None):
    """Extract features from listing text and structured fields.

    Returns (score, features_dict) where features_dict maps feature_key → True/False
    and score is 0-100.

    `feature_chips` is an optional list of short structured strings from the source
    site (e.g. ["Varanda", "Elevador", "Ar condicionado"]). Chips are matched per-
    chip so negated chips ("Sem elevador") can override positive matches, and
    chip matches are tried independently of the marketing-blob description.
    """
    text = " ".join(filter(None, [title or "", description or ""]))
    text_lower = text.lower()

    # Check both property_type field AND title for house indicators
    _house_kw = ("house", "moradia", "villa", "vivenda", "quinta", "moradia independente",
                 "moradia geminada", "moradia isolada")
    type_lower = (property_type or "").lower()
    title_lower = (title or "").lower()
    is_house = any(k in type_lower for k in _house_kw) or any(k in title_lower for k in _house_kw)

    # Normalize chips → list of lowercased strings; classify each as positive/negative
    pos_chips: list = []
    neg_chips: list = []
    if feature_chips:
        for raw in feature_chips:
            if not raw:
                continue
            c = str(raw).strip().lower()
            if not c:
                continue
            if _NEGATION_RE.search(c):
                neg_chips.append(c)
            else:
                pos_chips.append(c)

    detected = {}
    for key, patterns, apt_w, house_w in _FEATURE_PATTERNS:
        # Match against free-text description/title
        found = any(p.search(text_lower) for p in patterns)
        # Match against positive chips (structured data wins over negated description text)
        if not found and pos_chips:
            found = any(p.search(chip) for chip in pos_chips for p in patterns)
        # Explicit negation in chips overrides any positive hit
        if found and neg_chips:
            if any(p.search(chip) for chip in neg_chips for p in patterns):
                found = False
        detected[key] = found

    # Bonus: condition field says "new" → count as renovated if not already
    if condition and condition.lower() in ("new", "novo", "nova"):
        detected["renovated"] = True

    # Bonus: multiple bathrooms
    detected["multi_bath"] = (bathrooms or 0) >= 2

    # Bonus: high floor with elevator (apartments only)
    high_floor = False
    if floor and not is_house:
        floor_str = str(floor).lower()
        m = re.search(r'(\d+)', floor_str)
        if m and int(m.group(1)) >= 3:
            high_floor = True
        if "último" in floor_str or "last" in floor_str or "ultimo" in floor_str:
            high_floor = True
    detected["high_floor_elevator"] = high_floor and detected.get("elevator", False)

    # Compute score as capped sum of absolute weights (not percentage of total).
    # This rewards well-equipped listings instead of penalising ones where
    # the description simply doesn't enumerate every possible feature.
    bonus_weights_apt = {"multi_bath": 10, "high_floor_elevator": 8}
    bonus_weights_house = {"multi_bath": 10, "high_floor_elevator": 0}

    earned = 0
    for key, _, apt_w, house_w in _FEATURE_PATTERNS:
        w = house_w if is_house else apt_w
        if detected.get(key):
            earned += w
    bw = bonus_weights_house if is_house else bonus_weights_apt
    for bkey, bweight in bw.items():
        if detected.get(bkey):
            earned += bweight

    score = min(100, round(earned, 1))

    # Build features list (only detected ones)
    features_found = [k for k, v in detected.items() if v]

    return score, features_found


def compute_property_score(listing_id, listing_type="sale"):
    """Compute and return property feature score for a single listing."""
    table = _table_for(listing_type)
    conn = get_connection()
    row = conn.execute(
        f"SELECT id, description, title, property_type, condition, bathrooms, floor, feature_chips "
        f"FROM {table} WHERE id=?", (listing_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"error": "Listing not found"}

    desc = row["description"] or ""
    title = row["title"] or ""

    # Parse structured feature chips if present
    chips = None
    raw_chips = row["feature_chips"]
    if raw_chips:
        try:
            parsed = json.loads(raw_chips)
            if isinstance(parsed, list):
                chips = [str(c) for c in parsed if c]
        except (json.JSONDecodeError, TypeError):
            chips = None

    # Skip only if we have no text AND no chips to analyze
    if len(desc.strip()) < 20 and len(title.strip()) < 5 and not chips:
        return {
            "property_score": None,
            "property_rating": None,
            "features": [],
            "note": "Insufficient description text"
        }

    score, features = extract_property_features(
        desc, title, row["property_type"], row["condition"],
        row["bathrooms"], row["floor"], feature_chips=chips
    )

    _house_kw = ("house", "moradia", "villa", "vivenda", "quinta", "moradia independente",
                 "moradia geminada", "moradia isolada")
    type_lower = (row["property_type"] or "").lower()
    title_lower = (row["title"] or "").lower()
    is_house = any(k in type_lower for k in _house_kw) or any(k in title_lower for k in _house_kw)

    rating = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"

    # Feature details with labels and weights
    feature_labels = {
        "outdoor_space": "Outdoor space", "elevator": "Elevator",
        "parking": "Parking", "storage": "Storage room", "pool": "Pool",
        "garden": "Garden", "view": "View", "energy_a_b_c": "Energy A/B/C",
        "renovated": "Renovated/New", "air_cond": "A/C", "suite": "Suite",
        "multi_bath": "2+ Bathrooms", "high_floor_elevator": "High floor + elevator",
    }
    feature_details = []
    for key, _, apt_w, house_w in _PROPERTY_FEATURES:
        w = house_w if is_house else apt_w
        if w == 0:
            continue  # not applicable for this property type
        feature_details.append({
            "key": key,
            "label": feature_labels.get(key, key),
            "detected": key in features,
            "weight": w,
        })
    # Add bonus features
    bonus_map = [
        ("multi_bath", 10, 10),
        ("high_floor_elevator", 8, 0),
    ]
    for bkey, apt_bw, house_bw in bonus_map:
        bw = house_bw if is_house else apt_bw
        if bw == 0:
            continue
        feature_details.append({
            "key": bkey,
            "label": feature_labels.get(bkey, bkey),
            "detected": bkey in features,
            "weight": bw,
        })

    return {
        "property_score": score,
        "property_rating": rating,
        "property_type_class": "house" if is_house else "apartment",
        "features": feature_details,
        "detected_count": len(features),
        "total_features": len(feature_details),
    }


def compute_all_property_scores():
    """Batch-compute and store property_score for all active listings."""
    conn = get_connection()
    updated = 0

    for tbl, lt in [("sales", "sale"), ("rentals", "rent")]:
        rows = conn.execute(
            f"SELECT id FROM {tbl} WHERE status='active' AND description IS NOT NULL"
        ).fetchall()
        for row in rows:
            try:
                result = compute_property_score(row["id"], lt)
                if result.get("property_score") is not None:
                    features_json = json.dumps(result["features"])
                    c = get_connection()
                    try:
                        c.execute(
                            f"UPDATE {tbl} SET property_score=?, property_features=? WHERE id=?",
                            (result["property_score"], features_json, row["id"])
                        )
                        c.commit()
                        updated += 1
                    finally:
                        c.close()
            except Exception as e:
                logger.debug("Property score failed for %s %s: %s", tbl, row["id"], e)

    conn.close()
    logger.info("[DB] Computed property scores for %d listings", updated)
    return updated


if __name__ == "__main__":
    init_db()
