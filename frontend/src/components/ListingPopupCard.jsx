import { useState } from 'react'
import { useLanguage } from '../LanguageContext'
import { FlipRentBadges } from './FlipRentScorecard'
import GrantBadge from './GrantBadge'
import SourceLogo from './SourceLogo'

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
      <div className="py-1.5 my-1 border-y border-gray-100">
        <FlipRentBadges flip={l.flip_score} rent={l.rent_score} />
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
        <GrantBadge eligible={l.grant_eligible} />
      </div>

      {/* Footer: source + link */}
      <div className="flex items-center justify-between mt-2 pt-1.5 border-t border-gray-100">
        <SourceLogo source={l.source} />
        <a
          href={l.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary text-xs font-medium hover:underline"
        >
          {t.viewListing}
        </a>
      </div>
    </div>
  )
}
