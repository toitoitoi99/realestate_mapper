"""Insert realistic dummy listings into the database for UI development."""
import random, sys
from pathlib import Path
from database import Database
from models import Listing

random.seed(42)

NEIGHBORHOODS = [
    ("Alfama",          38.7139, -9.1334),
    ("Mouraria",        38.7162, -9.1354),
    ("Baixa",           38.7128, -9.1395),
    ("Chiado",          38.7108, -9.1420),
    ("Bairro Alto",     38.7131, -9.1470),
    ("Príncipe Real",   38.7160, -9.1487),
    ("Estrela",         38.7109, -9.1600),
    ("Lapa",            38.7067, -9.1609),
    ("Alcântara",       38.6985, -9.1762),
    ("Belém",           38.6978, -9.2051),
    ("Restelo",         38.7044, -9.2013),
    ("Campo de Ourique",38.7138, -9.1617),
    ("Campolide",       38.7259, -9.1620),
    ("Amoreiras",       38.7230, -9.1570),
    ("Rato",            38.7192, -9.1519),
    ("Avenidas Novas",  38.7330, -9.1487),
    ("Alvalade",        38.7490, -9.1417),
    ("Roma",            38.7400, -9.1380),
    ("Arroios",         38.7260, -9.1340),
    ("Intendente",      38.7225, -9.1335),
    ("Beato",           38.7280, -9.1122),
    ("Marvila",         38.7365, -9.1087),
    ("Parque das Nações",38.7649,-9.0958),
    ("Olivais",         38.7630, -9.1110),
    ("Lumiar",          38.7720, -9.1530),
    ("Benfica",         38.7500, -9.1850),
    ("Carnide",         38.7680, -9.1850),
]

PROPERTY_TYPES = ["apartment", "house", "studio", "penthouse"]
CONDITIONS     = ["new", "used", "renovated"]

def rand_coord(lat, lon, spread=0.008):
    return lat + random.uniform(-spread, spread), lon + random.uniform(-spread, spread)

def make_listing(i, hood, base_lat, base_lon):
    is_rent    = random.random() < 0.30
    is_sold    = random.random() < 0.10 if not is_rent else False
    is_reserved= random.random() < 0.05 if not is_rent and not is_sold else False
    rooms      = random.choices([0,1,2,3,4,5], weights=[5,15,30,30,15,5])[0]
    size       = random.randint(35, 260)
    ptype      = random.choice(PROPERTY_TYPES)
    cond       = random.choice(CONDITIONS)
    lat, lon   = rand_coord(base_lat, base_lon)

    if is_rent:
        price = size * random.uniform(12, 25)
    else:
        base_sqm = random.uniform(3500, 12000)
        price = size * base_sqm

    price = round(price / 100) * 100
    ppsqm = round(price / size, 2)

    status = "sold" if is_sold else ("reserved" if is_reserved else "active")

    return Listing(
        source="dummy",
        source_id=f"dummy-{i:04d}",
        url=f"https://www.idealista.pt/imovel/dummy{i:04d}/",
        listing_type="rent" if is_rent else "sale",
        status=status,
        price_amount=price,
        price_per_sqm=ppsqm,
        size_sqm=float(size),
        rooms=rooms,
        bedrooms=rooms,
        property_type=ptype,
        condition=cond,
        title=f"T{rooms} em {hood}",
        address=f"Rua Dummy {i}, {hood}",
        neighborhood=hood,
        parish=hood,
        district="Lisboa",
        city="Lisboa",
        lat=lat,
        lon=lon,
        images="[]",
        hash_dedupe=f"dummy-{i:04d}",
    )

db = Database()

count = 0
for i, (hood, lat, lon) in enumerate(NEIGHBORHOODS):
    n = random.randint(8, 25)
    for j in range(n):
        listing = make_listing(count, hood, lat, lon)
        db.upsert_listing(listing)
        count += 1

print(f"Inserted {count} dummy listings across {len(NEIGHBORHOODS)} neighborhoods.")
