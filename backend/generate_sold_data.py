"""
Generate dummy sold property transaction data for Lisbon parishes.
Creates a sold_transactions table with ~600 transactions spread across
Jan 2024 – Mar 2026, with realistic price trends per parish.
"""

import random
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "lisbon_realestate.db"

# Parish center coordinates and base price/sqm (roughly realistic)
PARISHES = {
    "Misericórdia":          {"lat": 38.7130, "lon": -9.1450, "base_psqm": 5200, "trend": 0.06},
    "Santa Maria Maior":     {"lat": 38.7110, "lon": -9.1330, "base_psqm": 5800, "trend": 0.08},
    "São Vicente":           {"lat": 38.7160, "lon": -9.1280, "base_psqm": 4600, "trend": 0.05},
    "Santo António":         {"lat": 38.7200, "lon": -9.1470, "base_psqm": 5500, "trend": 0.07},
    "Belém":                 {"lat": 38.6970, "lon": -9.2060, "base_psqm": 4800, "trend": 0.04},
    "Ajuda":                 {"lat": 38.7080, "lon": -9.1950, "base_psqm": 3200, "trend": 0.03},
    "Alcântara":             {"lat": 38.7050, "lon": -9.1780, "base_psqm": 4200, "trend": 0.05},
    "Parque das Nações":     {"lat": 38.7680, "lon": -9.0960, "base_psqm": 4500, "trend": 0.06},
    "Avenidas Novas":        {"lat": 38.7370, "lon": -9.1530, "base_psqm": 5000, "trend": 0.05},
    "Alvalade":              {"lat": 38.7510, "lon": -9.1420, "base_psqm": 4300, "trend": 0.04},
    "Areeiro":               {"lat": 38.7400, "lon": -9.1330, "base_psqm": 4100, "trend": 0.05},
    "Campo de Ourique":      {"lat": 38.7180, "lon": -9.1620, "base_psqm": 4700, "trend": 0.06},
    "Estrela":               {"lat": 38.7130, "lon": -9.1600, "base_psqm": 5100, "trend": 0.07},
    "Campolide":             {"lat": 38.7290, "lon": -9.1660, "base_psqm": 3800, "trend": 0.04},
    "Arroios":               {"lat": 38.7260, "lon": -9.1350, "base_psqm": 4000, "trend": 0.06},
    "Penha de França":       {"lat": 38.7300, "lon": -9.1230, "base_psqm": 3500, "trend": 0.05},
    "Beato":                 {"lat": 38.7330, "lon": -9.1100, "base_psqm": 3000, "trend": 0.08},
    "Carnide":               {"lat": 38.7650, "lon": -9.1850, "base_psqm": 3100, "trend": 0.03},
    "Lumiar":                {"lat": 38.7730, "lon": -9.1650, "base_psqm": 3300, "trend": 0.04},
    "Santa Clara":           {"lat": 38.7870, "lon": -9.1500, "base_psqm": 2800, "trend": 0.03},
    "Olivais":               {"lat": 38.7700, "lon": -9.1100, "base_psqm": 3000, "trend": 0.04},
    "Benfica":               {"lat": 38.7520, "lon": -9.2000, "base_psqm": 3200, "trend": 0.03},
    "São Domingos de Benfica": {"lat": 38.7480, "lon": -9.1780, "base_psqm": 3600, "trend": 0.04},
    "Marvila":               {"lat": 38.7450, "lon": -9.1000, "base_psqm": 2900, "trend": 0.09},
}

# Reference date: Jan 1, 2024
REF_DATE = datetime(2024, 1, 1)
END_DATE = datetime(2026, 3, 1)
TOTAL_DAYS = (END_DATE - REF_DATE).days

PROPERTY_TYPES = ["apartment", "apartment", "apartment", "apartment", "house", "duplex"]
ROOM_CONFIGS = [
    (0, 25, 45),   # T0: 25-45 sqm
    (1, 35, 65),   # T1: 35-65 sqm
    (2, 55, 100),  # T2: 55-100 sqm
    (2, 55, 100),  # T2 again (more common)
    (3, 80, 140),  # T3: 80-140 sqm
    (4, 110, 200), # T4: 110-200 sqm
]


def create_table():
    """Create the sold_transactions table."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sold_transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            parish          TEXT NOT NULL,
            lat             REAL NOT NULL,
            lon             REAL NOT NULL,
            price_amount    REAL NOT NULL,
            price_per_sqm   REAL NOT NULL,
            size_sqm        REAL NOT NULL,
            rooms           INTEGER,
            property_type   TEXT,
            sold_date       TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sold_transactions_date
            ON sold_transactions(sold_date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sold_transactions_parish
            ON sold_transactions(parish)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sold_transactions_latlon
            ON sold_transactions(lat, lon)
    """)
    conn.commit()
    conn.close()
    logger.info("Created sold_transactions table")


def generate_transactions():
    """Generate ~600 dummy sold transactions."""
    random.seed(42)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    # Clear existing dummy data
    conn.execute("DELETE FROM sold_transactions")
    conn.commit()

    now = datetime.utcnow().isoformat()
    count = 0

    for parish, info in PARISHES.items():
        # 20-35 transactions per parish
        n_transactions = random.randint(20, 35)

        for _ in range(n_transactions):
            # Random date within range
            days_offset = random.randint(0, TOTAL_DAYS)
            sold_date = REF_DATE + timedelta(days=days_offset)

            # Time-based price trend: base_psqm * (1 + trend * years_elapsed)
            years_elapsed = days_offset / 365.0
            trend_multiplier = 1.0 + info["trend"] * years_elapsed

            # Pick room config
            rooms, min_sqm, max_sqm = random.choice(ROOM_CONFIGS)
            size_sqm = round(random.uniform(min_sqm, max_sqm), 1)

            # Price per sqm with noise (+/- 20%)
            noise = random.gauss(1.0, 0.10)
            price_per_sqm = round(info["base_psqm"] * trend_multiplier * noise, 2)
            price_amount = round(price_per_sqm * size_sqm, 0)

            # Scatter lat/lon around parish center
            lat = info["lat"] + random.gauss(0, 0.004)
            lon = info["lon"] + random.gauss(0, 0.004)

            property_type = random.choice(PROPERTY_TYPES)

            conn.execute("""
                INSERT INTO sold_transactions
                    (parish, lat, lon, price_amount, price_per_sqm, size_sqm,
                     rooms, property_type, sold_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parish, round(lat, 6), round(lon, 6),
                price_amount, price_per_sqm, size_sqm,
                rooms, property_type,
                sold_date.strftime("%Y-%m-%d"),
                now,
            ))
            count += 1

    conn.commit()
    conn.close()
    logger.info(f"Generated {count} sold transactions across {len(PARISHES)} parishes")


if __name__ == "__main__":
    create_table()
    generate_transactions()
