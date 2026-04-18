// Derive a per-signal weight vector from a user's onboarding swipes.
//
// We start from the persona's default scoreWeights (see personas.js, mirrored
// in backend persona_engine.py) and nudge each signal up/down based on which
// signals were strong on listings the user liked vs disliked.
//
// Stored swipe shape (one row per swipe in Supabase profile_swipes):
//   { action: 'like'|'dislike'|'skip',
//     persona: string,
//     factor_positives: { yield_gross: 78, amenity: 60, light: 40, ... } }
//
// Skips don't move weights — they're noise.
//
// The math: for each like, each signal's centered value (positives[s] - 50)/50
// (i.e. -1..+1) bumps the signal's weight by alpha * weight * centered. For
// dislikes, same magnitude in the opposite direction. We clamp to [0, +∞),
// then re-normalise to sum=1 so the override is comparable to the prior.
//
// alpha controls aggressiveness. 0.15 means a single "strongly-positive on
// signal X" like raises X's weight by ~15%; 8 likes can roughly double it.

import { PERSONAS } from './personas'

const ALPHA = 0.15

// Minimum number of non-skip swipes before we feel confident enough to send
// an override. Below this the persona prior wins.
export const MIN_SWIPES_FOR_OVERRIDE = 4

export function computeWeights(personaId, swipes) {
  const persona = PERSONAS[personaId]
  if (!persona) return null
  const base = persona.scoreWeights
  if (!base) return null

  const usable = (swipes || []).filter(s => s.action === 'like' || s.action === 'dislike')
  if (usable.length < MIN_SWIPES_FOR_OVERRIDE) return null

  // Start from the prior (clone so we don't mutate the imported config).
  const weights = { ...base }

  for (const swipe of usable) {
    const positives = swipe.factor_positives || {}
    const sign = swipe.action === 'like' ? 1 : -1
    for (const signal of Object.keys(weights)) {
      const v = positives[signal]
      if (v == null) continue
      const centered = (Number(v) - 50) / 50  // -1..+1 (assuming 0..100 scale)
      weights[signal] = weights[signal] + ALPHA * weights[signal] * centered * sign
    }
  }

  // Clamp negatives + renormalise.
  let total = 0
  for (const k of Object.keys(weights)) {
    if (weights[k] < 0) weights[k] = 0
    total += weights[k]
  }
  if (total <= 0) return null
  for (const k of Object.keys(weights)) {
    weights[k] = Number((weights[k] / total).toFixed(4))
  }
  return weights
}

// Compact stable JSON for query strings — keys sorted so the URL is
// cacheable and identical computations give identical strings.
export function weightsToQueryParam(weights) {
  if (!weights) return null
  const sorted = {}
  for (const k of Object.keys(weights).sort()) sorted[k] = weights[k]
  return JSON.stringify(sorted)
}
