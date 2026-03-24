"""
import_json.py — Import JSON listing exports into the Lisbon Real Estate DB.

Usage
─────
python import_json.py dataset.json

The JSON file should be a list of listing records (or {"items": [...]}).
Field schema:
  source, source_id, url, price_amount, price_per_sqm, size_sqm, rooms,
  bedrooms, floor, property_type, condition, title, address, postal_code,
  city, district, neighborhood, parish, lat, lon, images, hash_dedupe,
  description, scraped_at
"""

import sys
import json
import re
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import database as db
from models import Listing, ScrapeRun


# ── Normalisation ─────────────────────────────────────────────────────────────

def record_to_listing(rec: dict) -> Listing | None:
    """Convert a raw record dict to a Listing."""
    url = rec.get("url") or rec.get("detailUrl")
    if not url:
        return None

    source_id = rec.get("source_id") or rec.get("estateId") or _id_from_url(url)
    if not source_id:
        return None

    source = (rec.get("source") or "idealista").lower().replace(" ", "_")

    price_amount = _num(rec.get("price_amount") or rec.get("price") or rec.get("estatePrice"))

    area_raw = (
        rec.get("size_sqm")
        or rec.get("size")
        or rec.get("estateSize")
        or (rec.get("areaSize", {}).get("BRAtotal") if isinstance(rec.get("areaSize"), dict) else None)
    )
    size_sqm = _num(area_raw)

    rooms    = _int(rec.get("rooms") or rec.get("typology"))
    bedrooms = _int(rec.get("bedrooms") or rec.get("noOfBedRooms"))
    if bedrooms is None:
        bedrooms = rooms

    address      = rec.get("address") or rec.get("fullAddress")
    postal_code  = rec.get("postal_code") or rec.get("postalCode")
    neighborhood = rec.get("neighborhood")
    parish       = rec.get("parish")
    district     = rec.get("district") or rec.get("province") or "Lisboa"
    city         = rec.get("city") or rec.get("municipality") or "Lisboa"

    lat = _coord(rec.get("lat") or rec.get("latitude"))
    lon = _coord(rec.get("lon") or rec.get("longitude"))

    images_raw = rec.get("images")
    if isinstance(images_raw, list):
        images = json.dumps(images_raw)
    elif isinstance(images_raw, str) and images_raw.startswith("["):
        images = images_raw
    else:
        images = None

    price_per_sqm = _num(rec.get("price_per_sqm"))
    if price_per_sqm is None and price_amount and size_sqm and size_sqm > 0:
        price_per_sqm = round(price_amount / size_sqm, 2)

    scraped_raw = rec.get("scraped_at") or rec.get("scraped_at_utc")
    try:
        scraped_at = datetime.fromisoformat(scraped_raw.replace("Z", "+00:00")) if scraped_raw else datetime.utcnow()
    except (ValueError, AttributeError):
        scraped_at = datetime.utcnow()

    return Listing(
        source=source,
        source_id=str(source_id),
        url=url,
        price_amount=price_amount,
        price_per_sqm=price_per_sqm,
        size_sqm=size_sqm,
        rooms=rooms,
        bedrooms=bedrooms,
        bathrooms=_int(rec.get("bathrooms")),
        floor=str(rec.get("floor") or "") or None,
        property_type=rec.get("property_type"),
        condition=rec.get("condition"),
        title=rec.get("title"),
        address=address,
        postal_code=postal_code,
        neighborhood=neighborhood,
        parish=parish,
        district=district,
        city=city,
        lat=lat,
        lon=lon,
        images=images,
        hash_dedupe=rec.get("hash_dedupe"),
        description=rec.get("description"),
        scraped_at=scraped_at,
    )


# ── Import ────────────────────────────────────────────────────────────────────

def import_records(records: list[dict], source_label: str = "json_import") -> dict:
    """Import a list of records into the DB. Returns stats dict."""
    run_id = db.start_scrape_run(source_label)
    new_count = updated_count = error_count = 0

    for i, rec in enumerate(records, 1):
        try:
            listing = record_to_listing(rec)
            if listing is None:
                print(f"  [{i}] Skipped — missing url or source_id")
                error_count += 1
                continue

            _, is_new = db.upsert_listing(listing)
            if is_new:
                new_count += 1
            else:
                updated_count += 1

            if i % 100 == 0:
                print(f"  [{i}/{len(records)}] processed…")
        except Exception as e:
            error_count += 1
            print(f"  [{i}] ERROR: {e}")

    db.rebuild_neighborhoods()

    run = ScrapeRun(
        source=source_label,
        listings_found=len(records),
        listings_new=new_count,
        listings_updated=updated_count,
        errors=error_count,
        status="completed",
    )
    db.finish_scrape_run(run_id, run)

    return {
        "total": len(records),
        "new": new_count,
        "updated": updated_count,
        "errors": error_count,
    }


def import_from_file(path: str) -> dict:
    print(f"[import] Reading {path}…")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "items" in data:
        records = data["items"]
    elif isinstance(data, list):
        records = data
    else:
        raise ValueError("Unexpected JSON format. Expected a list or {\"items\": [...]}")
    print(f"[import] {len(records)} records loaded")
    return import_records(records, source_label=f"file:{Path(path).name}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _num(v):
    """Parse a Portuguese-formatted number (e.g. '250.000' = 250000)."""
    if v is None:
        return None
    try:
        s = str(v).strip()
        if re.search(r'\.\d{3}($|[^0-9])', s):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", ".")
        n = float(s)
        return n if n != 0 else None
    except (ValueError, AttributeError):
        return None


def _coord(v):
    if v is None:
        return None
    try:
        n = float(v)
        return n if -180 <= n <= 180 else None
    except (ValueError, TypeError):
        return None


def _int(v):
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (ValueError, AttributeError):
        return None


def _id_from_url(url: str) -> str | None:
    m = re.search(r"/imovel/(\d+)", url)
    if m:
        return m.group(1)
    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Import a JSON listing export into the Lisbon Real Estate DB"
    )
    parser.add_argument("file", help="Path to JSON export file")
    args = parser.parse_args()

    db.init_db()
    stats = import_from_file(args.file)

    print(f"\n✓ Import complete")
    print(f"  Total records : {stats['total']}")
    print(f"  New listings  : {stats['new']}")
    print(f"  Updated       : {stats['updated']}")
    print(f"  Errors        : {stats['errors']}")


if __name__ == "__main__":
    main()
