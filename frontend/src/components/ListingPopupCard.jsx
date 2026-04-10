import { useEffect, useRef, useState } from 'react'
import { useLanguage } from '../LanguageContext'
import RarityBadge from './RarityBadge'
import GrantBadge from './GrantBadge'
import SourceLogo from './SourceLogo'
import { fetchDealScore } from '../api'

// Module-level cache so re-opening a popup doesn't re-fetch.
const yieldCache = new Map()

function ratingLetter(score) {
  if (score == null) return null
  if (score >= 80) return 'A'
  if (score >= 60) return 'B'
  if (score >= 40) return 'C'
  return 'D'
}

function ratingClasses(letter) {
  if (letter === 'A') return 'bg-emerald-600 text-white'
  if (letter === 'B') return 'bg-green-600 text-white'
  if (letter === 'C') return 'bg-amber-600 text-white'
  if (letter === 'D') return 'bg-red-600 text-white'
  return 'bg-gray-300 text-gray-700'
}

function ScorePill({ label, score }) {
  const letter = ratingLetter(score)
  return (
    <div className="flex items-center gap-1">
      <span className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</span>
      <span className={`text-[10px] font-bold px-1 rounded ${ratingClasses(letter)}`}>
        {letter || '—'}
      </span>
      <span className="text-xs font-semibold text-gray-800 tabular-nums">
        {score != null ? Math.round(score) : '—'}
      </span>
    </div>
  )
}

export default function ListingPopupCard({ listing: l }) {
  const { t } = useLanguage()
  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  const isRent = l.listing_type === 'rent'
  const isSold = l.status === 'sold'
  const isReserved = l.status === 'reserved'
  const listingType = isRent ? 'rent' : 'sale'

  // First image as thumbnail
  const thumbnail = (() => {
    if (!l.images) return null
    try {
      const arr = typeof l.images === 'string' ? JSON.parse(l.images) : l.images
      return Array.isArray(arr) && arr.length > 0 ? arr[0] : null
    } catch { return null }
  })()
  const [imgError, setImgError] = useState(false)

  // Lazy-fetch gross yield (not stored on the listing row).
  const cacheKey = `${listingType}:${l.id}`
  const [yieldPct, setYieldPct] = useState(() => yieldCache.get(cacheKey) ?? null)
  const [yieldLoading, setYieldLoading] = useState(false)
  const cancelledRef = useRef(false)

  useEffect(() => {
    cancelledRef.current = false
    // Only sales have a meaningful yield calc.
    if (isRent) return
    if (yieldCache.has(cacheKey)) {
      setYieldPct(yieldCache.get(cacheKey))
      return
    }
    setYieldLoading(true)
    fetchDealScore(l.id, listingType, 500)
      .then(result => {
        if (cancelledRef.current) return
        const y = result?.dimensions?.yield?.detail
        // detail looks like "5.2% gross yield, est. €1,234/mo rent"
        let pct = null
        if (typeof y === 'string') {
          const m = y.match(/([\d.]+)%/)
          if (m) pct = parseFloat(m[1])
        }
        yieldCache.set(cacheKey, pct)
        setYieldPct(pct)
      })
      .catch(() => {
        if (!cancelledRef.current) setYieldPct(null)
      })
      .finally(() => {
        if (!cancelledRef.current) setYieldLoading(false)
      })
    return () => { cancelledRef.current = true }
  }, [cacheKey, isRent, l.id, listingType])

  const typeLabel = l.property_type
    ? l.property_type.charAt(0).toUpperCase() + l.property_type.slice(1)
    : null

  return (
    <div className="text-sm min-w-[230px] max-w-[260px]">
      {/* Thumbnail */}
      {thumbnail && !imgError && (
        <div className="-mx-[13px] -mt-[13px] mb-2 overflow-hidden rounded-t">
          <img
            src={thumbnail}
            alt={l.title || 'Listing'}
            onError={() => setImgError(true)}
            className="w-full h-[120px] object-cover block"
            loading="lazy"
          />
        </div>
      )}

      {/* Header: status + price */}
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="flex flex-col">
          {(isSold || isReserved) && (
            <span className="text-[10px] font-semibold text-amber-700 uppercase tracking-wide">
              {isSold ? t.sold : t.reserved}
            </span>
          )}
          <span className="text-xs text-gray-600">
            {[typeLabel, l.rooms != null ? `T${l.rooms}` : null, l.size_sqm ? `${fmt(l.size_sqm)} m²` : null]
              .filter(Boolean).join(' · ')}
          </span>
        </div>
        <div className="text-right shrink-0">
          <div className="font-bold text-gray-900 leading-tight">
            €{fmt(l.price_amount)}{isRent ? '/mo' : ''}
          </div>
          {l.price_per_sqm && (
            <div className="text-[10px] text-gray-500 tabular-nums">
              €{fmt(l.price_per_sqm)}/m²
            </div>
          )}
        </div>
      </div>

      {/* Scores row */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 my-1 border-y border-gray-100">
        <ScorePill label="Deal" score={l.deal_score} />
        <ScorePill label="Property" score={l.property_score} />
        {!isRent && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-gray-500 uppercase tracking-wide">Yield</span>
            <span className="text-xs font-semibold text-gray-800 tabular-nums">
              {yieldLoading ? '…' : (yieldPct != null ? `${yieldPct.toFixed(1)}%` : '—')}
            </span>
          </div>
        )}
      </div>

      {/* Condition + location */}
      <div className="flex items-center gap-2 text-[11px] text-gray-500">
        {l.condition && <span className="capitalize">{l.condition}</span>}
        {l.condition && (l.neighborhood || l.parish) && <span>·</span>}
        {(l.neighborhood || l.parish) && (
          <span className="truncate">{l.neighborhood || l.parish}</span>
        )}
      </div>

      {/* Badges */}
      <div className="flex flex-wrap items-center gap-1 mt-1">
        <RarityBadge score={l.rarity_score} factors={l.rarity_factors} compact />
        <GrantBadge eligible={l.grant_eligible} />
      </div>

      {/* Footer: source + link */}
      <div className="flex items-center justify-between mt-2 pt-1.5 border-t border-gray-100">
        <SourceLogo source={l.source} />
        <a
          href={l.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-600 text-xs font-medium hover:underline"
        >
          {t.viewListing}
        </a>
      </div>
    </div>
  )
}
