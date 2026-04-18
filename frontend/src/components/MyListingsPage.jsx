import { useEffect, useMemo, useState } from 'react'
import { fetchReactions, clearReaction as apiClearReaction } from '../api'
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

export default function MyListingsPage({ onBack, onViewListing }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = () => {
    setLoading(true); setError(null)
    fetchReactions({ details: true })
      .then(d => setRows(d.reactions ?? []))
      .catch(e => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const { liked, disliked } = useMemo(() => {
    const sorted = [...rows].sort((a, b) =>
      new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0)
    )
    return {
      liked: sorted.filter(r => r.reaction === 'like'),
      disliked: sorted.filter(r => r.reaction === 'dislike'),
    }
  }, [rows])

  const handleClear = async (item) => {
    const prev = rows
    setRows(rows.filter(r => !(r.listing_kind === item.listing_kind && r.listing_id === item.listing_id)))
    try {
      await apiClearReaction(item.listing_kind, item.listing_id)
    } catch (e) {
      setRows(prev)
      setError(`Failed to clear: ${e?.message ?? e}`)
    }
  }

  return (
    <div className="flex flex-col h-full bg-gray-50">
      <div className="flex items-center gap-4 px-4 py-2 bg-white border-b border-gray-200 shrink-0">
        <button
          onClick={onBack}
          className="text-xs text-gray-600 hover:text-gray-900 cursor-pointer"
        >
          ← Back to map
        </button>
        <span className="font-semibold text-gray-800">⭐ My listings</span>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {loading ? (
          <div className="text-sm text-gray-500">Loading…</div>
        ) : error ? (
          <div className="text-sm text-red-600">
            {error} <button onClick={load} className="underline ml-2">Retry</button>
          </div>
        ) : (
          <>
            <div className="text-xs text-gray-500 mb-3">
              Properties you've reacted to. <b>Hidden</b> ones are dismissed from the map by default — you can bring them back from the map legend.
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
                title="Hidden"
                icon="👎"
                items={disliked}
                onClear={handleClear}
                onViewOnMap={onViewListing}
                emptyHint="Nothing hidden yet."
              />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
