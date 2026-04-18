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
      let msg = `API ${res.status}: ${url}`
      try {
        const body = await res.json()
        if (body.error) msg = body.error
      } catch (_) {}
      throw new Error(msg)
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

export async function triggerScrape({ source = 'idealista', maxPages = 10 } = {}) {
  return api(`${BASE}/scrape`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, max_pages: maxPages }),
  })
}

export async function fetchProjects(layer = null) {
  const params = layer ? `?layer=${layer}` : ''
  return api(`${BASE}/projects${params}`)
}

export async function fetchScrapeRuns({ source, limit = 5 } = {}) {
  const params = new URLSearchParams()
  if (source) params.set('source', source)
  params.set('limit', String(limit))
  return api(`${BASE}/scrape-runs?${params}`)
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

export async function fetchFlipRentPreview(id, {
  listingType = 'sale',
  disable = [],
  renoCostPerSqm = null,
  renoTier = null,
} = {}) {
  const params = new URLSearchParams({ listing_type: listingType })
  if (disable && disable.length) params.set('disable', disable.join(','))
  if (renoCostPerSqm != null) params.set('reno_cost_per_sqm', renoCostPerSqm)
  if (renoTier) params.set('reno_tier', renoTier)
  return api(`${BASE}/listings/${id}/score-preview?${params}`)
}

export async function extractImageTags(file) {
  const fd = new FormData()
  fd.append('file', file)
  // Skip the JSON wrapper of `api()` — it sets headers we don't want for FormData.
  const res = await fetch(`${BASE}/extract-image-tags`, { method: 'POST', body: fd })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.error || `API ${res.status}`)
  return body
}

export async function extractPreferences(message, currentPrefs = null) {
  return api(`${BASE}/extract-preferences`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, current_prefs: currentPrefs }),
  })
}

export async function fetchReactions({ details = false } = {}) {
  const params = details ? '?details=true' : ''
  return api(`${BASE}/reactions${params}`)
}

export async function setReaction(listingKind, listingId, reaction, comment = null) {
  return api(`${BASE}/reactions/${listingKind}/${listingId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reaction, comment }),
  })
}

export async function clearReaction(listingKind, listingId) {
  return api(`${BASE}/reactions/${listingKind}/${listingId}`, { method: 'DELETE' })
}

// Admin-only: per-persona agreement with the model's flip / rent score.
export async function fetchRatings({ details = false, persona = null, agree = null } = {}) {
  const params = new URLSearchParams()
  if (details) params.set('details', 'true')
  if (persona) params.set('persona', persona)
  if (agree) params.set('agree', agree)
  const qs = params.toString()
  return api(`${BASE}/ratings${qs ? `?${qs}` : ''}`)
}

export async function setRating(listingKind, listingId, persona, agree, comment = null) {
  return api(`${BASE}/ratings/${listingKind}/${listingId}/${persona}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agree, comment }),
  })
}

export async function clearRating(listingKind, listingId, persona) {
  return api(`${BASE}/ratings/${listingKind}/${listingId}/${persona}`, { method: 'DELETE' })
}

export async function fetchAdminTuning() {
  return api(`${BASE}/admin/tuning`)
}

export async function fetchAdminHealth() {
  return api(`${BASE}/admin/health`)
}

export async function fetchScoreBands() {
  return api(`${BASE}/settings/score-bands`)
}

export async function saveScoreBands(bands) {
  return api(`${BASE}/admin/tuning/score-bands`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ score_bands: bands }),
  })
}

export async function resetScoreBands() {
  return api(`${BASE}/admin/tuning/score-bands`, { method: 'DELETE' })
}

export async function addressLookup(address, lat, lon, listingType = 'sale') {
  return api(`${BASE}/address-lookup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ address, lat, lon, listing_type: listingType }),
  })
}
