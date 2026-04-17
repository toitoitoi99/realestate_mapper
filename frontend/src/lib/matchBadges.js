// Compare a listing against the user's saved preferences and return a list
// of {key, label} for each preference the listing satisfies. Only positive
// matches — never "missing" badges, since untagged listings should look
// neutral, not penalised.
//
// Used by ListingCard so the user can see WHY a listing matched.

const RENO_LABEL = {
  turnkey: 'Turnkey',
  cosmetic: 'Light reno',
  full_renovation: 'Reno project',
}
const RENO_ORDER = ['turnkey', 'cosmetic', 'full_renovation']

export function matchBadges(listing, prefs) {
  if (!listing || !prefs) return []
  const out = []

  // Style — exact match on style_primary
  if (prefs.style && listing.style_primary && listing.style_primary === prefs.style) {
    out.push({ key: 'style', label: cap(prefs.style) })
  }

  // Outdoor required — listing must have a non-"none" outdoor_type
  if (prefs.outdoor_required && listing.outdoor_type && listing.outdoor_type !== 'none') {
    out.push({ key: 'outdoor', label: cap(listing.outdoor_type) })
  }

  // Renovation tolerance — listing's class must be at or below the user's max
  if (prefs.max_renovation && listing.renovation_class) {
    const maxIdx = RENO_ORDER.indexOf(prefs.max_renovation)
    const listingIdx = RENO_ORDER.indexOf(listing.renovation_class)
    if (maxIdx >= 0 && listingIdx >= 0 && listingIdx <= maxIdx) {
      out.push({ key: 'reno', label: RENO_LABEL[listing.renovation_class] })
    }
  }

  // Bedrooms minimum — listing must meet the floor
  if (prefs.bedrooms_min != null && listing.bedrooms != null && listing.bedrooms >= prefs.bedrooms_min) {
    out.push({ key: 'bedrooms', label: `${listing.bedrooms} bed` })
  }

  return out
}

function cap(s) {
  if (!s) return s
  return s.charAt(0).toUpperCase() + s.slice(1)
}
