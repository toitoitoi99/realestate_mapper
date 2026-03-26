import { useState } from 'react'
import { useLanguage } from '../LanguageContext'
import RarityBadge from './RarityBadge'
import AmenityRating from './AmenityRating'

export default function ListingDetail({ listing, onBack }) {
  const { t } = useLanguage()
  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  const images = (() => {
    try { return JSON.parse(listing.images) }
    catch { return [] }
  })()
  const [imgIdx, setImgIdx] = useState(0)

  const isRent = listing.listing_type === 'rent'
  const isSold = listing.status === 'sold'
  const isReserved = listing.status === 'reserved'

  const details = [
    { label: t.rooms,        value: listing.rooms != null ? `T${listing.rooms}` : null },
    { label: t.bedrooms,     value: listing.bedrooms },
    { label: t.bathrooms,    value: listing.bathrooms },
    { label: t.size,         value: listing.size_sqm ? `${fmt(listing.size_sqm)} m²` : null },
    { label: t.floor,        value: listing.floor },
    { label: t.condition,    value: listing.condition },
    { label: t.propertyType, value: listing.property_type },
    { label: `€/m²`,        value: listing.price_per_sqm ? `€${fmt(listing.price_per_sqm)}` : null },
  ].filter(d => d.value != null)

  const location = [
    { label: t.address,      value: listing.address },
    { label: t.postalCode,   value: listing.postal_code },
    { label: t.neighborhoods, value: listing.neighborhood },
    { label: t.parish,       value: listing.parish },
  ].filter(d => d.value)

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200 shrink-0">
        <button
          onClick={onBack}
          className="text-sm text-blue-600 hover:text-blue-800 font-medium cursor-pointer"
        >
          ← {t.back}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Image */}
        {images.length > 0 ? (
          <div className="relative bg-gray-100">
            <img
              src={images[imgIdx]}
              alt=""
              className="w-full h-48 object-cover"
            />
            {images.length > 1 && (
              <div className="absolute bottom-2 left-0 right-0 flex justify-center gap-1">
                {images.slice(0, 8).map((_, i) => (
                  <button
                    key={i}
                    onClick={() => setImgIdx(i)}
                    className={`w-2 h-2 rounded-full cursor-pointer ${i === imgIdx ? 'bg-white' : 'bg-white/50'}`}
                  />
                ))}
                {images.length > 8 && <span className="text-white text-xs ml-1">+{images.length - 8}</span>}
              </div>
            )}
            {images.length > 1 && (
              <>
                <button
                  onClick={() => setImgIdx(i => (i - 1 + images.length) % images.length)}
                  className="absolute left-1 top-1/2 -translate-y-1/2 bg-black/30 text-white w-7 h-7 rounded-full flex items-center justify-center text-sm cursor-pointer hover:bg-black/50"
                >&#8249;</button>
                <button
                  onClick={() => setImgIdx(i => (i + 1) % images.length)}
                  className="absolute right-1 top-1/2 -translate-y-1/2 bg-black/30 text-white w-7 h-7 rounded-full flex items-center justify-center text-sm cursor-pointer hover:bg-black/50"
                >&#8250;</button>
              </>
            )}
          </div>
        ) : (
          <div className="bg-gray-100 h-32 flex items-center justify-center text-gray-400 text-sm">
            {t.noImages}
          </div>
        )}

        <div className="p-4 flex flex-col gap-4">
          {/* Title + Price */}
          <div>
            {(isSold || isReserved) && (
              <span className="inline-block text-xs font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded mb-1 uppercase tracking-wide">
                {isSold ? t.sold : t.reserved}
              </span>
            )}
            <h2 className="font-bold text-gray-900 text-base leading-snug">
              {listing.title || `T${listing.rooms ?? '?'} ${t.inArea(listing.neighborhood || 'Lisboa')}`}
            </h2>
            <div className="text-xl font-bold text-blue-700 mt-1">
              €{fmt(listing.price_amount)}{isRent ? '/mo' : ''}
            </div>
          </div>

          {/* Rarity */}
          <RarityBadge score={listing.rarity_score} factors={listing.rarity_factors} />

          {/* Property details */}
          {details.length > 0 && (
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              {details.map(d => (
                <div key={d.label}>
                  <span className="text-gray-500">{d.label}</span>
                  <div className="font-medium text-gray-900">{d.value}</div>
                </div>
              ))}
            </div>
          )}

          {/* Location */}
          {location.length > 0 && (
            <div className="text-sm">
              <h3 className="font-semibold text-gray-700 mb-1">{t.address}</h3>
              {location.map(d => (
                <div key={d.label} className="text-gray-600">
                  {d.value}
                </div>
              ))}
            </div>
          )}

          {/* Amenity Rating */}
          <AmenityRating lat={listing.lat} lon={listing.lon} />

          {/* Description */}
          {listing.description && (
            <div className="text-sm">
              <h3 className="font-semibold text-gray-700 mb-1">{t.description}</h3>
              <p className="text-gray-600 whitespace-pre-line text-xs leading-relaxed">
                {listing.description}
              </p>
            </div>
          )}

          {/* External link */}
          <a
            href={listing.url}
            target="_blank"
            rel="noopener noreferrer"
            className="block text-center bg-blue-600 text-white text-sm font-medium py-2 px-4 rounded-lg hover:bg-blue-700 transition-colors"
          >
            {t.viewOnSource}
          </a>

          {/* Scraped date */}
          {listing.scraped_at && (
            <div className="text-xs text-gray-400 text-center">
              {t.scrapedAt}: {new Date(listing.scraped_at).toLocaleDateString()}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
