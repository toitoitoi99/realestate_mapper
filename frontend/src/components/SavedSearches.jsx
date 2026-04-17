// Lightweight dropdown for the PersonaBar:
//   - lists the user's saved searches (most recent first)
//   - "Save current" captures the current filters + area under a chosen name
//   - per-row delete
// Renders as a simple HTML <details> so it doesn't need any new state library.

import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { listSavedSearches, createSavedSearch, deleteSavedSearch } from '../lib/savedSearches'

export default function SavedSearches({ filters, area, onApply }) {
  const { user } = useAuth()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const detailsRef = useRef(null)

  useEffect(() => {
    if (!user) { setItems([]); return }
    refresh()
  }, [user?.id])

  async function refresh() {
    setLoading(true)
    try { setItems(await listSavedSearches()) }
    finally { setLoading(false) }
  }

  async function save() {
    const name = window.prompt('Name this search:', defaultName(filters))
    if (!name?.trim()) return
    setSaving(true); setError(null)
    try {
      await createSavedSearch({ userId: user.id, name, filters, area })
      await refresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  async function remove(id, e) {
    e.stopPropagation()
    if (!window.confirm('Delete this saved search?')) return
    try {
      await deleteSavedSearch(id)
      await refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  function apply(item) {
    onApply?.({ filters: item.filters || {}, area: item.area || null })
    if (detailsRef.current) detailsRef.current.open = false
    setOpen(false)
  }

  if (!user) return null

  return (
    <details
      ref={detailsRef}
      className="relative"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className="list-none cursor-pointer text-blue-700 hover:underline select-none">
        Saved&nbsp;searches{items.length > 0 && ` (${items.length})`} {open ? '\u25b4' : '\u25be'}
      </summary>

      <div className="absolute right-0 mt-1 w-72 bg-white border border-gray-200 rounded-lg shadow-lg z-50 p-2">
        <button
          onClick={save}
          disabled={saving}
          className="w-full text-left px-2 py-1.5 rounded text-xs font-medium text-blue-700 hover:bg-blue-50 disabled:opacity-50"
        >
          + Save current filters\u2026
        </button>

        {error && <div className="mt-1 px-2 text-[10px] text-red-600">{error}</div>}

        <div className="mt-1 border-t border-gray-100 pt-1 max-h-64 overflow-y-auto">
          {loading && <div className="px-2 py-1 text-xs text-gray-400">Loading\u2026</div>}
          {!loading && items.length === 0 && (
            <div className="px-2 py-1 text-xs text-gray-400 italic">No saved searches yet.</div>
          )}
          {items.map(item => (
            <button
              key={item.id}
              onClick={() => apply(item)}
              className="w-full text-left px-2 py-1.5 rounded hover:bg-gray-50 group flex items-start justify-between gap-2"
              title={describe(item.filters)}
            >
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium text-gray-800 truncate">{item.name}</div>
                <div className="text-[10px] text-gray-500 truncate">{describe(item.filters)}</div>
              </div>
              <span
                onClick={(e) => remove(item.id, e)}
                className="text-gray-300 hover:text-red-500 text-xs px-1 cursor-pointer opacity-0 group-hover:opacity-100"
                title="Delete"
              >
                \u2715
              </span>
            </button>
          ))}
        </div>
      </div>
    </details>
  )
}

// --- helpers ---------------------------------------------------------------

function defaultName(filters) {
  const parts = []
  if (filters?.listing_type && filters.listing_type !== 'all') parts.push(filters.listing_type)
  if (filters?.min_price || filters?.max_price) {
    parts.push(`\u20ac${short(filters.min_price)}-${short(filters.max_price)}`)
  }
  if (filters?.bedrooms != null) parts.push(`${filters.bedrooms} bed`)
  if (filters?.parish) parts.push(filters.parish)
  return parts.join(' \u00b7 ') || 'My search'
}

function describe(filters) {
  if (!filters) return ''
  const out = []
  if (filters.listing_type && filters.listing_type !== 'all') out.push(filters.listing_type)
  if (filters.min_price)  out.push(`\u2265 \u20ac${short(filters.min_price)}`)
  if (filters.max_price)  out.push(`\u2264 \u20ac${short(filters.max_price)}`)
  if (filters.min_sqm)    out.push(`\u2265 ${filters.min_sqm}m\u00b2`)
  if (filters.max_sqm)    out.push(`\u2264 ${filters.max_sqm}m\u00b2`)
  if (filters.bedrooms != null) out.push(`${filters.bedrooms} bed`)
  if (filters.parish)     out.push(filters.parish)
  if (filters.show_sold)  out.push(filters.show_sold)
  return out.join(' \u00b7 ') || 'no filters'
}

function short(n) {
  if (n == null) return '\u2014'
  const v = Number(n)
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000)     return `${Math.round(v / 1_000)}k`
  return `${v}`
}
