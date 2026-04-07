const BASE = '/api'

async function api(url, opts) {
  const maxRetries = 3
  for (let i = 0; i < maxRetries; i++) {
    try {
      const res = await fetch(url, opts)
      if (res.ok) return res.json()
      if (res.status >= 500 && i < maxRetries - 1) {
        await new Promise(r => setTimeout(r, 1000 * (i + 1)))
        continue
      }
      throw new Error(`API ${res.status}: ${url}`)
    } catch (err) {
      if (err.message?.startsWith('API ')) throw err
      if (i < maxRetries - 1) {
        await new Promise(r => setTimeout(r, 1000 * (i + 1)))
        continue
      }
      throw err
    }
  }
}

export async function fetchAreas() {
  return api(`${BASE}/areas`)
}

export async function fetchStats() {
  return api(`${BASE}/stats`)
}

export async function fetchNeighborhoods(district = null) {
  const params = district ? `?district=${encodeURIComponent(district)}` : ''
  return api(`${BASE}/neighborhoods${params}`)
}

export async function fetchListings(filters = {}) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== '') params.append(k, v)
  })
  return api(`${BASE}/listings?${params}`)
}

export async function triggerScrape(maxPages = 10) {
  return api(`${BASE}/scrape`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source: 'idealista', max_pages: maxPages }),
  })
}

export async function fetchProjects(layer = null) {
  const params = layer ? `?layer=${layer}` : ''
  return api(`${BASE}/projects${params}`)
}

export async function fetchScrapeRuns() {
  return api(`${BASE}/scrape-runs?limit=5`)
}

export async function fetchIneStats() {
  return api(`${BASE}/ine-stats?latest_only=true`)
}

export async function fetchSecurity() {
  return api(`${BASE}/security`)
}

export async function fetchParishes(area = null) {
  const params = area ? `?area=${encodeURIComponent(area)}` : ''
  return api(`${BASE}/parishes${params}`)
}

export async function fetchSoldTrends(start, end) {
  const params = new URLSearchParams()
  if (start) params.set('start', start)
  if (end) params.set('end', end)
  return api(`${BASE}/sold-trends?${params}`)
}

export async function fetchAmenityRating(lat, lon) {
  const params = new URLSearchParams({ lat, lon })
  return api(`${BASE}/amenity-rating?${params}`)
}

export async function fetchListingDetail(id, listingType = 'sale') {
  const params = new URLSearchParams({ listing_type: listingType })
  return api(`${BASE}/listings/${id}?${params}`)
}

export async function translateDescription(id, listingType = 'sale') {
  return api(`${BASE}/translate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, listing_type: listingType }),
  })
}

export async function fetchListingComparison(id, listingType = 'sale', radiusM = 500, propertyType = null, bedrooms = null) {
  const params = new URLSearchParams({ listing_type: listingType, radius_m: radiusM })
  if (propertyType) params.set('property_type', propertyType)
  if (bedrooms != null) params.set('bedrooms', bedrooms)
  return api(`${BASE}/listings/${id}/compare?${params}`)
}

export async function fetchParishStats(area = 'aml') {
  return api(`${BASE}/parish-stats?area=${encodeURIComponent(area)}`)
}

export async function fetchNeighbourhoodTypologies() {
  return api(`${BASE}/neighbourhood-typologies`)
}

export async function fetchNearbyProjects(lat, lon, radiusM = 500) {
  const params = new URLSearchParams({ lat, lon, radius_m: radiusM })
  return api(`${BASE}/nearby-projects?${params}`)
}

export async function fetchAddressHistory(id, listingType = 'sale') {
  const params = new URLSearchParams({ listing_type: listingType })
  return api(`${BASE}/listings/${id}/address-history?${params}`)
}
