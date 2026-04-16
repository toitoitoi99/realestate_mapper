# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

- **Backend**: Python 3.9 (system `/usr/bin/python3`), Tornado web framework, SQLite (WAL mode)
- **Frontend**: React + Vite (port 3000), Tailwind CSS, react-leaflet
- **Scraping**: Playwright (Python) with `playwright-stealth` v2, persistent Chrome profile

## Commands

```bash
# One-time setup for a fresh clone or worktree (idempotent; auto-run by preview_start)
./scripts/bootstrap.sh                 # Python deps + Playwright chromium + frontend deps

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

## Preview

Use `preview_start("app")` to launch the full stack. The single `app` config in `.claude/launch.json` handles everything:

1. **Database**: In worktrees, copies the main repo's DB into the worktree if the local copy is missing or empty (<500KB). This gives each worktree an isolated snapshot of the data — scrapes in a worktree won't affect the main DB.
2. **Backend**: Dynamically finds a free port via `socket.bind(0)`, so multiple previews (main repo + any number of worktrees) never collide.
3. **Frontend**: Starts Vite with `BACKEND_PORT` env var pointing to the backend's dynamic port. The `vite.config.js` reads `process.env.BACKEND_PORT` (defaults to 8000 for manual `npm run dev`).
4. **Cleanup**: Backend subprocess is killed via `trap EXIT` when the preview stops.
5. **npm install**: Runs automatically if `node_modules/` is missing (common in fresh worktrees).
6. **PATH**: Exports `/usr/local/bin` so `node`/`npx` are found regardless of shell config.

Do NOT start `backend` and `frontend` as separate preview configs — the frontend proxy port must match the backend's dynamic port, which only the unified `app` config can coordinate.

### Known UI issues
- **Mobile layout**: At mobile viewport widths, the sidebar overlaps the map with no toggle. Not currently responsive.
- **Area switching**: When switching areas (e.g. AML → Porto), the map may not re-center correctly and the app title / search placeholder remain Lisbon-specific.
- **Backend root route**: The Tornado backend has no handler for `GET /` — health-check probes log 404 warnings. Not a functional issue.

## Visual regression — mandatory for UI tasks

**You MUST run the visual regression suite before declaring any UI task complete.** The suite lives at `tests/visual/` and the workflow is described in `.claude/skills/visual-regression/SKILL.md`.

Minimum flow after any change to `frontend/src/`:

1. `preview_start("app")` — confirm the app is running, note the URL.
2. `cd tests/visual && npm run check -- --url http://localhost:<port>`
3. Read `tests/visual/diffs/report.json`. For each non-`pass` state: either fix the code (regression) or promote the baseline with `node approve.mjs <name>` and explain why to the user.
4. In your final summary, state which baselines you updated and why.

The only excuse for skipping the suite is "the change is not browser-observable" (e.g. deleting unused CSS, editing comments) — and you must say so explicitly rather than silently skipping.

## Key constraints

- **Python 3.9**: No `str | None` union syntax — use `Optional[str]` from `typing`
- **playwright-stealth v2**: API is `Stealth().use_sync(page)`, not `stealth_sync(page)`
- **ArcGIS pagination**: Break only when `len(batch) < PAGE_SIZE`; don't rely on `exceededTransferLimit`
- **`/api/projects`**: Excludes `geometry` column and defaults to `limit=5000` (most recent) to keep response size manageable
- **DataDome**: Idealista blocks automated browsers; requires real Chrome + persistent profile + one-time manual challenge solve via `--setup`
