import { useState, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { useLanguage } from '../LanguageContext'
import { FlipRentBadges } from './FlipRentScorecard'
import GrantBadge from './GrantBadge'
import SourceLogo from './SourceLogo'
import { fetchListingDetail, fetchListingComparison } from '../api'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseImages(raw) {
  if (!raw) return []
  try {
    const arr = typeof raw === 'string' ? JSON.parse(raw) : raw
    return Array.isArray(arr) ? arr : []
  } catch { return [] }
}

function fmt(n) {
  return n != null ? Math.round(n).toLocaleString('pt-PT') : '—'
}

// ---------------------------------------------------------------------------
// Image Carousel
// ---------------------------------------------------------------------------

function ImageCarousel({ images }) {
  const [idx, setIdx] = useState(0)

  if (images.length === 0) {
    return (
      <div className="w-full h-52 bg-gray-100 flex items-center justify-center text-gray-400 text-sm rounded-t-lg">
        No photos
      </div>
    )
  }

  const prev = () => setIdx(i => (i - 1 + images.length) % images.length)
  const next = () => setIdx(i => (i + 1) % images.length)

  return (
    <div className="relative w-full h-52 overflow-hidden rounded-t-lg bg-gray-100">
      {/* Slide strip */}
      <div
        className="flex h-full transition-transform duration-300 ease-in-out"
        style={{ transform: `translateX(-${idx * 100}%)`, width: `${images.length * 100}%` }}
      >
        {images.map((src, i) => (
          <div key={i} className="h-full shrink-0" style={{ width: `${100 / images.length}%` }}>
            <img
              src={src}
              alt={`Photo ${i + 1}`}
              className="w-full h-full object-cover block"
              loading="lazy"
            />
          </div>
        ))}
      </div>

      {/* Prev / Next */}
      {images.length > 1 && (
        <>
          <button
            onClick={prev}
            className="absolute left-1.5 top-1/2 -translate-y-1/2 bg-black/40 text-white w-8 h-8 rounded-full flex items-center justify-center text-lg hover:bg-black/60 cursor-pointer"
          >&#8249;</button>
          <button
            onClick={next}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 bg-black/40 text-white w-8 h-8 rounded-full flex items-center justify-center text-lg hover:bg-black/60 cursor-pointer"
          >&#8250;</button>
        </>
      )}

      {/* Counter */}
      <div className="absolute bottom-2 right-2 bg-black/50 text-white text-xs px-2 py-0.5 rounded-full tabular-nums">
        {idx + 1}/{images.length}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Comparable row
// ---------------------------------------------------------------------------

function ComparableRow({ comp, refPricePsm }) {
  const distLabel = comp.distance_m != null ? `${comp.distance_m}m` : '—'

  let diffBadge = null
  if (refPricePsm && comp.price_per_sqm) {
    const pct = ((comp.price_per_sqm - refPricePsm) / refPricePsm) * 100
    const isExpensive = pct > 0
    const sign = pct > 0 ? '+' : ''
    diffBadge = (
      <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
        isExpensive ? 'bg-red-100 text-red-700' : 'bg-green-100 text-green-700'
      }`}>
        {sign}{pct.toFixed(0)}%
      </span>
    )
  }

  return (
    <div className="flex items-center gap-2 text-xs py-1.5 border-b border-gray-100 last:border-0">
      <span className="bg-primary-tint text-primary px-1.5 py-0.5 rounded text-[10px] font-medium w-12 text-center shrink-0">
        {distLabel}
      </span>
      <span className="flex-1 min-w-0 truncate text-gray-600">
        {comp.address || comp.neighborhood || '—'}
        {comp.rooms != null && <span className="text-gray-400 ml-1">T{comp.rooms}</span>}
        {comp.size_sqm && <span className="text-gray-400 ml-1">{fmt(comp.size_sqm)}m²</span>}
      </span>
      <span className="font-medium text-gray-700 whitespace-nowrap tabular-nums">
        €{fmt(comp.price_amount)}
      </span>
      {diffBadge}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Expanded panel (rendered as a fixed portal over the map)
// ---------------------------------------------------------------------------

function ExpandedPanel({ listing, onClose }) {
  const { t } = useLanguage()
  const isRent = listing.listing_type === 'rent'
  const isSold = listing.status === 'sold'
  const isReserved = listing.status === 'reserved'

  const [images, setImages] = useState(() => parseImages(listing.images))
  const [comparables, setComparables] = useState([])
  const [loadingComps, setLoadingComps] = useState(false)
  const [detailLoaded, setDetailLoaded] = useState(false)

  // Fetch full detail for images if not already available
  useEffect(() => {
    if (images.length > 0) {
      setDetailLoaded(true)
      return
    }
    let cancelled = false
    fetchListingDetail(listing.id, listing.listing_type || 'sale')
      .then(data => {
        if (!cancelled) {
          const imgs = parseImages(data.images)
          setImages(imgs)
          setDetailLoaded(true)
        }
      })
      .catch(() => { if (!cancelled) setDetailLoaded(true) })
    return () => { cancelled = true }
  }, [listing.id, listing.listing_type])

  // Fetch comparables
  useEffect(() => {
    if (!listing.id || !listing.lat || !listing.lon) return
    let cancelled = false
    setLoadingComps(true)
    fetchListingComparison(
      listing.id,
      listing.listing_type || 'sale',
      1000,            // 1 km radius
      null,            // any property type
      null             // any bedrooms
    )
      .then(data => {
        if (!cancelled) {
          const list = data?.comparables?.listings || []
          setComparables(list.slice(0, 8))
        }
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoadingComps(false) })
    return () => { cancelled = true }
  }, [listing.id, listing.listing_type])

  // Close on Escape key
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  return createPortal(
    /* Backdrop */
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-end pointer-events-none"
      style={{ padding: '12px' }}
    >
      {/* Panel */}
      <div
        className="pointer-events-auto bg-white rounded-xl shadow-2xl flex flex-col overflow-hidden relative"
        style={{ width: '420px', maxWidth: 'calc(100vw - 24px)', maxHeight: 'calc(100vh - 24px)' }}
      >
        {/* Image carousel */}
        <div className="shrink-0">
          <ImageCarousel images={images} />
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">

          {/* Key info row */}
          <div className="flex items-start justify-between gap-2">
            <div>
              {(isSold || isReserved) && (
                <span className="text-[10px] font-semibold text-amber-700 uppercase tracking-wide">
                  {isSold ? t.sold : t.reserved}
                </span>
              )}
              <div className="text-xs text-gray-500 mt-0.5">
                {[
                  listing.property_type
                    ? listing.property_type.charAt(0).toUpperCase() + listing.property_type.slice(1)
                    : null,
                  listing.rooms != null ? `T${listing.rooms}` : null,
                  listing.size_sqm ? `${fmt(listing.size_sqm)} m²` : null,
                  listing.neighborhood || listing.parish || null,
                ].filter(Boolean).join(' · ')}
              </div>
            </div>
            <div className="text-right shrink-0">
              <div className="font-bold text-gray-900 text-base leading-tight">
                €{fmt(listing.price_amount)}{isRent ? '/mo' : ''}
              </div>
              {listing.price_per_sqm && (
                <div className="text-[10px] text-gray-500 tabular-nums">
                  €{fmt(listing.price_per_sqm)}/m²
                </div>
              )}
            </div>
          </div>

          {/* Scores */}
          <div className="py-1.5 border-y border-gray-100">
            <FlipRentBadges flip={listing.flip_score} rent={listing.rent_score} />
          </div>

          {/* Comparable listings */}
          <div>
            <h3 className="font-semibold text-gray-700 text-sm mb-2">
              Comparable listings nearby
            </h3>
            {loadingComps ? (
              <div className="space-y-2">
                {[1, 2, 3].map(i => (
                  <div key={i} className="h-4 bg-gray-100 rounded animate-pulse" />
                ))}
              </div>
            ) : comparables.length > 0 ? (
              <div>
                {comparables.map(c => (
                  <ComparableRow
                    key={`${c.source}-${c.id}`}
                    comp={c}
                    refPricePsm={listing.price_per_sqm}
                  />
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-400">No comparable listings found within 1 km.</p>
            )}
          </div>

          {/* Footer: source + link */}
          <div className="flex items-center justify-between pt-1 border-t border-gray-100">
            <SourceLogo source={listing.source} />
            <a
              href={listing.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary text-xs font-medium hover:underline"
            >
              {t.viewListing}
            </a>
          </div>
        </div>

        {/* Close button (top-right corner, anchored to panel) */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 bg-black/40 text-white w-7 h-7 rounded-full flex items-center justify-center text-sm hover:bg-black/60 cursor-pointer"
          title="Close"
        >
          &#x2715;
        </button>
      </div>
    </div>,
    document.body
  )
}

// ---------------------------------------------------------------------------
// Main export: ListingPopupCard
// ---------------------------------------------------------------------------

export default function ListingPopupCard({ listing: l }) {
  const { t } = useLanguage()
  const [expanded, setExpanded] = useState(false)

  const isRent = l.listing_type === 'rent'
  const isSold = l.status === 'sold'
  const isReserved = l.status === 'reserved'

  // First image as thumbnail
  const thumbnail = (() => {
    const imgs = parseImages(l.images)
    return imgs.length > 0 ? imgs[0] : null
  })()
  const [imgError, setImgError] = useState(false)

  const typeLabel = l.property_type
    ? l.property_type.charAt(0).toUpperCase() + l.property_type.slice(1)
    : null

  const handleExpand = useCallback((e) => {
    e.stopPropagation()
    setExpanded(true)
  }, [])

  const handleClose = useCallback(() => setExpanded(false), [])

  return (
    <>
      {/* Collapsed card */}
      <div className="text-sm min-w-[230px] max-w-[260px] relative">
        {/* Expand button */}
        <button
          onClick={handleExpand}
          className="absolute top-0 right-0 z-10 bg-white/80 hover:bg-white border border-gray-200 rounded p-0.5 cursor-pointer"
          title="Expand"
        >
          {/* Two outward-pointing arrows (expand icon) */}
          <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-gray-500" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M3 4a1 1 0 011-1h4a1 1 0 010 2H6.414l2.293 2.293a1 1 0 11-1.414 1.414L5 6.414V8a1 1 0 01-2 0V4zm9 1a1 1 0 110-2h4a1 1 0 011 1v4a1 1 0 01-2 0V6.414l-2.293 2.293a1 1 0 11-1.414-1.414L13.586 5H12zm-9 7a1 1 0 012 0v1.586l2.293-2.293a1 1 0 111.414 1.414L6.414 15H8a1 1 0 010 2H4a1 1 0 01-1-1v-4zm13-1a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 010-2h1.586l-2.293-2.293a1 1 0 111.414-1.414L15 13.586V12a1 1 0 011-1z" clipRule="evenodd" />
          </svg>
        </button>

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

      {/* Expanded panel portal */}
      {expanded && <ExpandedPanel listing={l} onClose={handleClose} />}
    </>
  )
}
