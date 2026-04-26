# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Design system

Read `DESIGN.md` before any UI work. It is the authoritative source for colors, typography, spacing, border radius, shadow, and component patterns. Do not introduce new visual values without checking it first.

## Stack

- **Backend**: Python 3.9 (system `/usr/bin/python3`), Tornado web framework, SQLite (WAL mode)
- **Frontend**: React + Vite (port 3000), Tailwind CSS, react-leaflet
- **Scraping**: Playwright (Python) with `playwright-stealth` v2, persistent Chrome profile
- **Auth / user data**: Supabase (Postgres) — user sessions, swipes, reactions, saved searches (see "Supabase" section below)
- **AI scoring**: Anthropic Claude API (`ANTHROPIC_API_KEY`) — photo tagging, renovation classification, style extraction, preference extraction, listing comparison

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
- `api.py` — Tornado app; full route list: `/api/listings`, `/api/listings/:id`, `/api/listings/:id/score-preview`, `/api/listings/:id/compare`, `/api/listings/:id/address-history`, `/api/neighborhoods`, `/api/neighborhoods/:name`, `/api/scrape-runs`, `/api/scrape`, `/api/score`, `/api/projects`, `/api/security`, `/api/ine-stats`, `/api/stats`, `/api/sold-trends`, `/api/amenity-rating`, `/api/areas`, `/api/parishes`, `/api/parish-stats`, `/api/translate`, `/api/neighbourhood-typologies`, `/api/nearby-projects`, `/api/address-lookup`, `/api/compare-listings`, `/api/extract-image-tags`, `/api/extract-preferences`, `/api/preference-deck`, `/api/reactions`, `/api/reactions/:type/:id`, `/api/ratings`, `/api/ratings/:type/:id/:kind`, `/api/admin/tuning`, `/api/admin/health`, `/api/settings/score-bands`
- `database.py` — SQLite wrapper; all DB access goes through `Database` class; WAL mode; `upsert_listing()`, `upsert_project()`
- `models.py` — `Listing` dataclass
- `scoring_engine.py` — Flip/Rent score roll-up; all signals normalised to 0–100; asymmetric formula: `score = weighted_positive - weighted_blocker * 0.4`; signals absent when data is missing (weights renormalize automatically)
- `persona_engine.py` — Per-persona score weight vectors; mirrors `frontend/src/lib/personas.js`
- `signals.py`, `signals_text.py` — Raw signal computation (geo, text features); consumed by `scoring_engine.py`
- `amenity_rating.py` — Green-space / convenience amenity score computation
- `preference_deck.py` — Builds the onboarding swipe deck for a user+persona
- `region_profiles.py` — Area-level scoring profiles (`SIGNAL_KEYS`, `BLOCKER_KEYS`, `get_profile()`)
- `scorers/photo_tagger.py` — Claude vision: extracts style/room/quality tags from listing photos
- `scorers/renovation_classifier.py` — Claude vision: estimates renovation level from photos
- `scorers/style_extractor.py` — Claude vision: extracts interior style labels
- `scorers/preference_chat.py` — Claude text: extracts structured preferences from free-text input
- `scorers/listing_comparator.py` — Claude text: AI-powered listing comparison narrative
- `scrapers/idealista_playwright.py` — Playwright scraper for idealista.pt; uses real Chrome (`channel="chrome"`) + persistent profile at `data/browser_profile/` to bypass DataDome
- `scrapers/era_playwright.py`, `scrapers/remax_playwright.py`, `scrapers/imovirtual_playwright.py`, `scrapers/olx_playwright.py`, `scrapers/casa_sapo_playwright.py` — Additional property portal scrapers (same `run_scraper()` interface)
- `scrapers/cml_projects.py` — Fetches construction permits/applications from Lisbon open data ArcGIS API, computes polygon centroids

