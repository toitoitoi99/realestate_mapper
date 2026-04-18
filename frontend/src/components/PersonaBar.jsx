// Slim banner shown when a persona is active. Surfaces:
//   - the active persona
//   - the preference chips that are actually filtering the map (so the user
//     can see WHY they're seeing what they're seeing)
//   - a "matching N of M" count so the impact is concrete
//   - Saved searches dropdown
//   - Edit profile + Sign out
// Returns null when no persona is set.

import SavedSearches from './SavedSearches'

const RENO_LABEL = {
  turnkey: 'Turnkey only',
  cosmetic: 'Cosmetic OK',
  full_renovation: 'Any reno',
}

function activeChips(prefs) {
  if (!prefs) return []
  const out = []
  const b = prefs.budget || {}
  if (b.min || b.max) {
    const fmt = (n) => n ? `€${Number(n).toLocaleString('pt-PT')}` : '—'
    out.push(`${fmt(b.min)}–${fmt(b.max)}`)
  }
  const s = prefs.size || {}
  if (s.min || s.max) {
    out.push(`${s.min || 0}–${s.max || '∞'} m²`)
  }
  if (prefs.bedrooms_min != null) {
    out.push(`${prefs.bedrooms_min}+ bed`)
  }
  if (prefs.style) out.push(prefs.style)
  if (prefs.outdoor_required) out.push('outdoor')
  if (prefs.max_renovation) out.push(RENO_LABEL[prefs.max_renovation] || prefs.max_renovation)
  return out
}

export default function PersonaBar({
  persona, preferences, userEmail, listingCount, totalCount,
  onEditProfile, onSignOut, onOpenMyListings,
  // Saved-searches plumbing
  filters, area, onApplySavedSearch,
}) {
  if (!persona) return null
  const chips = activeChips(preferences)

  return (
    <div className="bg-blue-50 border-b border-blue-100 px-4 py-1.5 text-xs text-gray-700 flex items-center gap-3 flex-wrap">
      <span className="flex items-center gap-1.5">
        <span className="text-base leading-none">{persona.icon}</span>
        <span>
          Viewing as{' '}
          <span className="font-semibold text-blue-700">{persona.label}</span>
        </span>
      </span>

      {chips.length > 0 && (
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-gray-400">·</span>
          {chips.map((c, i) => (
            <span
              key={i}
              className="inline-block px-2 py-0.5 rounded-full bg-white border border-blue-200 text-blue-800 text-[11px] leading-tight"
            >
              {c}
            </span>
          ))}
        </span>
      )}

      {listingCount != null && totalCount != null && totalCount > 0 && (
        <span className="text-gray-500">
          <span className="text-gray-400">·</span>{' '}
          <span className="font-medium text-gray-700">{listingCount.toLocaleString('pt-PT')}</span>
          <span className="text-gray-400"> of {totalCount.toLocaleString('pt-PT')}</span> matching
        </span>
      )}

      <span className="ml-auto flex items-center gap-3">
        <SavedSearches
          filters={filters}
          area={area}
          onApply={onApplySavedSearch}
        />
        {onOpenMyListings && (
          <>
            <span className="text-gray-400">|</span>
            <button
              onClick={onOpenMyListings}
              className="text-blue-700 hover:underline"
            >
              ⭐ My listings
            </button>
          </>
        )}
        <span className="text-gray-400">|</span>
        <button
          onClick={onEditProfile}
          className="text-blue-700 hover:underline"
        >
          Edit profile
        </button>
        {userEmail && (
          <>
            <span className="text-gray-400">|</span>
            <span className="text-gray-500 hidden sm:inline">{userEmail}</span>
            <button
              onClick={onSignOut}
              className="text-gray-600 hover:text-gray-900 hover:underline"
            >
              Sign out
            </button>
          </>
        )}
      </span>
    </div>
  )
}
