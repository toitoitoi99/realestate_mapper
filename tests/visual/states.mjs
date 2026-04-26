// Visual regression state manifest.
//
// Each state declares:
//   name        slug, becomes baselines/<name>.png
//   viewport    { width, height }
//   threshold   pixelmatch match threshold 0..1 (0.1 is default — lower is stricter)
//   maxDiffPct  max % of pixels allowed to differ before failing (0..100)
//   setup       async (page) => void — deterministic driver from a freshly-loaded app
//   stabilize   optional ms wait after setup before the screenshot
//
// Map-heavy states use looser tolerances because tile rendering varies.
// UI-chrome states use strict tolerances.
//
// Determinism notes:
//   - We disable CSS animations globally on every page (see runner.mjs).
//   - Listing markers and sidebar order depend on the local DB snapshot.
//     Baselines are tied to whatever DB is in the worktree when they were captured.
//   - Map tiles are network-dependent; stabilize waits + loose threshold cover most noise.

const DESKTOP = { width: 1440, height: 900 };

// Small helper used by several states.
// Navigates to /app (bypassing the persona-selection landing page) then waits
// for at least one Leaflet marker to appear.
async function waitForListings(page) {
  // If the current URL is at the root landing page, go directly to /app
  const url = page.url();
  if (!url.includes('/app')) {
    await page.goto(url.replace(/\/$/, '') + '/app', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
  }
  await page.waitForFunction(() => {
    const markers = document.querySelectorAll('.leaflet-interactive');
    return markers.length > 0;
  }, { timeout: 30000 });
}

async function clickByText(page, text, opts = {}) {
  const locator = page.getByText(text, { exact: opts.exact ?? false }).first();
  await locator.click();
}

export const STATES = [
  {
    name: 'app-initial-load',
    viewport: DESKTOP,
    threshold: 0.15,
    maxDiffPct: 2.0,
    stabilize: 1500,
    async setup(page) {
      await waitForListings(page);
    },
  },
  {
    name: 'sidebar-collapsed',
    viewport: DESKTOP,
    threshold: 0.1,
    maxDiffPct: 0.5,
    stabilize: 400,
    async setup(page) {
      await waitForListings(page);
      // Sidebar collapse chevron lives at top-right edge of the sidebar (aria-label may vary).
      // Fall back to the first button whose accessible name matches a chevron-ish pattern.
      const btn = page.locator('button[aria-label*="collapse" i], button[title*="collapse" i]').first();
      if (await btn.count()) {
        await btn.click();
      } else {
        // Heuristic fallback: a small button at sidebar's top-right edge.
        await page.evaluate(() => {
          const sidebar = document.querySelector('aside, [class*="sidebar" i]');
          const btn = sidebar?.querySelector('button');
          btn?.click();
        });
      }
      await page.waitForTimeout(300);
    },
  },
  {
    name: 'sidebar-neighbourhoods-tab',
    viewport: DESKTOP,
    threshold: 0.1,
    maxDiffPct: 0.5,
    stabilize: 500,
    async setup(page) {
      await waitForListings(page);
      await clickByText(page, /neighbour|parish|bairro/i);
      await page.waitForTimeout(300);
    },
  },
  {
    name: 'filter-panel-advanced-open',
    viewport: DESKTOP,
    threshold: 0.1,
    maxDiffPct: 0.5,
    stabilize: 300,
    async setup(page) {
      await waitForListings(page);
      // "Advanced filters" label lives inside a clickable header.
      await page.getByText(/^Advanced filters$/).first().click({ timeout: 5000 });
      await page.waitForTimeout(400);
    },
  },
  {
    name: 'filter-listing-type-rent',
    viewport: DESKTOP,
    threshold: 0.1,
    maxDiffPct: 0.5,
    stabilize: 500,
    async setup(page) {
      await waitForListings(page);
      // "Rent" button in the listing-type group (three buttons: Buy / Rent / All).
      await page.getByRole('button', { name: /^Rent$/ }).first().click({ timeout: 5000 });
      await page.waitForTimeout(600);
    },
  },
  {
    name: 'filter-status-sold',
    viewport: DESKTOP,
    threshold: 0.1,
    maxDiffPct: 0.5,
    stabilize: 600,
    async setup(page) {
      await waitForListings(page);
      // Status row: Active / Sold / Both. Target exactly "Sold".
      await page.getByRole('button', { name: /^Sold$/ }).first().click({ timeout: 5000 });
      await page.waitForTimeout(600);
    },
  },
  {
    name: 'listing-popup-open',
    viewport: DESKTOP,
    threshold: 0.2,
    maxDiffPct: 2.0,
    stabilize: 800,
    async setup(page) {
      await waitForListings(page);
      // Click the first listing marker on the map.
      await page.evaluate(() => {
        const marker = document.querySelector('.leaflet-interactive');
        if (marker) {
          const rect = marker.getBoundingClientRect();
          const evt = new MouseEvent('click', {
            bubbles: true,
            cancelable: true,
            clientX: rect.left + rect.width / 2,
            clientY: rect.top + rect.height / 2,
          });
          marker.dispatchEvent(evt);
        }
      });
      await page.waitForTimeout(600);
    },
  },
  {
    name: 'listing-detail-view',
    viewport: DESKTOP,
    threshold: 0.15,
    maxDiffPct: 1.5,
    stabilize: 1500,
    async setup(page) {
      await waitForListings(page);
      // ListingCard renders as <div class="... rounded-lg ... cursor-pointer"> inside the sidebar.
      // Use the FlipRentBadges text "€" + "/m²" signature to find a listing card reliably;
      // fall back to clicking the first cursor-pointer block that contains a "€" price string.
      const clicked = await page.evaluate(() => {
        const candidates = Array.from(document.querySelectorAll('div.cursor-pointer'))
          .filter(el => el.textContent?.includes('€') && el.offsetWidth < 500 /* sidebar card width */);
        const first = candidates[0];
        if (first) { first.click(); return true; }
        return false;
      });
      if (!clicked) throw new Error('listing-detail-view: no listing card found to click');
      await page.waitForTimeout(1200);
    },
  },
  {
    name: 'scorecard-signals-expanded',
    viewport: DESKTOP,
    threshold: 0.15,
    maxDiffPct: 1.5,
    stabilize: 800,
    async setup(page) {
      await waitForListings(page);
      const clicked = await page.evaluate(() => {
        const candidates = Array.from(document.querySelectorAll('div.cursor-pointer'))
          .filter(el => el.textContent?.includes('€') && el.offsetWidth < 500);
        const first = candidates[0];
        if (first) { first.click(); return true; }
        return false;
      });
      if (!clicked) throw new Error('scorecard-signals-expanded: no listing card found');
      await page.waitForTimeout(1200);
      // Click "Review signals" toggle inside FlipRentScorecard.
      await page.getByText(/^Review signals$/).first().click({ timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(500);
    },
  },
  {
    name: 'map-zoom-11',
    viewport: DESKTOP,
    threshold: 0.3,
    maxDiffPct: 5.0,
    stabilize: 2500,
    async setup(page) {
      await waitForListings(page);
      await page.evaluate(() => {
        const mapEl = document.querySelector('.leaflet-container');
        const leafletMap = mapEl && mapEl._leaflet_map ? mapEl._leaflet_map : null;
        // Fallback: leaflet stores map on the element in most versions.
      });
      // More reliable: use keyboard + set view via global hook if exposed.
      await page.evaluate(() => {
        const el = document.querySelector('.leaflet-container');
        // react-leaflet attaches the Map instance under __leafletMap in our app shim;
        // otherwise we rely on the zoom control buttons.
      });
      // Use zoom buttons deterministically.
      await page.locator('.leaflet-container').click();
      // Press "-" to zoom out down to ~11, then "+" to bump back up if needed.
      // Default zoom is 10 (AML). Press "+" once to reach 11.
      await page.keyboard.press('=');
      await page.waitForTimeout(2000);
    },
  },
  {
    name: 'map-zoom-14',
    viewport: DESKTOP,
    threshold: 0.3,
    maxDiffPct: 5.0,
    stabilize: 2500,
    async setup(page) {
      await waitForListings(page);
      await page.locator('.leaflet-container').click();
      for (let i = 0; i < 4; i++) {
        await page.keyboard.press('=');
        await page.waitForTimeout(350);
      }
      await page.waitForTimeout(1500);
    },
  },
  {
    name: 'map-zoom-17',
    viewport: DESKTOP,
    threshold: 0.3,
    maxDiffPct: 6.0,
    stabilize: 2500,
    async setup(page) {
      await waitForListings(page);
      await page.locator('.leaflet-container').click();
      for (let i = 0; i < 7; i++) {
        await page.keyboard.press('=');
        await page.waitForTimeout(350);
      }
      await page.waitForTimeout(1500);
    },
  },
  {
    name: 'map-legend-base-map-open',
    viewport: DESKTOP,
    threshold: 0.1,
    maxDiffPct: 1.0,
    stabilize: 500,
    async setup(page) {
      await waitForListings(page);
      // Open the base-map picker inside MapLegend (toggles its dropdown).
      await page.getByText(/^Base Map$/i).first().click({ timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(500);
    },
  },
];
