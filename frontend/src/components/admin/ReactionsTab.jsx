import { useEffect, useMemo, useState } from 'react'
import { fetchReactions, clearReaction as apiClearReaction } from '../../api'

function fmtPrice(n) {
  if (n == null) return '—'
  return '€' + Math.round(n).toLocaleString('pt-PT')
}

function fmtPsm(n) {
  if (n == null) return null
  return `€${Math.round(n).toLocaleString('pt-PT')}/m²`
}

function fmtDate(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function Card({ item, onClear, onViewOnMap }) {
  const l = item.listing
  const isRent = item.listing_kind === 'rent'
  return (
    <div className="bg-white rounded border border-gray-200 p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`px-1.5 py-0.5 text-[10px] rounded ${isRent ? 'bg-violet-100 text-violet-700' : 'bg-amber-100 text-amber-700'}`}>
              {isRent ? 'RENT' : 'SALE'}
            </span>
            {l?.source && (
              <span className="px-1.5 py-0.5 text-[10px] rounded bg-gray-100 text-gray-600 uppercase">{l.source}</span>
            )}
            {l?.status && l.status !== 'active' && (
              <span className="px-1.5 py-0.5 text-[10px] rounded bg-red-100 text-red-700">{l.status}</span>
            )}
            {l?.neighborhood && (
              <span className="text-xs text-gray-500 truncate">{l.neighborhood}</span>
            )}
          </div>
          <div className="mt-1 font-medium text-gray-800 truncate" title={l?.title || ''}>
            {l?.title || <span className="text-gray-400 italic">listing #{item.listing_id} (missing)</span>}
          </div>
          <div className="mt-1 flex items-center gap-3 text-xs text-gray-600 flex-wrap">
            <span className="font-semibold text-gray-900">{fmtPrice(l?.price_amount)}</span>
            {fmtPsm(l?.price_per_sqm) && <span>{fmtPsm(l?.price_per_sqm)}</span>}
            {l?.size_sqm && <span>{l.size_sqm} m²</span>}
            {l?.rooms != null && <span>T{l.rooms}</span>}
          </div>
          {item.comment && (
            <div className="mt-2 text-xs text-gray-700 bg-gray-50 rounded p-2 border border-gray-100 whitespace-pre-wrap">
              💬 {item.comment}
            </div>
          )}
          <div className="mt-2 text-[11px] text-gray-400">
            {fmtDate(item.updated_at || item.created_at)}
          </div>
        </div>
        <div className="flex flex-col gap-1 shrink-0">
          {l?.url && (
            <a
              href={l.url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-2 py-1 text-xs border border-gray-300 rounded text-gray-700 hover:bg-gray-50 text-center"
            >
              Open
            </a>
          )}
          {l && (
            <button
              onClick={() => onViewOnMap(l)}
              className="px-2 py-1 text-xs border border-gray-300 rounded text-gray-700 hover:bg-gray-50"
            >
              Map
            </button>
          )}
          <button
            onClick={() => onClear(item)}
            className="px-2 py-1 text-xs border border-gray-300 rounded text-gray-500 hover:bg-red-50 hover:text-red-600"
          >
            Clear
          </button>
        </div>
      </div>
    </div>
  )
}

function Column({ title, icon, items, onClear, onViewOnMap }) {
  return (
    <div className="bg-gray-100/60 rounded-lg p-3">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-800 text-sm">
          {icon} {title}
        </h3>
        <span className="text-xs text-gray-500">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-gray-400 italic p-2">None yet.</p>
      ) : (
        <div className="space-y-2">
          {items.map(item => (
            <Card
              key={`${item.listing_kind}-${item.listing_id}`}
              item={item}
              onClear={onClear}
              onViewOnMap={onViewOnMap}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export default function ReactionsTab({ onViewOnMap }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = () => {
    setLoading(true)
    setError(null)
    fetchReactions({ details: true })
      .then(d => setRows(d.reactions ?? []))
      .catch(e => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const { liked, disliked } = useMemo(() => {
    const sorted = [...rows].sort((a, b) => {
      const ad = new Date(a.updated_at || a.created_at || 0).getTime()
      const bd = new Date(b.updated_at || b.created_at || 0).getTime()
      return bd - ad
    })
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
      setError(`Failed to clear reaction: ${e?.message ?? e}`)
    }
  }

  if (loading) return <div className="p-4 text-sm text-gray-500">Loading reactions…</div>
  if (error) return (
    <div className="p-4 text-sm text-red-600">
      {error} <button onClick={load} className="underline ml-2">Retry</button>
    </div>
  )

  return (
    <div className="p-4">
      <div className="text-xs text-gray-500 mb-3">
        All like/dislike reactions with their comments. "Map" jumps back to the map with the listing selected.
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Column title="Liked"    icon="👍" items={liked}    onClear={handleClear} onViewOnMap={onViewOnMap} />
        <Column title="Disliked" icon="👎" items={disliked} onClear={handleClear} onViewOnMap={onViewOnMap} />
      </div>
    </div>
  )
}
