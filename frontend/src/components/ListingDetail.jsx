import { useState, useEffect } from 'react'
import { useLanguage } from '../LanguageContext'
import { translateDescription, fetchListingDetail, fetchNearbyProjects } from '../api'
import FlipRentScorecard from './FlipRentScorecard'
import AmenityRating from './AmenityRating'
import ListingComparison from './ListingComparison'
import SourceLogo from './SourceLogo'
import ReactionButtons from './ReactionButtons'

function PriceDiff({ current, other }) {
  if (!current || !other) return null
  const diff = ((other - current) / current) * 100
  if (Math.abs(diff) < 0.1) return null
  const color = diff > 0 ? 'text-red-500' : 'text-green-600'
  const sign = diff > 0 ? '+' : ''
  return <span className={`text-xs ml-1 ${color}`}>{sign}{diff.toFixed(1)}%</span>
}

/** Parse floor field — handles raw strings like "['floor_3']", "floor_3", "3", etc. */
function parseFloor(raw) {
  if (raw == null) return null
  let s = String(raw).trim()
  // Strip list brackets and quotes
  s = s.replace(/^\[?'?"?/, '').replace(/'?"?\]?$/, '')
  // Extract number from "floor_N" pattern
  const m = s.match(/floor[_\s]*(\d+)/i)
  if (m) return m[1]
  // Already a clean value (number, "RC", etc.)
  if (s && s !== 'null' && s !== 'undefined') return s
  return null
}

/** Days between scraped_at and now */
function daysAgo(scrapedAt) {
  if (!scrapedAt) return null
  const scraped = new Date(scrapedAt)
  const now = new Date()
  return Math.max(0, Math.round((now - scraped) / 86_400_000))
}

/** Format percentage diff with color */
function PctBadge({ pct, invert = false }) {
  if (pct == null || !isFinite(pct)) return null
  const isNeg = pct < 0
  const color = (invert ? !isNeg : isNeg) ? 'text-green-600' : 'text-red-500'
  const sign = pct > 0 ? '+' : ''
  return <span className={`text-xs font-semibold ${color}`}>{sign}{pct.toFixed(0)}%</span>
}

export default function ListingDetail({ listing, onBack, parishStats, ineStats, reaction, onSetReaction, onClearReaction }) {
  const { lang, t } = useLanguage()
  const [descriptionEn, setDescriptionEn] = useState(null)
  const [translating, setTranslating] = useState(false)
  const [crossListings, setCrossListings] = useState([])
  const [previousPrice, setPreviousPrice] = useState(null)
  const [nearbyProjects, setNearbyProjects] = useState(null)
  const [radiusM, setRadiusM] = useState(500)

  useEffect(() => {
    if (lang !== 'en' || !listing.description) {
      setDescriptionEn(null)
      return
    }
    let cancelled = false
    setTranslating(true)
    translateDescription(listing.id, listing.listing_type || 'sale')
      .then(data => {
        if (!cancelled) setDescriptionEn(data.description_en)
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setTranslating(false) })
    return () => { cancelled = true }
  }, [listing.id, listing.listing_type, listing.description, lang])

  useEffect(() => {
    let cancelled = false
    setCrossListings([])
    setPreviousPrice(null)
    fetchListingDetail(listing.id, listing.listing_type || 'sale')
      .then(data => {
        if (!cancelled) {
          setCrossListings(data.cross_listings || [])
          if (data.previous_price_amount) setPreviousPrice(data.previous_price_amount)
        }
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [listing.id, listing.listing_type])

  // Fetch nearby construction projects
  useEffect(() => {
    if (!listing?.lat || !listing?.lon) return
    let cancelled = false
    fetchNearbyProjects(listing.lat, listing.lon, 500)
      .then(data => { if (!cancelled) setNearbyProjects(data) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [listing?.lat, listing?.lon])

  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  const images = (() => {
    try { return JSON.parse(listing.images) }
    catch { return [] }
  })()
  const [imgIdx, setImgIdx] = useState(0)

  const isRent = listing.listing_type === 'rent'
  const isSold = listing.status === 'sold'
  const isReserved = listing.status === 'reserved'

  // --- Computed values ---
  const days = daysAgo(listing.scraped_at)
  const floorClean = parseFloor(listing.floor)
  const tp = listing.price_per_sqm

  // Parish median comparison — try multiple fields since scraper data is inconsistent
  const parishCandidates = [listing.parish, listing.city, listing.neighborhood, listing.district].filter(Boolean)
  const parishData = parishStats?.stats || parishStats || {}
  let parishMedianPsm = null
  let parishDiffPct = null
  let matchedParish = null
  if (tp && Object.keys(parishData).length > 0) {
    for (const candidate of parishCandidates) {
      // Try exact match
      if (parishData[candidate]) {
        matchedParish = candidate
        break
      }
      // Try partial match
      const found = Object.entries(parishData).find(([k]) =>
        k.toLowerCase().includes(candidate.toLowerCase()) ||
        candidate.toLowerCase().includes(k.toLowerCase())
      )
      if (found) {
        matchedParish = found[0]
        break
      }
    }
    if (matchedParish && parishData[matchedParish]?.median_price_per_sqm) {
      parishMedianPsm = parishData[matchedParish].median_price_per_sqm
      parishDiffPct = ((tp - parishMedianPsm) / parishMedianPsm) * 100
    }
  }

  // INE sold price comparison
  let ineSoldPsm = null
  let ineDiffPct = null
  if (tp && ineStats?.stats) {
    // Use "Total" category for the municipality
    const total = ineStats.stats.find(s => s.category === 'Total' || s.category_label?.includes('Total'))
    if (total?.median_price_per_sqm) {
      ineSoldPsm = total.median_price_per_sqm
      ineDiffPct = ((tp - ineSoldPsm) / ineSoldPsm) * 100
    }
  }

  // Re-list price drop
  const priceDropPct = previousPrice && listing.price_amount
    ? ((listing.price_amount - previousPrice) / previousPrice) * 100
    : null

  // Gross/living area
  const grossArea = listing.gross_area_sqm
  const livingArea = listing.size_sqm
  const areaEfficiency = grossArea && livingArea && grossArea > 0
    ? Math.round((livingArea / grossArea) * 100)
    : null

  // Property details grid
  const details = [
    { label: t.rooms,        value: listing.rooms != null ? `T${listing.rooms}` : null },
    { label: t.bedrooms,     value: listing.bedrooms },
    { label: t.bathrooms,    value: listing.bathrooms },
    { label: t.livingArea || 'Living area', value: livingArea ? `${fmt(livingArea)} m²` : null },
    ...(grossArea && grossArea !== livingArea ? [{
      label: t.grossArea || 'Gross area',
      value: `${fmt(grossArea)} m²${areaEfficiency ? ` (${areaEfficiency}% ${t.efficiency || 'eff.'})` : ''}`
    }] : []),
    { label: t.floor,        value: floorClean },
    { label: t.condition,    value: listing.condition },
    { label: t.propertyType, value: listing.property_type },
    { label: `€/m²`,        value: tp ? `€${fmt(tp)}` : null },
  ].filter(d => d.value != null)

  const location = [
    { label: t.address,      value: listing.address },
    { label: t.postalCode,   value: listing.postal_code },
    { label: t.neighborhoods, value: listing.neighborhood },
    { label: t.parish,       value: listing.parish },
  ].filter(d => d.value)

  // Sanitize description HTML
  const descriptionHtml = (() => {
    const raw = lang === 'en' && descriptionEn ? descriptionEn : listing.description
    if (!raw) return null
    // Convert <br/> and <br> to newlines, strip other tags
    return raw
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<[^>]+>/g, '')
      .trim()
  })()

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
          {/* Title + Price + Days on market */}
          <div>
            {(isSold || isReserved) && (
              <span className="inline-block text-xs font-semibold text-gray-800 bg-gray-100 px-2 py-0.5 rounded mb-1 uppercase tracking-wide">
                {isSold ? t.sold : t.reserved}
              </span>
            )}
            <div className="flex items-center justify-between gap-2">
              <h2 className="font-bold text-gray-900 text-base leading-snug">
                {listing.title || `T${listing.rooms ?? '?'} ${t.inArea(listing.neighborhood || 'Lisboa')}`}
              </h2>
              <SourceLogo source={listing.source} size="md" />
            </div>
            <div className="flex items-baseline gap-2 mt-1">
              <span className="text-xl font-bold text-blue-700">
                €{fmt(listing.price_amount)}{isRent ? '/mo' : ''}
              </span>
              {days != null && (
                <span className="text-xs text-gray-400">
                  {days === 0 ? (t.justListed || 'Just listed') : `${days} ${t.daysOnMarket || 'days'}`}
                </span>
              )}
            </div>

            {/* Re-list price drop */}
            {previousPrice && priceDropPct != null && (
              <div className="flex items-center gap-1.5 mt-1 bg-green-50 border border-green-200 rounded px-2 py-1">
                <span className="text-xs text-gray-600">{t.priceDropFrom || 'Price drop from'}</span>
                <span className="text-xs text-gray-500 line-through">€{fmt(previousPrice)}</span>
                <PctBadge pct={priceDropPct} invert />
              </div>
            )}
          </div>

          {/* Like / dislike + comment */}
          {onSetReaction && (
            <div className="bg-white border border-gray-200 rounded-lg p-3">
              <ReactionButtons
                reaction={reaction}
                onSet={(r, c) => onSetReaction(listing, r, c)}
                onClear={() => onClearReaction(listing)}
                variant="full"
              />
            </div>
          )}

          {/* ============ VALUE SUMMARY CARD ============ */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 space-y-2">
            {/* Row 1: Quick badges */}
            <div className="flex flex-wrap items-center gap-1.5">
              {listing.condition && (
                <span className="text-[10px] font-medium bg-gray-200 text-gray-700 px-1.5 py-0.5 rounded">
                  {listing.condition}
                </span>
              )}
              {days != null && days <= 3 && (
                <span className="text-[10px] font-bold bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">NEW</span>
              )}
            </div>

            {/* Row 2: €/m² vs parish median */}
            {parishMedianPsm && (
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-500">{t.parishMedian || 'Parish median'}</span>
                <div className="flex items-center gap-1.5">
                  <span className="text-gray-600">€{fmt(parishMedianPsm)}/m²</span>
                  <PctBadge pct={parishDiffPct} />
                </div>
              </div>
            )}

            {/* Row 3: €/m² vs INE sold */}
            {ineSoldPsm && (
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-500">{t.ineSoldMedian || 'INE sold median'}</span>
                <div className="flex items-center gap-1.5">
                  <span className="text-gray-600">€{fmt(ineSoldPsm)}/m²</span>
                  <PctBadge pct={ineDiffPct} />
                  {ineDiffPct > 0 && (
                    <span className="text-[10px] text-gray-400">({t.askingPremium || 'premium'})</span>
                  )}
                </div>
              </div>
            )}

            {/* Row 4: Nearby construction */}
            {nearbyProjects && nearbyProjects.count > 0 && (
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-500">{t.nearbyProjects || 'Nearby construction'}</span>
                <div className="flex items-center gap-1.5">
                  {nearbyProjects.issued > 0 && (
                    <span className="bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded text-[10px] font-medium">
                      {nearbyProjects.issued} {t.issuedPermits || 'issued'}
                    </span>
                  )}
                  {nearbyProjects.pending > 0 && (
                    <span className="bg-yellow-100 text-yellow-700 px-1.5 py-0.5 rounded text-[10px] font-medium">
                      {nearbyProjects.pending} {t.pendingApps || 'pending'}
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Flip + Rent scorecard (replaces Deal Score / Property Score / Rarity) */}
          <FlipRentScorecard listing={listing} />

          {/* Price comparison across sites */}
          {crossListings.length > 0 && (
            <div className="border border-gray-200 rounded-lg p-3">
              <h3 className="font-semibold text-gray-700 text-sm mb-2">Price comparison</h3>
              <div className="flex items-center justify-between py-1.5 border-b border-gray-100">
                <div className="flex items-center gap-2">
                  <SourceLogo source={listing.source} size="md" />
                  <span className="text-xs text-gray-400">this listing</span>
                </div>
                <span className="font-bold text-blue-700 text-sm">€{fmt(listing.price_amount)}</span>
              </div>
              {crossListings.map(cl => (
                <a
                  key={cl.id}
                  href={cl.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-between py-1.5 hover:bg-gray-50 rounded -mx-1 px-1"
                >
                  <SourceLogo source={cl.source} size="md" />
                  <div className="text-right">
                    <span className="font-bold text-gray-900 text-sm">€{fmt(cl.price_amount)}</span>
                    <PriceDiff current={listing.price_amount} other={cl.price_amount} />
                  </div>
                </a>
              ))}
            </div>
          )}

          {/* ============ PRICE COMPARISON (MOVED UP) ============ */}
          <ListingComparison listing={listing} radiusM={radiusM} onRadiusChange={setRadiusM} />

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

          {/* Nearby construction projects detail */}
          {nearbyProjects && nearbyProjects.count > 0 && (
            <details className="text-sm">
              <summary className="font-semibold text-gray-700 cursor-pointer hover:text-blue-600">
                {t.nearbyProjects || 'Nearby construction'} ({nearbyProjects.count})
              </summary>
              <div className="mt-2 space-y-1.5 max-h-36 overflow-y-auto">
                {nearbyProjects.projects.map((p, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs py-1 border-b border-gray-100">
                    <span className="bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded text-[10px] font-medium w-10 text-center">
                      {p.distance_m}m
                    </span>
                    <span className={`px-1 py-0.5 rounded text-[10px] font-medium ${
                      p.layer === 'issued' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'
                    }`}>
                      {p.layer}
                    </span>
                    <span className="flex-1 truncate text-gray-600">{p.operation || p.subject || p.address || '—'}</span>
                  </div>
                ))}
              </div>
            </details>
          )}

          {/* Description (sanitized) */}
          {descriptionHtml && (
            <div className="text-sm">
              <h3 className="font-semibold text-gray-700 mb-1">{t.description}</h3>
              {translating ? (
                <p className="text-gray-400 text-xs italic">Translating…</p>
              ) : (
                <p className="text-gray-600 whitespace-pre-line text-xs leading-relaxed">
                  {descriptionHtml}
                </p>
              )}
            </div>
          )}

          {/* External link */}
          <a
            href={listing.url}
            target="_blank"
            rel="noopener noreferrer"
            className="block text-center bg-blue-600 text-white text-sm font-medium py-2 px-4 rounded-lg hover:bg-blue-700 transition-colors"
          >
            {t.viewOnSource} ({listing.source})
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
