# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

- **Backend**: Python 3.9 (system `/usr/bin/python3`), Tornado web framework, SQLite (WAL mode)
- **Frontend**: React + Vite (port 3000), Tailwind CSS, react-leaflet
- **Scraping**: Playwright (Python) with `playwright-stealth` v2, persistent Chrome profile

## Commands

```bash
# Backend (from backend/)
python3 api.py                         # Start API server on port 8000

# Construction projects data (one-time fetch)
python3 scrapers/cml_projects.py       # Fetch ~45k projects from Lisbon open data

# Security POIs (one-time fetch, no auth required)
python3 scrapers/cml_security.py       # Fetch PSP stations + CCTV cameras from CML ArcGIS

# INE housing transaction prices (re-run periodically for new quarters)
python3 scrapers/ine_housing.py        # Fetch median sold price per m² for all 18 AML municipalities
python3 scrapers/ine_housing.py --show # Print latest prices table
python3 scrapers/ine_housing.py --geocod 1A01106  # Single municipality

# Idealista scraper (requires prior --setup with manual challenge solve)
python3 scrapers/idealista_playwright.py --setup   # One-time: open Chrome, solve DataDome challenge
python3 scrapers/idealista_playwright.py           # Headless scrape (after --setup)

# Frontend (from frontend/)
npm run dev                            # Start dev server on port 3000
npm run build                          # Production build
```

## Architecture

### Backend (`backend/`)
- `api.py` — Tornado app; routes: `/api/stats`, `/api/neighborhoods`, `/api/listings`, `/api/projects`, `/api/scrape`, `/api/areas`, `/api/parishes`
- `database.py` — SQLite wrapper; all DB access goes through `Database` class; WAL mode; `upsert_listing()`, `upsert_project()`
- `models.py` — `Listing` dataclass
- `scrapers/idealista_playwright.py` — Playwright scraper for idealista.pt; uses real Chrome (`channel="chrome"`) + persistent profile at `data/browser_profile/` to bypass DataDome
- `scrapers/cml_projects.py` — Fetches construction permits/applications from Lisbon open data ArcGIS API, computes polygon centroids

### Frontend (`frontend/src/`)
- `App.jsx` — Root; fetches listings, neighborhoods, projects; manages filter state and `visibleCategories`
- `api.js` — All `fetch()` calls; proxied via Vite to backend at `:8000`
- `projectCategories.js` — `CATEGORIES` map (8 types with colors) + `getCategory(operation)` classifier
- `components/Map.jsx` — MapContainer with listing markers and ProjectLayer
- `components/ProjectLayer.jsx` — Renders construction project CircleMarkers, filtered by `visibleCategories`
- `components/MapLegend.jsx` — Layer toggles with per-category counts
- `components/Sidebar.jsx` — Listing filters and results list

### Data sources
- **Listings**: idealista.pt (scraped via Playwright)
- **Construction projects**: `dados.cm-lisboa.pt` ArcGIS FeatureServer — two layers: `0` (issued permits), `1` (pending applications); paginated GeoJSON API
- **Security POIs**: `POISeguranca` FeatureServer (same ArcGIS org) — layers: `0` Municipal Police (1), `1` PSP stations (100), `2` CCTV cameras Bairro Alto (25)
- **INE sold prices**: INE JSON API (`pindica.jsp`), indicator `0012234`; covers all 18 AML municipalities (9 Grande Lisboa + 9 Península de Setúbal); quarterly median €/m²
- **Geographic scope**: Multi-area — configurable via `backend/areas.json` (Lisbon, AML, Porto, Algarve); area switcher in the UI; default area is AML centred at [38.68, -9.10] zoom 10

### Database (`backend/data/lisboa_realestate.db`)
- **`sales`** — Sale listings (main table); columns include `source`, `source_id`, `url`, `status`, `price_amount`, `price_per_sqm`, `size_sqm`, `rooms`, `neighborhood`, `parish`, `lat`, `lon`, `rarity_score`, `rarity_factors`, `building_geojson`, etc.
- **`rentals`** — Rental listings; same schema as `sales` minus rarity/building columns
- **`neighborhoods`** — Aggregated per-neighborhood stats (avg/median prices, counts)
- **`construction_projects`** — CML building permits/applications with centroid coords
- **`ine_stats`** — INE quarterly median sold prices per municipality
- **`security_pois`** — PSP stations and CCTV cameras
- **`amenity_ratings`** — Per-location amenity scores (green spaces, convenience, etc.)
- **`scrape_runs`** — Scraper execution history
- **`listing_history`** — Price/status change tracking
- **`sold_transactions`** — Detected sold properties

Note: The API (`database.py`) queries `sales` and `rentals` tables separately, not a unified `listings` table. The `/api/listings` endpoint unions them.

## Preview / Dev setup

### launch.json
The `.claude/launch.json` defines `backend` and `frontend` server configs for the preview module. Key gotchas:

- **Node.js PATH**: The preview shell doesn't inherit the user's full PATH. The frontend launch command must explicitly set `PATH=/usr/local/bin:$PATH` (or wherever `node`/`npx` live) in the bash command.
- **Port conflicts**: Port 8000 (backend) and 3000 (frontend) may already be in use (e.g. by the main repo when running in a worktree). Both configs use `autoPort: true` to handle this. The Vite proxy target must be dynamic — use `process.env.BACKEND_PORT || 8000` in `vite.config.js` and pass `BACKEND_PORT=<port>` in the frontend launch command.
- **Worktree setup**: Git worktrees don't share `node_modules`. After creating a worktree, you must run `cd frontend && npm install` before starting the frontend. The SQLite DB will also be empty — the app will show 0 listings until scrapers are run.

### Known UI issues
- **Mobile layout**: At mobile viewport widths, the sidebar overlaps the map with no toggle. Not currently responsive.
- **Area switching**: When switching areas (e.g. AML → Porto), the map may not re-center correctly and the app title / search placeholder remain Lisbon-specific.
- **Backend root route**: The Tornado backend has no handler for `GET /` — health-check probes log 404 warnings. Not a functional issue.

## Key constraints

- **Python 3.9**: No `str | None` union syntax — use `Optional[str]` from `typing`
- **playwright-stealth v2**: API is `Stealth().use_sync(page)`, not `stealth_sync(page)`
- **ArcGIS pagination**: Break only when `len(batch) < PAGE_SIZE`; don't rely on `exceededTransferLimit`
- **`/api/projects`**: Excludes `geometry` column and defaults to `limit=5000` (most recent) to keep response size manageable
- **DataDome**: Idealista blocks automated browsers; requires real Chrome + persistent profile + one-time manual challenge solve via `--setup`
- **Vite proxy port**: The proxy target in `vite.config.js` reads `process.env.BACKEND_PORT` (default 8000). When running backend on a non-standard port (worktrees, port conflicts), set `BACKEND_PORT` accordingly before starting the frontend.
