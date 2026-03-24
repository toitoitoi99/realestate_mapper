// main.js — Idealista.pt Lisbon scraper
// Apify Actor using PlaywrightCrawler
//
// Strategy:
//   1. Paginate through idealista.pt/comprar-casas/lisboa/
//   2. Collect detail-page URLs from the search results
//   3. Visit each detail page and extract from __NEXT_DATA__ JSON
//      (embedded in a <script id="__NEXT_DATA__"> tag) — much more
//      reliable than HTML parsing and survives layout changes.
//   4. Fall back to JSON-LD if __NEXT_DATA__ doesn't have what we need.
//   5. Push a normalised record to the Apify dataset.
//
// Output schema (matches Oslo scrapers):
//   source, url, source_id, title, address, postal_code, city, district,
//   neighborhood, price_amount, price_per_sqm, size_sqm, rooms, bedrooms,
//   floor, property_type, condition, lat, lon, images[], hash_dedupe,
//   scraped_at_utc

import { Actor, log } from 'apify';
import { PlaywrightCrawler } from '@crawlee/playwright';
import crypto from 'node:crypto';

// ── Config ────────────────────────────────────────────────────────────────────

const BASE_URL      = 'https://www.idealista.pt';
const SEARCH_URL    = 'https://www.idealista.pt/comprar-casas/lisboa/';
const DEFAULT_MAX_PAGES = 60;
const DEFAULT_MAX_ITEMS = 1500;
const MAX_IMAGES    = 15;

// ── Utilities ─────────────────────────────────────────────────────────────────

const toNum = (v) => {
  if (v == null) return null;
  // Portuguese/European format: "250.000" = 250000, "120,5" = 120.5
  const s = String(v).replace(/[^\d.,]/g, '');
  const n = Number(s.replace(/\.(?=\d{3})/g, '').replace(',', '.'));
  return Number.isFinite(n) && n > 0 ? n : null;
};

const sha1 = (s) => crypto.createHash('sha1').update(String(s), 'utf8').digest('hex');

const computeHash = ({ address, city, price, size, source }) => {
  const addr = (address || '').toLowerCase().normalize('NFD')
    .replace(/\p{Diacritic}/gu, '').replace(/\s+/g, ' ').trim();
  const priceR = Number.isFinite(price) ? Math.round(price / 1000) * 1000 : '';
  const sizeR  = Number.isFinite(size)  ? Math.round(size) : '';
  return sha1(`${addr}|${(city || '').toLowerCase()}|${priceR}|${sizeR}|${source}`);
};

const uniq = (arr) => Array.from(new Set(arr.filter(Boolean)));

const safeJson = (s) => { try { return JSON.parse(s); } catch { return null; } };

// ── __NEXT_DATA__ extraction ──────────────────────────────────────────────────

function extractNextData(html) {
  const m = html.match(/<script[^>]+id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/i);
  return m ? safeJson(m[1]) : null;
}

/**
 * Walk a nested object to find a value by a list of possible key paths.
 * e.g. findDeep(obj, [['adDetail', 'price'], ['ad', 'price']])
 */
function findDeep(obj, paths) {
  for (const path of paths) {
    let cur = obj;
    for (const key of path) {
      if (cur == null || typeof cur !== 'object') { cur = undefined; break; }
      cur = cur[key];
    }
    if (cur != null) return cur;
  }
  return undefined;
}

// ── Detail page parser ────────────────────────────────────────────────────────