### Frontend (`frontend/src/`)
- `App.jsx` — Root; fetches listings, neighborhoods, projects; manages filter state and `visibleCategories`; fetches Supabase `profile_swipes` and `listing_ratings` per user; handles reactions
- `api.js` — All `fetch()` calls; proxied via Vite to the backend port (`BACKEND_PORT` in preview; defaults to 8000 for manual `npm run dev`)
- `projectCategories.js` — `CATEGORIES` map (8 types with colors) + `getCategory(operation)` classifier
- `translations.js` — i18n strings (PT/EN); active locale via `LanguageContext`
- `useFilters.js` — Custom hook encapsulating all listing filter state
- `contexts/AuthContext.jsx` — Supabase auth state (session, sign-in, sign-out)
- `lib/supabase.js` — Supabase client; exports `supabase` and `supabaseEnabled` flag (false when env vars missing — app still renders without auth)
- `lib/swipeWeights.js` — `computeWeights(personaId, swipes)` — derives per-signal weight overrides from onboarding swipes; requires ≥4 non-skip swipes
- `lib/preferences.js` — Preference vocabulary (budget, size, bedrooms, style, renovation level); `preferencesToFilters()` maps to backend query params
- `lib/personas.js` — `PERSONAS` map with per-persona `scoreWeights`; mirrors `backend/persona_engine.py`
- `lib/savedSearches.js` — CRUD for Supabase `saved_searches` table
- `lib/reactionWeights.js` — Converts `listing_reactions` rows into swipe-shaped pseudo-events for `computeWeights()`
- `pages/Landing.jsx` — Unauthenticated landing/sign-in page
- `pages/Onboarding.jsx` — Swipe-based onboarding deck; writes `profile_swipes` and `profiles` rows to Supabase
- `components/Map.jsx` — MapContainer with listing markers and ProjectLayer
- `components/ProjectLayer.jsx` — Renders construction project CircleMarkers, filtered by `visibleCategories`
- `components/MapLegend.jsx` — Layer toggles with per-category counts
- `components/Sidebar.jsx` — Listing filters and results list
- `components/SidebarTabs.jsx` — Tab switcher (Listings / Map / My)
- `components/ListingCard.jsx` — Card in results list; shows score bands, price, rarity badge
- `components/ListingDetail.jsx` — Full listing detail with scoring breakdown, photos, map
- `components/FilterPanel.jsx` — Filter controls (price, size, rooms, parish, etc.)
- `components/PersonaBar.jsx` / `PersonaRater.jsx` / `PersonaInsightCard.jsx` — Persona selection and score insight UI
- `components/AdminPage.jsx` — Admin shell wrapping `admin/` tab components
- `components/admin/CalibrateTab.jsx` — Admin: rate listings to calibrate scoring; reads/writes `listing_ratings` in Supabase
- `components/admin/TuningTab.jsx` — Admin: adjust score band thresholds
- `components/admin/ScrapersTab.jsx` — Admin: trigger scrape jobs via `/api/scrape`
- `components/admin/DataHealthTab.jsx` — Admin: signal coverage and data quality metrics
- `components/admin/ConfigTab.jsx` — Admin: score weight tuning

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

### `listing_type` parameter

Most endpoints that take a `listing_type` parameter behave as follows:

- `"sale"` — query only the `sales` table (default for most endpoints)
- `"rent"` — query only the `rentals` table
- `null` / omitted on `/api/listings` — returns both tables unioned (all listings)
- `null` / omitted on `/api/listings/:id` — defaults to `"sale"`

The `/api/listings` endpoint is the only one that returns both types in a single response; all detail endpoints require an explicit `listing_type`.

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

## Supabase

Supabase holds **user-specific data only**. All listing data, scores, and geo data live in SQLite. Supabase is optional — the app renders (without auth) if `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` are not set.

Environment: set in `frontend/.env.local` (copied automatically from main repo to worktrees by `launch.json`).

### Supabase tables

