import { useLanguage } from '../LanguageContext'
import GrantBadge from './GrantBadge'
import { FlipRentBadges } from './FlipRentScorecard'
import SourceLogo from './SourceLogo'

export default function ListingCard({ listing, onSelect, highlighted }) {
  const { t } = useLanguage()
  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  return (
    <div
      onClick={() => onSelect(listing)}
      className={`block p-3 rounded-lg transition-all text-left cursor-pointer ${
        highlighted
          ? 'bg-blue-50 border-2 border-blue-400 shadow-sm'
          : 'border border-gray-100 hover:border-blue-300 hover:shadow-sm'
      }`}
    >
      <div className="flex justify-between items-start gap-2 mb-1">
        <span className="font-semibold text-gray-900 text-sm leading-tight line-clamp-2">
          {listing.title || `T${listing.rooms ?? '?'} ${t.inArea(listing.neighborhood || 'Lisboa')}`}
        </span>
        <span className="shrink-0 text-blue-700 font-bold text-sm">
          €{fmt(listing.price_amount)}
        </span>
      </div>

      <div className="flex gap-3 text-xs text-gray-500 mt-1">
        {listing.rooms != null && <span>T{listing.rooms}</span>}
        {listing.size_sqm && <span>{fmt(listing.size_sqm)} m²</span>}
        {listing.price_per_sqm && <span>€{fmt(listing.price_per_sqm)}/m²</span>}
      </div>

      <div className="flex items-center justify-between mt-1">
        {listing.neighborhood && (
          <span className="text-xs text-gray-400 truncate">{listing.neighborhood}</span>
        )}
        <span className="ml-auto"><SourceLogo source={listing.source} /></span>
      </div>

      <FlipRentBadges flip={listing.flip_score} rent={listing.rent_score} />
      <GrantBadge eligible={listing.grant_eligible} />
    </div>
  )
}
