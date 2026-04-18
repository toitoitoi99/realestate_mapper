/**
 * Compact row summarizing a listing for admin/user review surfaces
 * (ReactionsTab, Disagreements panel, MyListingsPage).
 */
function fmtPrice(n) {
  if (n == null) return '—'
  return '€' + Math.round(n).toLocaleString('pt-PT')
}
function fmtPsm(n) {
  if (n == null) return null
  return `€${Math.round(n).toLocaleString('pt-PT')}/m²`
}
function fmtDate(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

export default function ListingRefCard({
  listing,
  listingKind,
  title,              // fallback title if listing is null
  comment,
  updatedAt,
  topBadges = [],     // array of { text, className }
  commentPrefix = '💬',
  actions,            // node rendered in the right-side column
  onViewOnMap,        // optional: convenience wrapper to add a "Map" button
}) {
  const l = listing
  const isRent = listingKind === 'rent'
  return (
    <div className="bg-white rounded border border-gray-200 p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`px-1.5 py-0.5 text-[10px] rounded ${isRent ? 'bg-violet-100 text-violet-700' : 'bg-amber-100 text-amber-700'}`}>
              {isRent ? 'RENT' : 'SALE'}
            </span>
            {l?.source && (
              <span className="px-1.5 py-0.5 text-[10px] rounded bg-gray-100 text-gray-600 uppercase">{l.source}</span>
            )}
            {l?.status && l.status !== 'active' && (
              <span className="px-1.5 py-0.5 text-[10px] rounded bg-red-100 text-red-700">{l.status}</span>
            )}
            {topBadges.map((b, i) => (
              <span key={i} className={`px-1.5 py-0.5 text-[10px] rounded ${b.className}`}>{b.text}</span>
            ))}
            {l?.neighborhood && (
              <span className="text-xs text-gray-500 truncate">{l.neighborhood}</span>
            )}
          </div>
          <div className="mt-1 font-medium text-gray-800 truncate" title={l?.title || title || ''}>
            {l?.title || title || <span className="text-gray-400 italic">listing (missing)</span>}
          </div>
          <div className="mt-1 flex items-center gap-3 text-xs text-gray-600 flex-wrap">
            <span className="font-semibold text-gray-900">{fmtPrice(l?.price_amount)}</span>
            {fmtPsm(l?.price_per_sqm) && <span>{fmtPsm(l?.price_per_sqm)}</span>}
            {l?.size_sqm && <span>{l.size_sqm} m²</span>}
            {l?.rooms != null && <span>T{l.rooms}</span>}
            {l?.flip_score != null && <span className="text-orange-700">Flip {Math.round(l.flip_score)}</span>}
            {l?.rent_score != null && <span className="text-violet-700">Rent {Math.round(l.rent_score)}</span>}
          </div>
          {comment && (
            <div className="mt-2 text-xs text-gray-700 bg-gray-50 rounded p-2 border border-gray-100 whitespace-pre-wrap">
              {commentPrefix} {comment}
            </div>
          )}
          {updatedAt && (
            <div className="mt-2 text-[11px] text-gray-400">{fmtDate(updatedAt)}</div>
          )}
        </div>
        <div className="flex flex-col gap-1 shrink-0">
          {l?.url && (
            <a
              href={l.url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-2 py-1 text-xs border border-gray-300 rounded text-gray-700 hover:bg-gray-50 text-center"
            >Open</a>
          )}
          {l && onViewOnMap && (
            <button
              onClick={() => onViewOnMap(l)}
              className="px-2 py-1 text-xs border border-gray-300 rounded text-gray-700 hover:bg-gray-50"
            >Map</button>
          )}
          {actions}
        </div>
      </div>
    </div>
  )
}
