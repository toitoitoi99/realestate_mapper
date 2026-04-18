// Per-persona "headline KPI" shown on each listing card. Computes a single
// concrete number that matters most to that persona, using bundle data
// already on the row (flip_factors.bundle).
//
// Returns { label, value, tone } or null if not applicable / data missing.
//   tone: 'good' | 'ok' | 'bad' — drives badge color.

function readBundle(listing) {
  const raw = listing?.flip_factors
  if (!raw) return null
  try {
    return JSON.parse(raw).bundle || null
  } catch {
    return null
  }
}

function formatEUR(n) {
  if (n == null || !Number.isFinite(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}€${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000)     return `${sign}€${Math.round(abs / 1_000)}k`
  return `${sign}€${Math.round(abs)}`
}

// --- Per-persona calculators ----------------------------------------------

function rentalInvestorInsight(listing) {
  const bundle = readBundle(listing)
  const rentPerSqm = bundle?.expected_rent_eur_sqm
  const size = listing.size_sqm
  const price = listing.price_amount
  if (!rentPerSqm || !size || !price) return null
  const annualRent = rentPerSqm * size * 12
  const yieldPct = (annualRent / price) * 100
  if (!Number.isFinite(yieldPct)) return null
  return {
    label: 'Yield',
    value: `${yieldPct.toFixed(1)}%`,
    tone: yieldPct >= 5 ? 'good' : yieldPct >= 3.5 ? 'ok' : 'bad',
  }
}

function flipperInsight(listing) {
  const bundle = readBundle(listing)
  const resalePerSqm = bundle?.expected_resale_eur_sqm
  const size = listing.size_sqm
  const price = listing.price_amount
  if (!resalePerSqm || !size || !price) return null
  const reno = listing.reno_cost_estimate || 0
  const margin = (resalePerSqm * size) - price - reno
  if (!Number.isFinite(margin)) return null
  return {
    label: 'Margin',
    value: formatEUR(margin),
    tone: margin >= 50_000 ? 'good' : margin >= 0 ? 'ok' : 'bad',
  }
}

function homeBuyerInsight(listing) {
  // Buyer wants amenities + light + outdoor — surface the lifestyle score
  // (averaged from those positives) when available.
  const bundle = readBundle(listing)
  const positives = bundle?.positives || {}
  const parts = ['amenity', 'light', 'outdoor_space']
    .map(k => positives[k])
    .filter(v => v != null)
  if (parts.length === 0) return null
  const avg = parts.reduce((a, b) => a + b, 0) / parts.length
  return {
    label: 'Lifestyle fit',
    value: `${Math.round(avg)}`,
    tone: avg >= 60 ? 'good' : avg >= 45 ? 'ok' : 'bad',
  }
}

function homeRenterInsight(listing) {
  // Renters care about €/m²/mo. Renting is per month, so divide price by size.
  const price = listing.price_amount
  const size = listing.size_sqm
  if (!price || !size) return null
  const perSqm = price / size
  return {
    label: '€/m²/mo',
    value: `€${perSqm.toFixed(1)}`,
    tone: 'ok',  // No universal "good" threshold for rent €/m² — just inform.
  }
}

const PERSONA_INSIGHT = {
  rental_investor: rentalInvestorInsight,
  flipper:         flipperInsight,
  home_buyer:      homeBuyerInsight,
  home_renter:     homeRenterInsight,
}

export function personaInsight(listing, personaId) {
  if (!personaId || !listing) return null
  const fn = PERSONA_INSIGHT[personaId]
  return fn ? fn(listing) : null
}
