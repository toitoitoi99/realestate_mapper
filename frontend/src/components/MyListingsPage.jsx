import { useEffect, useMemo, useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { supabase } from '../lib/supabase'
import { fetchListingDetail } from '../api'
import ListingRefCard from './ListingRefCard'

function Column({ title, icon, items, onClear, onViewOnMap, emptyHint }) {
  return (
    <div className="bg-gray-100/60 rounded-lg p-3">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-800 text-sm">
          {icon} {title}
        </h3>
        <span className="text-xs text-gray-500">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-gray-400 italic p-2">{emptyHint}</p>
      ) : (
        <div className="space-y-2">
          {items.map(item => (
            <ListingRefCard
              key={`${item.listing_kind}-${item.listing_id}`}
              listing={item.listing}
              listingKind={item.listing_kind}
              comment={item.comment}
              updatedAt={item.updated_at || item.created_at}
              onViewOnMap={item.listing && onViewOnMap ? onViewOnMap : null}
              actions={
                <button
                  onClick={() => onClear(item)}
                  className="px-2 py-1 text-xs border border-gray-300 rounded text-gray-500 hover:bg-red-50 hover:text-red-600"
                >Clear</button>
              }
            />
          ))}
        </div>
      )}
    </div>
  )
}

// When embedded in MyPage, rows/loading/error/onClear are passed from the parent.
// When used standalone, it fetches its own data.
export default function MyListingsPage({
  onBack, onViewListing, embedded = false,
  rows: rowsProp, loading: loadingProp, error: errorProp, onRetry, onClear: onClearProp,
}) {
  const { user } = useAuth()
  const [rowsOwn, setRowsOwn] = useState([])
  const [loadingOwn, setLoadingOwn] = useState(true)
  const [errorOwn, setErrorOwn] = useState(null)

  const controlled = rowsProp !== undefined
  const rows = controlled ? rowsProp : rowsOwn
  const loading = controlled ? (loadingProp ?? false) : loadingOwn
  const error = controlled ? (errorProp ?? null) : errorOwn

  const load = async () => {
    if (controlled) { onRetry?.(); return }
    if (!user || !supabase) { setRowsOwn([]); setLoadingOwn(false); return }
    setLoadingOwn(true); setErrorOwn(null)
    try {
      const { data, error: err } = await supabase
        .from('listing_reactions')
        .select('listing_kind, listing_id, reaction, comment, created_at, updated_at')
        .eq('user_id', user.id)
        .order('updated_at', { ascending: false })
      if (err) throw err
      const enriched = await Promise.all((data ?? []).map(async r => {
        try {
          const d = await fetchListingDetail(r.listing_id, r.listing_kind === 'rent' ? 'rent' : 'sale')
          return { ...r, listing: d?.id ? d : null }
        } catch { return { ...r, listing: null } }
      }))
      setRowsOwn(enriched)
    } catch (e) {
      setErrorOwn(String(e?.message ?? e))
    } finally {
      setLoadingOwn(false)
    }
  }
  useEffect(() => { if (!controlled) load() }, [user?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const { liked, disliked } = useMemo(() => ({
    liked: rows.filter(r => r.reaction === 'like'),
    disliked: rows.filter(r => r.reaction === 'dislike'),
  }), [rows])

  const handleClear = onClearProp ?? (async (item) => {
    if (!user || !supabase) return
    const prev = rowsOwn
    setRowsOwn(rowsOwn.filter(r => !(r.listing_kind === item.listing_kind && r.listing_id === item.listing_id)))
    const { error: err } = await supabase.from('listing_reactions')
      .delete()
      .eq('user_id', user.id)
      .eq('listing_kind', item.listing_kind)
      .eq('listing_id', item.listing_id)
    if (err) { setRowsOwn(prev); setErrorOwn(`Failed to clear: ${err.message}`) }
  })

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {!embedded && (
        <div className="flex items-center gap-4 px-4 py-2 bg-white border-b border-gray-200 shrink-0">
          <button onClick={onBack} className="text-xs text-gray-600 hover:text-gray-900 cursor-pointer">
            ← Back to map
          </button>
          <span className="font-semibold text-gray-800">Shortlist</span>
        </div>
      )}

      <div className="flex-1 overflow-auto p-4">
        {loading ? (
          <div className="text-sm text-gray-500">Loading…</div>
        ) : error ? (
          <div className="text-sm text-red-600">
            {error} <button onClick={load} className="underline ml-2 cursor-pointer">Retry</button>
          </div>
        ) : (
          <>
            <div className="text-xs text-gray-500 mb-3">
              Properties you've reacted to. Passed listings are hidden from the map by default.
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Column
                title="Liked"
                icon="👍"
                items={liked}
                onClear={handleClear}
                onViewOnMap={onViewListing}
                emptyHint="Nothing liked yet. Use 👍 on a listing to save it."
              />
              <Column
                title="Passed"
                icon="👎"
                items={disliked}
                onClear={handleClear}
                onViewOnMap={onViewListing}
                emptyHint="Nothing passed yet."
              />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
