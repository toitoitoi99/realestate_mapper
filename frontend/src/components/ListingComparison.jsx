import { useState, useEffect, useRef } from 'react'
import { useLanguage } from '../LanguageContext'
import { fetchListingComparison, fetchAddressHistory } from '../api'

export default function ListingComparison({ listing, radiusM: externalRadius, onRadiusChange }) {
  const { t } = useLanguage()
  const [internalRadius, setInternalRadius] = useState(500)
  const radiusM = externalRadius ?? internalRadius
  const setRadiusM = onRadiusChange ?? setInternalRadius
  const [matchType, setMatchType] = useState(true)    // filter by property_type
  const [matchBeds, setMatchBeds] = useState(true)     // filter by bedrooms
  const [data, setData] = useState(null)
  const [addressData, setAddressData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [addressLoading, setAddressLoading] = useState(false)
  const debounceRef = useRef(null)

  // Fetch comparison data with debounce on radius changes
  useEffect(() => {
    if (!listing?.id || !listing?.lat || !listing?.lon) return
    setLoading(true)

    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      const lt = listing.listing_type || 'sale'
      const pt = matchType ? listing.property_type : null
      const beds = matchBeds ? listing.bedrooms : null
      fetchListingComparison(listing.id, lt, radiusM, pt, beds)
        .then(d => setData(d))
        .catch(() => setData(null))
        .finally(() => setLoading(false))
    }, 300)

    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [listing?.id, radiusM, matchType, matchBeds])

  // Fetch address history (no debounce needed)
  useEffect(() => {
    if (!listing?.id) return
    setAddressLoading(true)
    fetchAddressHistory(listing.id, listing.listing_type || 'sale')
      .then(d => setAddressData(d))
      .catch(() => setAddressData(null))
      .finally(() => setAddressLoading(false))
  }, [listing?.id])

  if (!listing?.lat || !listing?.lon) return null

  const stats = data?.comparables?.stats || {}
  const comparables = data?.comparables?.listings || []
  const rentals = data?.rentals || {}
  const history = data?.history || []
  const addressMatches = addressData?.matches || []
  const tp = listing.price_per_sqm

  // Position on range bar (0-100%)
  const rangeMin = stats.min_price_per_sqm || 0
  const rangeMax = stats.max_price_per_sqm || 1
  const rangeSpan = rangeMax - rangeMin || 1
  const listingPos = tp ? Math.min(100, Math.max(0, ((tp - rangeMin) / rangeSpan) * 100)) : 50
  const medianPos = stats.median_price_per_sqm ? ((stats.median_price_per_sqm - rangeMin) / rangeSpan) * 100 : 50

  // Color based on position vs median
  const posColor = tp && stats.median_price_per_sqm
    ? (tp < stats.median_price_per_sqm ? 'text-green-600' : tp > stats.median_price_per_sqm ? 'text-red-600' : 'text-yellow-600')
    : 'text-gray-600'

  return (
    <div className="text-sm space-y-4">
      {/* Section: Price Comparison */}
      <details open className="group">
        <summary className="font-semibold text-gray-700 mb-2 cursor-pointer list-none flex items-center gap-1">
          <span className="inline-block transition-transform group-open:rotate-90 text-gray-400">&rsaquo;</span>
          {t.priceComparison || 'Price Comparison'}
        </summary>

        {/* Radius slider */}
        <div className="mb-3">
          <label className="text-xs text-gray-500 block mb-1">
            {t.radiusLabel || 'Radius'}: {radiusM}m
          </label>
          <input
            type="range"
            min={250}
            max={2000}
            step={50}
            value={radiusM}
            onChange={e => setRadiusM(Number(e.target.value))}
            className="w-full h-1.5 bg-gray-200 rounded-full appearance-none cursor-pointer accent-blue-600"
          />
          <div className="flex justify-between text-[10px] text-gray-400 mt-0.5">
            <span>250m</span><span>1km</span><span>2km</span>
          </div>
        </div>

        {/* Filter toggles */}
        <div className="flex gap-3 mb-3">
          <label className="flex items-center gap-1 text-xs text-gray-600 cursor-pointer">
            <input type="checkbox" checked={matchType} onChange={e => setMatchType(e.target.checked)}
              className="rounded border-gray-300 text-blue-600 w-3.5 h-3.5" />
            {t.filterByType || 'Same type'}
          </label>
          <label className="flex items-center gap-1 text-xs text-gray-600 cursor-pointer">
            <input type="checkbox" checked={matchBeds} onChange={e => setMatchBeds(e.target.checked)}
              className="rounded border-gray-300 text-blue-600 w-3.5 h-3.5" />
            {t.filterByBedrooms || 'Same bedrooms'}
          </label>
        </div>

        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3].map(i => <div key={i} className="h-4 bg-gray-200 rounded animate-pulse" />)}
          </div>
        ) : data?.comparables?.count > 0 ? (
          <>
            {/* Stats card */}
            <div className="bg-gray-50 rounded-lg p-3 mb-3">
              <div className="text-xs text-gray-500 mb-2">
                {t.basedOn || 'Based on'} {data.comparables.count} {t.similarProperties || 'similar properties'} {t.within || 'within'} {radiusM}m
              </div>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="font-semibold text-gray-800">
                  €{stats.median_price_per_sqm?.toLocaleString()}/m²
                </span>
                <span className="text-xs text-gray-500">{t.medianLabel || 'median'}</span>
              </div>

              {/* Range bar */}
              {tp && stats.min_price_per_sqm != null && (
                <div className="relative mt-2 mb-1">
                  <div className="h-2 bg-gradient-to-r from-green-200 via-yellow-200 to-red-200 rounded-full" />
                  {/* Median marker */}
                  <div className="absolute top-0 h-2 w-0.5 bg-gray-500" style={{ left: `${medianPos}%` }} />
                  {/* Listing position */}
                  <div
                    className="absolute -top-1 w-4 h-4 rounded-full border-2 border-white shadow bg-blue-600"
                    style={{ left: `calc(${listingPos}% - 8px)` }}
                  />
                  <div className="flex justify-between text-[10px] text-gray-400 mt-1">
                    <span>€{stats.min_price_per_sqm?.toLocaleString()}</span>
                    <span>€{stats.max_price_per_sqm?.toLocaleString()}</span>
                  </div>
                </div>
              )}

              {/* Percentile text */}
              {stats.listing_percentile != null && (
                <div className={`text-xs font-medium mt-1 ${posColor}`}>
                  {stats.listing_percentile < 50
                    ? `${t.belowMedian || 'Below median'} — ${t.cheaperThan || 'cheaper than'} ${Math.round(100 - stats.listing_percentile)}% ${t.ofNearby || 'of nearby'}`
                    : `${t.aboveMedian || 'Above median'} — ${t.moreExpensiveThan || 'more expensive than'} ${Math.round(stats.listing_percentile)}% ${t.ofNearby || 'of nearby'}`
                  }
                </div>
              )}
            </div>

            {/* Comparable listings mini-list */}
            <details className="group">
              <summary className="text-xs text-blue-600 cursor-pointer hover:underline">
                {t.showComparables || 'Show comparable listings'} ({comparables.length})
              </summary>
              <div className="mt-2 space-y-1.5 max-h-40 overflow-y-auto">
                {comparables.slice(0, 15).map(c => (
                  <div key={`${c.source}-${c.id}`} className="flex items-center gap-2 text-xs text-gray-600 py-1 border-b border-gray-100">
                    <span className="bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded text-[10px] font-medium w-12 text-center">
                      {c.distance_m}m
                    </span>
                    <span className="flex-1 truncate">{c.address || 'Unknown'}</span>
                    <span className="font-medium whitespace-nowrap">€{c.price_per_sqm?.toLocaleString()}/m²</span>
                    {c.status === 'sold' && (
                      <span className="bg-red-100 text-red-700 px-1 py-0.5 rounded text-[10px]">sold</span>
                    )}
                  </div>
                ))}
              </div>
            </details>
          </>
        ) : (
          <div className="text-xs text-gray-400">{t.noComparables || 'No comparable listings found in this radius'}</div>
        )}
      </details>

      {/* Section: Rental Yield */}
      {rentals.count > 0 && (
        <div>
          <h3 className="font-semibold text-gray-700 mb-2">{t.rentalYield || 'Rental Yield Estimate'}</h3>
          <div className="bg-green-50 rounded-lg p-3">
            <div className="grid grid-cols-2 gap-2 text-xs">
              {rentals.stats?.source === 'sales' ? (
                /* Viewing a rental listing: show nearby sale prices */
                <>
                  <div>
                    <div className="text-gray-500">{t.avgSaleSqm || 'Avg sale price/m²'}</div>
                    <div className="font-medium text-gray-800">€{rentals.stats?.avg_sale_per_sqm?.toFixed(0)}/m²</div>
                  </div>
                  <div>
                    <div className="text-gray-500">{t.medianSaleSqm || 'Median sale price/m²'}</div>
                    <div className="font-medium text-gray-800">€{rentals.stats?.median_sale_per_sqm?.toFixed(0)}/m²</div>
                  </div>
                  {rentals.stats?.estimated_purchase_price > 0 && (
                    <div>
                      <div className="text-gray-500">{t.estimatedPurchase || 'Est. purchase price'}</div>
                      <div className="font-medium text-gray-800">€{Math.round(rentals.stats.estimated_purchase_price).toLocaleString()}</div>
                    </div>
                  )}
                </>
              ) : (
                /* Viewing a sale listing: show nearby rental prices */
                <>
                  <div>
                    <div className="text-gray-500">{t.avgRentSqm || 'Avg rent/m²'}</div>
                    <div className="font-medium text-gray-800">€{rentals.stats?.avg_rent_per_sqm?.toFixed(2)}/m²</div>
                  </div>
                  <div>
                    <div className="text-gray-500">{t.medianRentSqm || 'Median rent/m²'}</div>
                    <div className="font-medium text-gray-800">€{rentals.stats?.median_rent_per_sqm?.toFixed(2)}/m²</div>
                  </div>
                  {rentals.stats?.estimated_monthly_rent > 0 && (
                    <div>
                      <div className="text-gray-500">{t.estimatedRent || 'Est. monthly rent'}</div>
                      <div className="font-medium text-gray-800">€{Math.round(rentals.stats.estimated_monthly_rent).toLocaleString()}</div>
                    </div>
                  )}
                </>
              )}
              {rentals.stats?.gross_yield_pct != null && (
                <div>
                  <div className="text-gray-500">{t.grossYield || 'Gross yield'}</div>
                  <div className="font-bold text-green-700 text-base">{rentals.stats.gross_yield_pct}%</div>
                </div>
              )}
            </div>
            <div className="text-[10px] text-gray-400 mt-2">
              {t.basedOn || 'Based on'} {rentals.count} {rentals.stats?.source === 'sales' ? (t.nearbySales || 'nearby sales') : (t.nearbyRentals || 'nearby rentals')} {t.within || 'within'} {radiusM}m
            </div>
          </div>
        </div>
      )}

      {/* Section: Price History */}
      {history.length > 0 && (
        <div>
          <h3 className="font-semibold text-gray-700 mb-2">{t.priceHistory || 'Price History'}</h3>
          <div className="space-y-1.5">
            {history.map((h, i) => (
              <div key={i} className="flex items-center gap-2 text-xs py-1 border-b border-gray-100">
                <span className="text-gray-400 w-20 shrink-0">
                  {new Date(h.changed_at).toLocaleDateString()}
                </span>
                <span className="text-gray-600 capitalize">{h.field.replace('_', ' ')}</span>
                <span className="text-red-500 line-through">{h.old_value}</span>
                <span className="text-gray-400">→</span>
                <span className="text-green-600 font-medium">{h.new_value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Section: Address History */}
      {addressMatches.length > 0 && (
        <div>
          <h3 className="font-semibold text-gray-700 mb-2">{t.addressHistory || 'Address History'}</h3>
          <div className="text-[10px] text-gray-400 mb-1">
            {t.previousListings || 'Other listings at this address'}
          </div>
          <div className="space-y-1.5 max-h-36 overflow-y-auto">
            {addressMatches.map((m, i) => (
              <div key={i} className="flex items-center gap-2 text-xs py-1 border-b border-gray-100">
                <span className="bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded text-[10px] font-medium">
                  {m.source}
                </span>
                <span className="text-gray-400 w-16 shrink-0">
                  {m.scraped_at ? new Date(m.scraped_at).toLocaleDateString() : '—'}
                </span>
                <span className="flex-1">
                  {m.price_amount ? `€${Math.round(m.price_amount).toLocaleString()}` : '—'}
                </span>
                {m.price_per_sqm && (
                  <span className="text-gray-500">€{Math.round(m.price_per_sqm)}/m²</span>
                )}
                {m.status === 'sold' && (
                  <span className="bg-red-100 text-red-700 px-1 py-0.5 rounded text-[10px]">sold</span>
                )}
                <span className={`px-1 py-0.5 rounded text-[10px] ${
                  m.listing_type === 'rent' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'
                }`}>
                  {m.listing_type === 'rent' ? 'rent' : 'sale'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
