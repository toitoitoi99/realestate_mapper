import { useMemo } from 'react'
import ListingRefCard from './ListingRefCard'

function fmt(n) {
  return n != null ? Math.round(n).toLocaleString('pt-PT') : '—'
}

function StatCard({ label, value, sub, color = 'text-slate-800' }) {
  return (
    <div className="bg-white rounded-lg border border-gray-100 p-3 flex flex-col gap-0.5">
      <span className={`text-xl font-bold tabular-nums ${color}`}>{value}</span>
      <span className="text-xs text-gray-500">{label}</span>
      {sub && <span className="text-[10px] text-gray-400">{sub}</span>}
    </div>
  )
}

function NeighborhoodCard({ rank, name, count, avgPrice, budget }) {
  const pctOfBudget = budget && avgPrice ? Math.round((avgPrice / budget) * 100) : null
  return (
    <div className="bg-white rounded-lg border border-gray-100 p-3 flex items-start gap-3">
      <span className="text-2xl font-black text-gray-200 leading-none w-6 shrink-0">{rank}</span>
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-slate-800 text-sm truncate">{name}</p>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-xs text-gray-500">{count} liked listing{count !== 1 ? 's' : ''}</span>
          {avgPrice && (
            <span className="text-xs text-gray-400">· avg €{fmt(avgPrice)}</span>
          )}
        </div>
        {pctOfBudget && (
          <div className="mt-1.5 h-1 bg-gray-100 rounded-full overflow-hidden w-full">
            <div
              className={`h-full rounded-full ${pctOfBudget <= 100 ? 'bg-emerald-400' : 'bg-amber-400'}`}
              style={{ width: `${Math.min(pctOfBudget, 100)}%` }}
            />
          </div>
        )}
      </div>
    </div>
  )
}

export default function MyOverviewTab({ liked, disliked, all, onViewListing, onGoToShortlist, budget }) {
  const stats = useMemo(() => {
    const total = all.length
    const likedCount = liked.length
    const dislikedCount = disliked.length
    const now = Date.now()
    const weekMs = 7 * 24 * 60 * 60 * 1000
    const thisWeek = all.filter(r => new Date(r.updated_at || r.created_at).getTime() > now - weekMs).length

    const likedPrices = liked
      .map(r => r.listing?.price_amount)
      .filter(p => p != null && p > 0)
    const avgPrice = likedPrices.length ? likedPrices.reduce((a, b) => a + b, 0) / likedPrices.length : null
    const minPrice = likedPrices.length ? Math.min(...likedPrices) : null
    const maxPrice = likedPrices.length ? Math.max(...likedPrices) : null

    return { total, likedCount, dislikedCount, thisWeek, avgPrice, minPrice, maxPrice }
  }, [liked, disliked, all])

  const topNeighborhoods = useMemo(() => {
    const counts = {}
    const prices = {}
    for (const r of liked) {
      const n = r.listing?.neighborhood || r.listing?.parish
      if (!n) continue
      counts[n] = (counts[n] || 0) + 1
      if (r.listing?.price_amount > 0) {
        if (!prices[n]) prices[n] = []
        prices[n].push(r.listing.price_amount)
      }
    }
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([name, count]) => {
        const ps = prices[name] || []
        const avgPrice = ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : null
        return { name, count, avgPrice }
      })
  }, [liked])

  const shortlistPreview = liked.slice(0, 3)

  return (
    <div className="p-4 space-y-5">

      {/* Decision stats */}
      <section>
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Activity</h2>
        <div className="grid grid-cols-3 gap-2">
          <StatCard label="Liked" value={stats.likedCount} color="text-emerald-600" />
          <StatCard label="Passed" value={stats.dislikedCount} color="text-slate-500" />
          <StatCard label="This week" value={stats.thisWeek} sub="reactions" />
        </div>
      </section>

      {/* Price range of liked listings */}
      {stats.likedCount > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Your liked listings</h2>
          <div className="bg-white rounded-lg border border-gray-100 p-3 space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-gray-500">Price range</span>
              <span className="font-medium text-slate-700">
                €{fmt(stats.minPrice)} – €{fmt(stats.maxPrice)}
              </span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-gray-500">Average price</span>
              <span className="font-medium text-slate-700">€{fmt(stats.avgPrice)}</span>
            </div>
          </div>
        </section>
      )}

      {/* Top neighborhoods */}
      {topNeighborhoods.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Top neighborhoods for you</h2>
          <div className="space-y-2">
            {topNeighborhoods.map((n, i) => (
              <NeighborhoodCard
                key={n.name}
                rank={i + 1}
                name={n.name}
                count={n.count}
                avgPrice={n.avgPrice}
                budget={budget}
              />
            ))}
          </div>
          <p className="text-[10px] text-gray-400 mt-1.5">Based on your liked listings.</p>
        </section>
      )}

      {/* Shortlist preview */}
      {shortlistPreview.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Shortlist</h2>
            {liked.length > 3 && (
              <button
                onClick={onGoToShortlist}
                className="text-xs text-primary hover:underline cursor-pointer"
              >
                View all {liked.length} →
              </button>
            )}
          </div>
          <div className="space-y-2">
            {shortlistPreview.map(item => (
              <ListingRefCard
                key={`${item.listing_kind}-${item.listing_id}`}
                listing={item.listing}
                listingKind={item.listing_kind}
                comment={item.comment}
                updatedAt={item.updated_at || item.created_at}
                onViewOnMap={item.listing && onViewListing ? onViewListing : null}
              />
            ))}
          </div>
        </section>
      )}

      {stats.likedCount === 0 && stats.dislikedCount === 0 && (
        <div className="text-center py-10 text-gray-400">
          <p className="text-3xl mb-2">👆</p>
          <p className="text-sm">Like or pass on listings to build your overview.</p>
        </div>
      )}

    </div>
  )
}
