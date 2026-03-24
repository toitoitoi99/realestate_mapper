const BASE = '/api'

export async function fetchStats() {
  const res = await fetch(`${BASE}/stats`)
  return res.json()
}

export async function fetchNeighborhoods(district = null) {
  const params = district ? `?district=${encodeURIComponent(district)}` : ''
  const res = await fetch(`${BASE}/neighborhoods${params}`)
  return res.json()
}

export async function fetchListings(filters = {}) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== '') params.append(k, v)
  })
  const res = await fetch(`${BASE}/listings?${params}`)
  return res.json()
}

export async function triggerScrape(maxPages = 10) {
  const res = await fetch(`${BASE}/scrape`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source: 'idealista', max_pages: maxPages }),
  })
  return res.json()
}

export async function fetchProjects(layer = null) {
  const params = layer ? `?layer=${layer}` : ''
  const res = await fetch(`${BASE}/projects${params}`)
  return res.json()
}

export async function fetchScrapeRuns() {
  const res = await fetch(`${BASE}/scrape-runs?limit=5`)
  return res.json()
}

export async function fetchIneStats() {
  const res = await fetch(`${BASE}/ine-stats?latest_only=true`)
  return res.json()
}

export async function fetchSecurity() {
  const res = await fetch(`${BASE}/security`)
  return res.json()
}

export async function fetchParishes() {
  const res = await fetch(`${BASE}/parishes`)
  return res.json()
}
