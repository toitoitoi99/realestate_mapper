import { useEffect, useMemo, useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { supabase } from '../lib/supabase'
import { fetchListingDetail } from '../api'
import MyOverviewTab from './MyOverviewTab'
import MyListingsPage from './MyListingsPage'
import ComparePage from './ComparePage'
import Onboarding from '../pages/Onboarding'

const TABS = [
  { id: 'overview',  label: 'Overview' },
  { id: 'shortlist', label: 'Shortlist' },
  { id: 'analysis',  label: 'Analysis' },
  { id: 'profile',   label: 'Profile' },
]

export default function MyPage({ onBack, onViewListing, initialTab = 'overview' }) {
  const { user, profile } = useAuth()
  const [tab, setTab] = useState(initialTab)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Lift reactions fetch so Overview and Shortlist share one request
  const load = async () => {
    if (!user || !supabase) { setRows([]); setLoading(false); return }
    setLoading(true); setError(null)
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
      setRows(enriched)
    } catch (e) {
      setError(String(e?.message ?? e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [user?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const { liked, disliked } = useMemo(() => ({
    liked: rows.filter(r => r.reaction === 'like'),
    disliked: rows.filter(r => r.reaction === 'dislike'),
  }), [rows])

  // Budget from profile preferences (if set)
  const budget = profile?.preferences?.max_price || null

  const handleClear = async (item) => {
    if (!user || !supabase) return
    const prev = rows
    setRows(rows.filter(r => !(r.listing_kind === item.listing_kind && r.listing_id === item.listing_id)))
    const { error: err } = await supabase.from('listing_reactions')
      .delete()
      .eq('user_id', user.id)
      .eq('listing_kind', item.listing_kind)
      .eq('listing_id', item.listing_id)
    if (err) { setRows(prev); setError(`Failed to clear: ${err.message}`) }
  }

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 shrink-0">
        <div className="flex items-center gap-4 px-4 py-2">
          <button
            onClick={onBack}
            className="text-xs text-gray-600 hover:text-gray-900 cursor-pointer"
          >
            ← Back to map
          </button>
          <span className="font-semibold text-gray-800">My page</span>
          {loading && <span className="text-xs text-gray-400 ml-auto">Loading…</span>}
        </div>

        {/* Tab bar */}
        <div className="flex border-t border-gray-100">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex-1 py-2.5 text-xs font-medium border-b-2 transition-colors cursor-pointer ${
                tab === t.id
                  ? 'border-primary text-primary'
                  : 'border-transparent text-gray-500 hover:text-gray-800'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-auto">
        {error && (
          <div className="px-4 py-2 text-xs text-red-600 bg-red-50 border-b border-red-100">
            {error} <button onClick={load} className="underline ml-1 cursor-pointer">Retry</button>
          </div>
        )}

        {tab === 'overview' && (
          <MyOverviewTab
            liked={liked}
            disliked={disliked}
            all={rows}
            onViewListing={onViewListing}
            onGoToShortlist={() => setTab('shortlist')}
            budget={budget}
          />
        )}

        {tab === 'shortlist' && (
          <MyListingsPage
            embedded
            rows={rows}
            loading={loading}
            error={error}
            onRetry={load}
            onClear={handleClear}
            onViewListing={onViewListing}
          />
        )}

        {tab === 'analysis' && (
          <ComparePage embedded onViewListing={onViewListing} />
        )}

        {tab === 'profile' && (
          <div className="overflow-auto">
            <Onboarding onDone={onBack} />
          </div>
        )}
      </div>
    </div>
  )
}
