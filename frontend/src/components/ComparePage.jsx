import { useEffect, useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { supabase } from '../lib/supabase'
import { fetchListingDetail, compareListings } from '../api'
import ListingRefCard from './ListingRefCard'

function fmtPrice(n) {
  if (n == null) return '—'
  return '€' + Math.round(n).toLocaleString('pt-PT')
}

function PointList({ points, color }) {
  if (!points?.length) return <p className="text-xs text-gray-400 italic">None noted.</p>
  return (
    <ul className="space-y-1">
      {points.map((p, i) => (
        <li key={i} className={`text-sm flex gap-2 ${color}`}>
          <span className="shrink-0 mt-0.5">{color.includes('green') ? '✓' : '✗'}</span>
          <span>{p}</span>
        </li>
      ))}
    </ul>
  )
}

function Slot({ label, listing, likedListings, onPick, onClear }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')

  const filtered = likedListings.filter(l => {
    if (!query) return true
    const q = query.toLowerCase()
    return (
      (l.listing?.title || '').toLowerCase().includes(q) ||
      (l.listing?.neighborhood || '').toLowerCase().includes(q) ||
      String(l.listing?.price_amount || '').includes(q)
    )
  })

  if (listing) {
    return (
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-2">
          <span className="font-semibold text-gray-700 text-sm">{label}</span>
          <button
            onClick={onClear}
            className="text-xs text-gray-400 hover:text-red-500"
          >✕ Change</button>
        </div>
        <div className="border border-gray-200 rounded-lg overflow-hidden">
          {listing.images_parsed?.[0] && (
            <img
              src={listing.images_parsed[0]}
              alt=""
              className="w-full h-32 object-cover"
              onError={e => { e.target.style.display = 'none' }}
            />
          )}
          <div className="p-3">
            <div className="font-medium text-sm text-gray-900 truncate">
              {listing.title || `T${listing.rooms ?? '?'}`}
            </div>
            <div className="text-xs text-gray-500 mt-0.5">
              {listing.neighborhood}{listing.parish ? ` · ${listing.parish}` : ''}
            </div>
            <div className="flex gap-3 text-xs mt-1 font-semibold text-gray-800">
              <span>{fmtPrice(listing.price_amount)}</span>
              {listing.size_sqm && <span>{listing.size_sqm} m²</span>}
              {listing.rooms != null && <span>T{listing.rooms}</span>}
            </div>
            {listing.url && (
              <a
                href={listing.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[11px] text-primary hover:underline mt-1 inline-block"
              >View listing ↗</a>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 min-w-0">
      <div className="font-semibold text-gray-700 text-sm mb-2">{label}</div>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full border-2 border-dashed border-gray-300 rounded-lg p-6 text-sm text-gray-400 hover:border-primary-border hover:text-primary transition-colors"
      >
        {open ? '▲ Close picker' : '+ Pick a listing'}
      </button>
      {open && (
        <div className="mt-2 border border-gray-200 rounded-lg bg-white shadow-sm max-h-72 overflow-auto">
          <div className="p-2 border-b border-gray-100 sticky top-0 bg-white">
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Filter by name, neighborhood…"
              className="w-full text-xs border border-gray-200 rounded px-2 py-1 outline-none focus:border-primary-border"
              autoFocus
            />
          </div>
          {filtered.length === 0 ? (
            <p className="p-3 text-xs text-gray-400 italic">No liked listings match.</p>
          ) : (
            <div className="p-2 space-y-2">
              {filtered.map(item => (
                <button
                  key={`${item.listing_kind}-${item.listing_id}`}
                  onClick={() => { onPick(item); setOpen(false); setQuery('') }}
                  className="w-full text-left hover:bg-primary-tint rounded transition-colors"
                >
                  <ListingRefCard
                    listing={item.listing}
                    listingKind={item.listing_kind}
                  />
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function ComparePage({ onBack, onViewListing, embedded = false }) {
  const { user, profile } = useAuth()
  const [likedListings, setLikedListings] = useState([])
  const [loadingLiked, setLoadingLiked] = useState(true)
  const [slotA, setSlotA] = useState(null)
  const [slotB, setSlotB] = useState(null)
  const [result, setResult] = useState(null)
  const [comparing, setComparing] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!user || !supabase) { setLoadingLiked(false); return }
    ;(async () => {
      setLoadingLiked(true)
      try {
        const { data } = await supabase
          .from('listing_reactions')
          .select('listing_kind, listing_id, reaction, comment, created_at')
          .eq('user_id', user.id)
          .eq('reaction', 'like')
          .order('created_at', { ascending: false })
        const enriched = await Promise.all((data ?? []).map(async r => {
          try {
            const d = await fetchListingDetail(r.listing_id, r.listing_kind === 'rent' ? 'rent' : 'sale')
            const listing = d.listing ?? null
            if (listing && listing.images) {
              try { listing.images_parsed = JSON.parse(listing.images) } catch { listing.images_parsed = [] }
            }
            return { ...r, listing }
          } catch { return { ...r, listing: null } }
        }))
        setLikedListings(enriched.filter(e => e.listing))
      } finally {
        setLoadingLiked(false)
      }
    })()
  }, [user?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleCompare = async () => {
    if (!slotA || !slotB) return
    setComparing(true)
    setResult(null)
    setError(null)
    try {
      const r = await compareListings(
        { id: slotA.listing_id, kind: slotA.listing_kind },
        { id: slotB.listing_id, kind: slotB.listing_kind },
        profile?.persona ?? null,
      )
      setResult(r)
    } catch (e) {
      setError(String(e?.message ?? e))
    } finally {
      setComparing(false)
    }
  }

  const canCompare = slotA && slotB && !comparing

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Header */}
      {!embedded && (
        <div className="flex items-center gap-4 px-4 py-2 bg-white border-b border-gray-200 shrink-0">
          <button onClick={onBack} className="text-xs text-gray-600 hover:text-gray-900 cursor-pointer">
            ← Back to map
          </button>
          <span className="font-semibold text-gray-800">⚖️ Compare listings</span>
        </div>
      )}

      <div className="flex-1 overflow-auto p-4 space-y-4">
        {loadingLiked ? (
          <p className="text-sm text-gray-400">Loading your liked listings…</p>
        ) : likedListings.length === 0 ? (
          <div className="text-center py-12 text-gray-400 text-sm">
            <p className="text-3xl mb-3">👍</p>
            <p>Like some listings first — they'll appear here for comparison.</p>
          </div>
        ) : (
          <>
            {/* Slot pickers */}
            <div className="flex gap-4 items-start">
              <Slot
                label="Listing A"
                listing={slotA?.listing}
                likedListings={likedListings.filter(l =>
                  !slotB || `${l.listing_kind}-${l.listing_id}` !== `${slotB.listing_kind}-${slotB.listing_id}`
                )}
                onPick={item => { setSlotA(item); setResult(null) }}
                onClear={() => { setSlotA(null); setResult(null) }}
              />
              <div className="flex items-start pt-10 text-gray-300 text-2xl select-none">vs</div>
              <Slot
                label="Listing B"
                listing={slotB?.listing}
                likedListings={likedListings.filter(l =>
                  !slotA || `${l.listing_kind}-${l.listing_id}` !== `${slotA.listing_kind}-${slotA.listing_id}`
                )}
                onPick={item => { setSlotB(item); setResult(null) }}
                onClear={() => { setSlotB(null); setResult(null) }}
              />
            </div>

            {/* Compare button */}
            {slotA && slotB && !result && (
              <div className="flex justify-center">
                <button
                  onClick={handleCompare}
                  disabled={!canCompare}
                  className="px-6 py-2 bg-primary text-white text-sm font-medium rounded-lg hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  {comparing ? (
                    <>
                      <span className="animate-spin inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                      Analysing with AI…
                    </>
                  ) : '⚖️ Compare with AI'}
                </button>
              </div>
            )}

            {error && (
              <div className="text-sm text-red-600 text-center">
                {error}
                <button onClick={handleCompare} className="underline ml-2">Retry</button>
              </div>
            )}

            {/* Results */}
            {result && (
              <div className="space-y-4">
                <div className="flex gap-4 items-start">
                  {/* Listing A analysis */}
                  <div className="flex-1 space-y-3">
                    <div>
                      <h4 className="text-sm font-semibold text-green-700 mb-1">👍 Positives</h4>
                      <PointList points={result.a?.positives} color="text-green-700" />
                    </div>
                    <div>
                      <h4 className="text-sm font-semibold text-red-600 mb-1">👎 Negatives</h4>
                      <PointList points={result.a?.negatives} color="text-red-600" />
                    </div>
                  </div>

                  <div className="w-px bg-gray-200 self-stretch" />

                  {/* Listing B analysis */}
                  <div className="flex-1 space-y-3">
                    <div>
                      <h4 className="text-sm font-semibold text-green-700 mb-1">👍 Positives</h4>
                      <PointList points={result.b?.positives} color="text-green-700" />
                    </div>
                    <div>
                      <h4 className="text-sm font-semibold text-red-600 mb-1">👎 Negatives</h4>
                      <PointList points={result.b?.negatives} color="text-red-600" />
                    </div>
                  </div>
                </div>

                {/* Recommendation */}
                {result.recommendation && (
                  <div className="bg-primary-tint border border-primary-border rounded-lg p-4">
                    <h4 className="text-sm font-semibold text-primary mb-1">🎯 Recommendation</h4>
                    <p className="text-sm text-primary">{result.recommendation}</p>
                  </div>
                )}

                <div className="flex justify-center">
                  <button
                    onClick={handleCompare}
                    className="text-xs text-gray-400 hover:text-gray-600 underline"
                  >Re-run analysis</button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