| Table | Who writes | What it stores |
|---|---|---|
| `profiles` | `pages/Onboarding.jsx` | `user_id`, `persona_id`, `preferences` JSON (budget, size, bedrooms, style, renovation level) |
| `profile_swipes` | `pages/Onboarding.jsx` | One row per onboarding swipe: `action` (like/dislike/skip), `persona`, `factor_positives` JSON (signal name → 0–100 score) |
| `listing_reactions` | `App.jsx`, `MyListingsPage.jsx` | User like/dislike/bookmark on a listing: `user_id`, `listing_id`, `listing_type`, `reaction`, `comment` |
| `listing_ratings` | `admin/CalibrateTab.jsx`, `admin/TuningTab.jsx` | Admin quality ratings for calibration: `user_id`, `listing_id`, `listing_type`, `rating_type` (flip/rent), `score` |
| `saved_searches` | `lib/savedSearches.js` | Saved filter sets: `user_id`, `name`, `filters` JSON |

### Score weight override pipeline

1. User completes onboarding → swipes written to `profile_swipes` (Supabase).
2. On app load, `App.jsx` fetches `profile_swipes` for the current user + persona.
3. `lib/swipeWeights.js::computeWeights()` nudges the persona's base `scoreWeights` up/down based on liked/disliked signal patterns (requires ≥ 4 non-skip swipes).
4. Computed weights are serialized as a JSON query param and sent with `/api/listings` requests.
5. Backend `scoring_engine.py` applies the override weights in the score roll-up.
6. `lib/reactionWeights.js` also converts `listing_reactions` into pseudo-swipes that flow through the same `computeWeights()` path.

## Debugging

### Backend won't start — `Address already in use`
`launch.json` picks a free port dynamically, so this should not happen in preview. If running `python3 api.py` manually, the default port is 8000. Kill any existing process: `lsof -ti:8000 | xargs kill`.

### Frontend shows no listings
1. Check the backend is running: `curl http://localhost:8000/api/stats` (or the dynamic port shown in preview logs).
2. Check `frontend/.env.local` exists with correct `VITE_SUPABASE_*` vars — Vite strips it on fresh worktrees but `launch.json` copies it from the main repo automatically.
3. If the DB is empty, check that `backend/data/lisboa_realestate.db` exists and is > 500KB. The preview copies from the main repo; for manual runs, copy it yourself.

### Supabase auth not working
- Confirm `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` are set in `frontend/.env.local`.
- The app degrades gracefully when these are missing (`supabaseEnabled = false`), so missing env vars produce a silent "auth unavailable" state, not a crash.

### Scraper blocked / returns 0 listings
DataDome blocks headless Chrome. Run `python3 scrapers/idealista_playwright.py --setup` to open a real browser, solve the challenge manually, then re-run headless. The solved cookie is persisted in `data/browser_profile/`.

### Python 3.9 syntax errors
Use `Optional[X]` from `typing` instead of `X | None`. The post-edit hook (`settings.json`) runs `py_compile` automatically on every `.py` file edit and will surface these immediately.

### Scorer / Claude API errors
All scorers in `backend/scorers/` require `ANTHROPIC_API_KEY` in the environment. The `launch.json` reads it from `~/.zshrc` automatically. For manual runs: `export ANTHROPIC_API_KEY=...`. The `/api/extract-image-tags`, `/api/extract-preferences`, and `/api/compare-listings` endpoints will return 500 if the key is missing.

### Visual regression failures
See `.claude/skills/visual-regression/SKILL.md` for the full workflow. Quick tip: `dimension-mismatch` means the viewport changed, not a styling bug — don't approve; investigate.

## Key constraints

- **Python 3.9**: No `str | None` union syntax — use `Optional[str]` from `typing`
- **playwright-stealth v2**: API is `Stealth().use_sync(page)`, not `stealth_sync(page)`
- **ArcGIS pagination**: Break only when `len(batch) < PAGE_SIZE`; don't rely on `exceededTransferLimit`
- **`/api/projects`**: Excludes `geometry` column and defaults to `limit=5000` (most recent) to keep response size manageable
- **DataDome**: Idealista blocks automated browsers; requires real Chrome + persistent profile + one-time manual challenge solve via `--setup`