async function extractListingFromDetail(page) {
  const url = page.url();

  // Extract source_id from URL: /imovel/12345678/
  const idMatch = url.match(/\/imovel\/(\d+)/);
  const source_id = idMatch ? idMatch[1] : sha1(url);

  const html = await page.content();
  const nextData = extractNextData(html);

  // ── Pull from __NEXT_DATA__ (most reliable) ──────────────────────────────

  // Idealista's Next.js structure puts the ad under various paths depending on version
  const pageProps = findDeep(nextData, [
    ['props', 'pageProps'],
    ['pageProps'],
  ]);

  const ad = findDeep(pageProps, [
    ['adDetail'],
    ['ad'],
    ['listing'],
    ['estate'],
  ]) || {};

  // Price
  const price_amount = toNum(
    ad.price ?? ad.priceInfo?.amount ?? ad.askingPrice
  );

  // Size
  const size_sqm = toNum(
    ad.size ?? ad.floorSize ?? ad.usableArea ?? ad.area
  );

  // Rooms (Portuguese typology: T0=0, T1=1 … mapped to integer)
  const roomsRaw = ad.rooms ?? ad.typology ?? ad.roomNumber;
  let rooms = null;
  if (roomsRaw != null) {
    // Could be "T2", "2", 2
    const m = String(roomsRaw).match(/(\d+)/);
    rooms = m ? Number(m[1]) : null;
  }

  const bedrooms = toNum(ad.bedrooms ?? ad.noOfBedrooms ?? null) ?? rooms;

  const floor = ad.floor != null ? String(ad.floor) : null;

  const property_type = normalizePropertyType(
    ad.propertyType ?? ad.estateType ?? ad.typeName
  );

  const condition = normalizeCondition(ad.condition ?? ad.status ?? ad.newDevelopment);

  // Location
  const title    = ad.title ?? ad.heading ?? null;
  const address  = ad.address ?? ad.fullAddress
    ?? [ad.street, ad.number].filter(Boolean).join(' ')
    || null;
  const postal_code  = ad.postalCode ?? ad.zipCode ?? null;
  const neighborhood = ad.neighborhood ?? ad.barrio ?? ad.district ?? null;
  const parish       = ad.parish ?? ad.freguesia ?? null;
  const city         = ad.city ?? ad.municipality ?? 'Lisboa';
  const district     = ad.province ?? ad.region ?? 'Lisboa';

  // Coordinates
  let lat = toNum(ad.latitude ?? ad.coordinates?.latitude ?? ad.ubication?.latitude);
  let lon = toNum(ad.longitude ?? ad.coordinates?.longitude ?? ad.ubication?.longitude);

  // Description
  const description = ad.description ?? ad.detail ?? null;

  // Images from __NEXT_DATA__
  const nextImages = extractImagesFromNextData(ad);

  // ── JSON-LD fallback ─────────────────────────────────────────────────────

  const jsonLd = await extractJsonLd(page);
  const ldAbout   = jsonLd?.about ?? {};
  const ldGeo     = ldAbout?.geo ?? {};
  const ldAddress = ldAbout?.address ?? {};

  const price_amount_ld = extractLdPrice(jsonLd);
  const size_sqm_ld     = toNum(ldAbout?.floorSize?.value);

  const finalPrice  = price_amount ?? price_amount_ld;
  const finalSize   = size_sqm ?? size_sqm_ld;
  const finalLat    = lat ?? toNum(ldGeo.latitude);
  const finalLon    = lon ?? toNum(ldGeo.longitude);
  const finalAddr   = address ?? ldAddress.streetAddress ?? null;
  const finalPostal = postal_code ?? ldAddress.postalCode ?? null;
  const finalCity   = city ?? ldAddress.addressLocality ?? 'Lisboa';

  // ── Images ───────────────────────────────────────────────────────────────

  const ldImages = Array.isArray(jsonLd?.image)
    ? jsonLd.image.map(i => (typeof i === 'string' ? i : i?.url)).filter(Boolean)
    : [];

  const images = uniq([...nextImages, ...ldImages])
    .filter(u => /^https?:\/\//.test(u))
    .slice(0, MAX_IMAGES);

  // ── Price per m² ─────────────────────────────────────────────────────────

  const price_per_sqm =
    finalPrice && finalSize && finalSize > 0
      ? Math.round(finalPrice / finalSize)
      : null;

  // ── Build record ─────────────────────────────────────────────────────────

  const record = {
    source:         'idealista',
    source_id,
    url,
    title:          title ?? null,
    address:        finalAddr,
    postal_code:    finalPostal,
    city:           finalCity,
    district:       district ?? 'Lisboa',
    neighborhood:   neighborhood ?? null,
    parish:         parish ?? null,
    price_amount:   finalPrice ?? null,
    price_per_sqm:  price_per_sqm,
    size_sqm:       finalSize ?? null,
    rooms,
    bedrooms,
    floor,
    property_type,
    condition,
    lat:            finalLat,
    lon:            finalLon,
    images,
    description:    typeof description === 'string' ? description.slice(0, 1000) : null,
    scraped_at_utc: new Date().toISOString(),
    hash_dedupe:    null,
  };

  record.hash_dedupe = computeHash({
    address:  record.address,
    city:     record.city,
    price:    record.price_amount,
    size:     record.size_sqm,
    source:   'idealista',
  });

  return record;
}

// ── Search results page: collect detail URLs ──────────────────────────────────

async function extractDetailUrls(page) {
  // Try JSON-LD ItemList first (cleanest)
  const urls = await page.$$eval('script[type="application/ld+json"]', (nodes) => {
    const out = [];
    for (const n of nodes) {
      try {
        const j = JSON.parse(n.textContent || 'null');
        const arr = Array.isArray(j) ? j : [j];
        for (const obj of arr) {
          if (obj?.['@type'] === 'ItemList' && Array.isArray(obj.itemListElement)) {
            out.push(...obj.itemListElement.map(el => el?.item?.url || el?.url).filter(Boolean));
          }
        }
      } catch {}
    }
    return out;
  });

  if (urls.length > 0) return uniq(urls);

  // Fallback: href links matching /imovel/XXXXXXXX/
  const anchors = await page.$$eval('a[href*="/imovel/"]', (as) =>
    as.map(a => a.href).filter(u => /\/imovel\/\d+/.test(u))
  );

  return uniq(anchors);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function extractJsonLd(page) {
  const scripts = await page.$$eval('script[type="application/ld+json"]', nodes =>
    nodes.map(n => { try { return JSON.parse(n.textContent || 'null'); } catch { return null; } })
  );
  const flat = scripts.flatMap(j => Array.isArray(j) ? j : [j]).filter(Boolean);
  return flat.find(n => {
    const t = n['@type'];
    return t === 'RealEstateListing' || (Array.isArray(t) && t.includes('RealEstateListing'));
  }) ?? null;
}

function extractLdPrice(ld) {
  if (!ld) return null;
  const specs = ld.offers?.priceSpecification ?? [];
  for (const p of specs) {
    if (String(p?.name ?? '').toLowerCase().includes('pric')) return toNum(p.price);
  }
  if (specs.length) return toNum(specs[0].price);
  return toNum(ld.offers?.price);
}

function extractImagesFromNextData(ad) {
  const imgs = ad.images ?? ad.gallery ?? ad.media ?? [];
  if (!Array.isArray(imgs)) return [];
  return imgs.map(i => {
    if (typeof i === 'string') return i;
    return i?.url ?? i?.src ?? i?.imageUrl ?? null;
  }).filter(Boolean);
}

function normalizePropertyType(raw) {
  if (!raw) return 'apartment';
  const s = String(raw).toLowerCase();
  if (/moradia|vivenda|house|villa/.test(s)) return 'house';
  if (/estúdio|studio|t0/.test(s))          return 'studio';
  if (/loft/.test(s))                        return 'loft';
  if (/duplex/.test(s))                      return 'duplex';
  if (/penthouse|cobertura/.test(s))         return 'penthouse';
  return 'apartment';
}

function normalizeCondition(raw) {
  if (!raw) return null;
  const s = String(raw).toLowerCase();
  if (/new|novo|new_development/.test(s))    return 'new';
  if (/renov|remodel|rehabilit/.test(s))     return 'renovated';
  return 'used';
}

function buildPageUrl(page) {
  if (page === 1) return SEARCH_URL;
  return SEARCH_URL.replace(/\/?$/, `/pagina-${page}.htm`);
}

// ── Main ──────────────────────────────────────────────────────────────────────

await Actor.main(async () => {
  const input = (await Actor.getInput()) || {};
  const maxPages = Number.isFinite(input.maxPages) ? input.maxPages : DEFAULT_MAX_PAGES;
  const maxItems = Number.isFinite(input.maxItems) ? input.maxItems : DEFAULT_MAX_ITEMS;
  const searchUrl = input.searchUrl || SEARCH_URL;

  log.info(`Starting Idealista Lisbon scraper — maxPages=${maxPages}, maxItems=${maxItems}`);

  let pushedItems = 0;
  let currentPage = 1;

  const crawler = new PlaywrightCrawler({
    maxConcurrency: 1,
    requestHandlerTimeoutSecs: 90,
    proxyConfiguration: input.proxyConfiguration
      ? await Actor.createProxyConfiguration(input.proxyConfiguration)
      : undefined,

    async requestHandler({ page, request, enqueueLinks }) {

      // Accept cookies banner (best-effort)
      try {
        const cookieBtn = page.locator(
          'button:has-text("Aceitar"), button:has-text("Aceito"), #didomi-notice-agree-button, [id*="accept"]'
        );
        if (await cookieBtn.first().isVisible({ timeout: 3000 }).catch(() => false)) {
          await cookieBtn.first().click({ timeout: 3000 }).catch(() => {});
          await page.waitForTimeout(500);
        }
      } catch {}

      // ── Detail page ─────────────────────────────────────────────────────
      if (request.userData.detail) {
        if (pushedItems >= maxItems) return;

        try {
          await page.waitForSelector('#__NEXT_DATA__, script[type="application/ld+json"]', {
            timeout: 12000,
          }).catch(() => {});

          const item = await extractListingFromDetail(page);

          if (item && item.price_amount) {
            await Actor.pushData(item);
            pushedItems += 1;
            log.info(`[${pushedItems}/${maxItems}] Pushed: ${item.url} — €${item.price_amount?.toLocaleString()}`);
          } else {
            log.warning(`No price found on detail: ${request.url}`);
          }
        } catch (err) {
          log.warning(`Detail extract error for ${request.url}: ${err.message}`);
        }
        return;
      }

      // ── Search results page ───────────────────────────────────────────
      const pageNo = request.userData.pageNo ?? 1;

      try {
        await Promise.race([
          page.waitForSelector('a[href*="/imovel/"]', { timeout: 12000 }),
          page.waitForSelector('script[type="application/ld+json"]', { timeout: 12000 }),
        ]);
      } catch {
        log.warning(`Search page may be empty or blocked: ${request.url}`);
      }

      const detailUrls = await extractDetailUrls(page);
      log.info(`Search page ${pageNo}: found ${detailUrls.length} listings`);

      if (detailUrls.length === 0) {
        log.warning('No detail URLs found — stopping pagination.');
        return;
      }

      await enqueueLinks({
        urls: detailUrls,
        transformRequestFunction: (req) => {
          req.userData = { detail: true };
          return req;
        },
      });

      // Paginate
      const isLastPage   = detailUrls.length < 25;
      const hitPageCap   = pageNo >= maxPages;
      const hitItemCap   = pushedItems >= maxItems;

      if (!isLastPage && !hitPageCap && !hitItemCap) {
        const nextUrl = buildPageUrl(pageNo + 1);
        await enqueueLinks({
          urls: [nextUrl],
          transformRequestFunction: (req) => {
            req.userData = { pageNo: pageNo + 1 };
            return req;
          },
        });
      }
    },

    async failedRequestHandler({ request }) {
      log.error(`Request failed after retries: ${request.url}`);
    },
  });

  await crawler.run([{
    url: searchUrl,
    userData: { pageNo: 1 },
  }]);

  log.info(`Done. Pushed ${pushedItems} listings.`);
});
