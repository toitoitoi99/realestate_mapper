"""
Data models for the Lisbon Real Estate app.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Listing:
    """A single real estate listing."""

    # Identity
    source: str                         # e.g. "idealista", "imovirtual", "era"
    source_id: str                      # Listing ID on source site (from URL)
    url: str                            # Full listing URL
    listing_type: str = 'sale'          # 'sale' | 'rent'
    status: str = 'active'             # 'active' | 'sold' | 'reserved'

    # Price
    price_amount: Optional[float] = None    # Asking price in EUR
    price_per_sqm: Optional[float] = None   # EUR / m² (computed)

    # Property details
    size_sqm: Optional[float] = None        # Living area in m² (área útil)
    gross_area_sqm: Optional[float] = None  # Total surface area in m² (área bruta)
    rooms: Optional[int] = None             # Typology number (T0=0, T1=1, etc.)
    bedrooms: Optional[int] = None          # Bedrooms (may differ from rooms in PT)
    bathrooms: Optional[int] = None
    floor: Optional[str] = None             # Floor level (e.g. "3", "RC", "último")
    property_type: Optional[str] = None     # apartment, house, studio, etc.
    condition: Optional[str] = None         # new, used, renovated

    # Location
    title: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None
    neighborhood: Optional[str] = None     # Bairro (e.g. "Alfama", "Chiado")
    parish: Optional[str] = None           # Freguesia
    district: Optional[str] = None         # e.g. "Lisboa"
    city: Optional[str] = None             # e.g. "Lisboa"
    lat: Optional[float] = None
    lon: Optional[float] = None

    # Media
    images: Optional[str] = None           # JSON array string of image URLs

    # Deduplication (SHA-1 of address+city+price+size+source)
    hash_dedupe: Optional[str] = None

    # Cross-site matching (SHA-1 of address+city+price+size, no source)
    hash_cross: Optional[str] = None

    # Metadata
    description: Optional[str] = None
    scraped_at: datetime = field(default_factory=datetime.utcnow)

    # DB primary key (set after insert)
    id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "source_id": self.source_id,
            "url": self.url,
            "listing_type": self.listing_type,
            "status": self.status,
            "price_amount": self.price_amount,
            "price_per_sqm": self.price_per_sqm,
            "size_sqm": self.size_sqm,
            "gross_area_sqm": self.gross_area_sqm,
            "rooms": self.rooms,
            "bedrooms": self.bedrooms,
            "bathrooms": self.bathrooms,
            "floor": self.floor,
            "property_type": self.property_type,
            "condition": self.condition,
            "title": self.title,
            "address": self.address,
            "postal_code": self.postal_code,
            "neighborhood": self.neighborhood,
            "parish": self.parish,
            "district": self.district,
            "city": self.city,
            "lat": self.lat,
            "lon": self.lon,
            "images": self.images,
            "hash_dedupe": self.hash_dedupe,
            "hash_cross": self.hash_cross,
            "description": self.description,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
        }


@dataclass
class Neighborhood:
    """Aggregated price stats for a named neighborhood or parish."""

    name: str
    district: str
    listing_count: int = 0
    avg_price: Optional[float] = None
    median_price: Optional[float] = None
    avg_price_per_sqm: Optional[float] = None
    median_price_per_sqm: Optional[float] = None
    min_price_per_sqm: Optional[float] = None
    max_price_per_sqm: Optional[float] = None

    # GeoJSON polygon string (for map overlay)
    geometry: Optional[str] = None

    updated_at: datetime = field(default_factory=datetime.utcnow)
    id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "district": self.district,
            "listing_count": self.listing_count,
            "avg_price": self.avg_price,
            "median_price": self.median_price,
            "avg_price_per_sqm": self.avg_price_per_sqm,
            "median_price_per_sqm": self.median_price_per_sqm,
            "min_price_per_sqm": self.min_price_per_sqm,
            "max_price_per_sqm": self.max_price_per_sqm,
            "geometry": self.geometry,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class ScrapeRun:
    """Audit record for each scrape job executed."""

    source: str
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    listings_found: int = 0
    listings_new: int = 0
    listings_updated: int = 0
    errors: int = 0
    status: str = "running"     # running | completed | failed
    notes: Optional[str] = None
    id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "listings_found": self.listings_found,
            "listings_new": self.listings_new,
            "listings_updated": self.listings_updated,
            "errors": self.errors,
            "status": self.status,
            "notes": self.notes,
        }
