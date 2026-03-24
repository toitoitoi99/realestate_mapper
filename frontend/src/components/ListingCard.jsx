import { useLanguage } from '../LanguageContext'
import RarityBadge from './RarityBadge'

export default function ListingCard({ listing, onSelect }) {
  const { t } = useLanguage()
  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  return (
    <div
      onClick={() => onSelect(listing)}
      className="block p-3 border border-gray-100 rounded-lg hover:border-blue-300 hover:shadow-sm transition-all text-left cursor-pointer"
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

      {listing.neighborhood && (
        <div className="text-xs text-gray-400 mt-1 truncate">{listing.neighborhood}</div>
      )}

      <RarityBadge score={listing.rarity_score} factors={listing.rarity_factors} />
    </div>
  )
}
