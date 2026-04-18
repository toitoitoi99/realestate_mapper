// Convert listing reactions (likes/dislikes from the existing reactions
// feature) into swipe-shaped pseudo-events so they can flow through the
// same computeWeights() path as onboarding swipes.
//
// A swipe carries factor_positives that the deck endpoint pre-computes
// at swipe time. Reactions don't — but the user already has the listings
// in memory, and each listing's flip_factors / rent_factors blob has the
// same positives bundle. We just extract and shape it.
//
// Limitations:
//   - Only reactions on currently-loaded listings contribute. Listings
//     outside the active result set are silently ignored (acceptable since
//     the default fetch returns up to 10k listings).
//   - Reactions don't carry persona — we apply them under the active
//     persona. Fine in practice since users don't switch personas often.

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

/** Build an `{kind-id: listing}` index for O(1) lookup. */
export function indexListingsByKey(listings) {
  const idx = new Map()
  for (const l of listings || []) {
    idx.set(`${l.listing_type}-${l.id}`, l)
  }
  return idx
}

/**
 * Convert a reactions map (`${kind}-${id}` → {reaction, comment}) into
 * an array of swipe-shaped events: {action, factor_positives, persona,
 * source: 'reaction'}. Drops reactions whose listing isn't loaded or
 * lacks the persona's bundle source.
 */
export function reactionsToSwipes(reactions, listingsByKey, personaId) {
  if (!personaId || !reactions || !listingsByKey) return []
  const out = []
  for (const [key, r] of Object.entries(reactions)) {
    if (r?.reaction !== 'like' && r?.reaction !== 'dislike') continue
    const listing = listingsByKey.get(key)
    if (!listing) continue
    const positives = readPositives(listing, personaId)
    if (!positives) continue
    out.push({
      action: r.reaction,
      factor_positives: positives,
      persona: personaId,
      source: 'reaction',
    })
  }
  return out
}
