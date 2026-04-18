// Mirror of backend/persona_engine.py compute_persona_score, but instead of
// returning the final score it returns each signal's contribution so the UI
// can show "this listing ranked here because of X (+12), Y (+8), Z (-3)".
//
// `weights` is the SAME vector the backend used (swipe-overridden when
// available, else the persona prior from personas.js). Pass that through
// from App.jsx so the breakdown matches the actual ranking.

import { PERSONAS } from './personas'

const PERSONA_BUNDLE_SOURCE = {
  rental_investor: 'flip_factors',
  flipper:         'flip_factors',
  home_buyer:      'flip_factors',
  home_renter:     'rent_factors',
}

function readPositives(listing, personaId) {
  const col = PERSONA_BUNDLE_SOURCE[personaId] || 'flip_factors'
  const raw = listing?.[col]
  if (!raw) return null
  try {
    return (JSON.parse(raw)?.bundle?.positives) || null
  } catch {
    return null
  }
}

/**
 * Compute per-signal contributions for one listing.
 *
 * Returns { score, total_weight, items: [{signal, weight, value, contribution}, ...] }
 * sorted by contribution descending, or null when no signals are available.
 *
 * `score` mirrors the backend's normalised output (sum / total_weight).
 * `contribution` is the signal's portion of the final score (value × weight / total_weight)
 * so the items roughly sum to `score` — easy to read as "this signal pushed the score
 * up by N points."
 */
export function personaContributions(listing, personaId, weightsOverride = null) {
  if (!listing || !personaId) return null
  const persona = PERSONAS[personaId]
  if (!persona) return null
  const weights = weightsOverride || persona.scoreWeights
  if (!weights) return null

  const positives = readPositives(listing, personaId)
  if (!positives) return null

  const items = []
  let weightTotal = 0
  let weightedSum = 0
  for (const [signal, w] of Object.entries(weights)) {
    const v = positives[signal]
    if (v == null) continue
    items.push({ signal, weight: w, value: Number(v) })
    weightedSum += Number(v) * w
    weightTotal += w
  }
  if (weightTotal === 0) return null

  // Per-signal contribution to the final 0-100 score.
  for (const it of items) {
    it.contribution = (it.value * it.weight) / weightTotal
  }
  items.sort((a, b) => b.contribution - a.contribution)

  return {
    score: weightedSum / weightTotal,
    total_weight: weightTotal,
    items,
    weights_overridden: !!weightsOverride,
  }
}

// Pretty labels for signal names — same vocabulary as FlipRentScorecard.
export const SIGNAL_LABELS = {
  market_discount:    'Market discount',
  amenity:            'Amenities',
  transit:            'Transit access',
  light:              'Natural light',
  outdoor_space:      'Outdoor space',
  layout_openness:    'Layout flexibility',
  dev_momentum:       'Nearby development',
  sea_view:           'Sea view',
  demand_durability:  'Demand durability',
  expected_resale:    'Expected resale',
  yield_gross:        'Gross yield',
}
