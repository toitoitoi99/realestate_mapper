import { useState, useEffect, useMemo } from 'react'
import { fetchFlipRentPreview } from '../api'
import { useScoreBands, ratingFor } from '../ScoreBandsContext'

// Signal display metadata.
const SIGNAL_LABELS = {
  market_discount: 'Market discount',
  amenity: 'Amenity / livability',
  transit: 'Transit',
  light: 'Natural light',
  outdoor_space: 'Outdoor space',
  layout_openness: 'Layout flexibility',
  dev_momentum: 'Nearby new development',
  sea_view: 'Sea view',
  demand_durability: 'Demand durability',
  expected_resale: 'Expected resale margin',
  yield_gross: 'Gross rental yield',
  noise: 'Noise exposure',
  social_housing_adj: 'Social housing proximity',
  public_project_adj: 'Public projects nearby',
  heritage_restriction: 'Heritage restrictions',
  dark_unit: 'Dark unit risk',
  structural_red_flags: 'Structural red flags',
  construction_risk: 'Construction / execution risk',
}

const POSITIVE_KEYS = [
  'market_discount', 'amenity', 'transit', 'light', 'outdoor_space',
  'layout_openness', 'dev_momentum', 'sea_view', 'demand_durability',
  'expected_resale', 'yield_gross',
]
const BLOCKER_KEYS = [
  'noise', 'social_housing_adj', 'public_project_adj',
  'heritage_restriction', 'dark_unit', 'structural_red_flags',
  'construction_risk',
]

function ratingClasses(rating) {
  if (rating === 'A') return { pill: 'bg-emerald-600 text-white', chip: 'text-emerald-700' }
  if (rating === 'B') return { pill: 'bg-green-600 text-white',   chip: 'text-green-700' }
  if (rating === 'C') return { pill: 'bg-amber-500 text-white',   chip: 'text-amber-700' }
  return                        { pill: 'bg-red-500 text-white',     chip: 'text-red-700' }
}

function ratingBarWidth(score) {
  return `${Math.max(0, Math.min(100, score || 0))}%`
}

/**
 * Compact inline badge pair for the listing card.
 * `show` filters which badge(s) render:
 *   'both' (default) — show flip + rent
 *   'flip'           — show flip only
 *   'rent'           — show rent only
 */
export function FlipRentBadges({ flip, rent, show = 'both', className = 'mt-1.5' }) {
  if (flip == null && rent == null) return null
  const { bands } = useScoreBands()
  const f = Math.round(flip || 0)
  const r = Math.round(rent || 0)
  const fRating = ratingFor(flip, bands)
  const rRating = ratingFor(rent, bands)
  const showFlip = (show === 'both' || show === 'flip') && flip != null
  const showRent = (show === 'both' || show === 'rent') && rent != null
  if (!showFlip && !showRent) return null
  return (
    <div className={`flex gap-1.5 ${className}`}>
      {showFlip && (
        <span
          className={`inline-flex items-center gap-1 text-xs font-semibold px-1.5 py-0.5 rounded ${ratingClasses(fRating).pill}`}
          title="Flip potential"
        >
          <span className="uppercase tracking-wider text-[10px] opacity-80">Flip</span>
          <span>{fRating}</span>
          <span className="opacity-80">{f}</span>
        </span>
      )}
      {showRent && (
        <span
          className={`inline-flex items-center gap-1 text-xs font-semibold px-1.5 py-0.5 rounded ${ratingClasses(rRating).pill}`}
          title="Rental potential"
        >
          <span className="uppercase tracking-wider text-[10px] opacity-80">Rent</span>
          <span>{rRating}</span>
          <span className="opacity-80">{r}</span>
        </span>
      )}
    </div>
  )
}

function ScoreBar({ label, score, accent }) {
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-600 mb-0.5">
        <span>{label}</span>
        <span className="font-medium text-gray-900">{Math.round(score || 0)}</span>
      </div>
      <div className="h-2 bg-gray-100 rounded overflow-hidden">
        <div className={`h-full ${accent}`} style={{ width: ratingBarWidth(score) }} />
      </div>
    </div>
  )
}

/** Full detail card — score breakdown + signal toggles + reno slider. */
export default function FlipRentScorecard({ listing }) {
  const [disabled, setDisabled] = useState(new Set())
  const [renoPerSqm, setRenoPerSqm] = useState(null)  // null = use stored default
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(false)
  const { bands } = useScoreBands()

  const listingType = listing.listing_type || 'sale'

  // Baseline comes from the listing row (already on the API).
  const baselineFlip = listing.flip_score
  const baselineRent = listing.rent_score
  const stored = listing.reno_cost_estimate
  const size = listing.size_sqm
  // If size known, infer the baseline €/m² the scorer used.
  const storedPerSqm = stored && size ? Math.round(stored / size) : null

  const effectiveRenoPerSqm = renoPerSqm != null ? renoPerSqm : storedPerSqm

  // Fetch preview whenever toggles or slider change (debounced).
  useEffect(() => {
    if (disabled.size === 0 && renoPerSqm == null) {
      setPreview(null); setError(null); return
    }
    let cancelled = false
    const timer = setTimeout(() => {
      setLoading(true); setError(null)
      fetchFlipRentPreview(listing.id, {
        listingType,
        disable: Array.from(disabled),
        renoCostPerSqm: renoPerSqm,
      })
        .then(data => { if (!cancelled) setPreview(data) })
        .catch(err => { if (!cancelled) setError(err.message) })
        .finally(() => { if (!cancelled) setLoading(false) })
    }, 180)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [listing.id, listingType, disabled, renoPerSqm])

  const currentFlip = preview?.flip?.score ?? baselineFlip
  const currentRent = preview?.rent?.score ?? baselineRent
  const currentFlipRating = preview?.flip?.rating ?? ratingFor(baselineFlip, bands)
  const currentRentRating = preview?.rent?.rating ?? ratingFor(baselineRent, bands)

  const dFlip = preview ? (currentFlip - baselineFlip) : 0
  const dRent = preview ? (currentRent - baselineRent) : 0

  // Pull signal values from the preview response (up-to-date) or fall back
  // to the stored factor JSON blobs on the listing row.
  const bundle = preview?.bundle || (() => {
    try {
      return listing.flip_factors ? JSON.parse(listing.flip_factors).bundle : null
    } catch { return null }
  })()

  const positives = bundle?.positives || {}
  const blockers = bundle?.blockers || {}

  // Renovation classifier output (PR #85). Populated by scorers/renovation_classifier.
  const renovationEvidence = (() => {
    try { return listing.renovation_evidence ? JSON.parse(listing.renovation_evidence) : [] }
    catch { return [] }
  })()
  const renovationNeeds = (() => {
    try { return listing.renovation_needs ? JSON.parse(listing.renovation_needs) : [] }
    catch { return [] }
  })()

  if (baselineFlip == null && baselineRent == null) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-3 text-sm text-gray-500">
        No flip/rent score available for this listing yet.
        <div className="text-xs mt-1 text-gray-400">
          Run <code>python3 signal_batch.py</code> to compute.
        </div>
      </div>
    )
  }

  function toggle(key) {
    setDisabled(prev => {
      const n = new Set(prev)
      if (n.has(key)) n.delete(key); else n.add(key)
      return n
    })
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-gray-400">Scorecard</div>
          <div className="text-xs text-gray-500 mt-0.5">
            Profile: <span className="font-medium text-gray-700">{listing.region_profile || preview?.profile || '—'}</span>
            {bundle?.comparables_count != null && (
              <span className="ml-2">· {bundle.comparables_count} comparables</span>
            )}
          </div>
        </div>
        <button
          onClick={() => setExpanded(e => !e)}
          className="text-xs text-primary hover:underline"
        >
          {expanded ? 'Hide signals' : 'Review signals'}
        </button>
      </div>

      {/* Top-level scores */}
      <div className="grid grid-cols-2 gap-3 mt-3">
        <div className="p-3 rounded border border-gray-100 bg-gray-50">
          <div className="flex items-baseline justify-between">
            <div className="text-xs uppercase tracking-wider text-gray-500">Flip</div>
            <span className={`inline-flex items-center px-2 py-0.5 rounded text-sm font-bold ${ratingClasses(currentFlipRating).pill}`}>
              {currentFlipRating}
            </span>
          </div>
          <div className="flex items-baseline gap-2 mt-1">
            <div className="text-2xl font-bold text-gray-900">{Math.round(currentFlip)}</div>
            {preview && (
              <div className={`text-xs font-medium ${dFlip > 0 ? 'text-emerald-600' : dFlip < 0 ? 'text-red-600' : 'text-gray-400'}`}>
                {dFlip > 0 ? '+' : ''}{dFlip.toFixed(1)}
              </div>
            )}
          </div>
        </div>
        <div className="p-3 rounded border border-gray-100 bg-gray-50">
          <div className="flex items-baseline justify-between">
            <div className="text-xs uppercase tracking-wider text-gray-500">Rent</div>
            <span className={`inline-flex items-center px-2 py-0.5 rounded text-sm font-bold ${ratingClasses(currentRentRating).pill}`}>
              {currentRentRating}
            </span>
          </div>
          <div className="flex items-baseline gap-2 mt-1">
            <div className="text-2xl font-bold text-gray-900">{Math.round(currentRent)}</div>
            {preview && (
              <div className={`text-xs font-medium ${dRent > 0 ? 'text-emerald-600' : dRent < 0 ? 'text-red-600' : 'text-gray-400'}`}>
                {dRent > 0 ? '+' : ''}{dRent.toFixed(1)}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Reno cost slider */}
      <div className="mt-4">
        <div className="flex justify-between items-center text-xs mb-1">
          <span className="text-gray-600">Renovation €/m²</span>
          <span className="font-medium text-gray-900">
            {effectiveRenoPerSqm ? `€${effectiveRenoPerSqm.toLocaleString('pt-PT')}/m²` : '—'}
            {listing.size_sqm && effectiveRenoPerSqm && (
              <span className="text-gray-400 ml-1">
                · total €{(effectiveRenoPerSqm * listing.size_sqm).toLocaleString('pt-PT', { maximumFractionDigits: 0 })}
              </span>
            )}
          </span>
        </div>
        <input
          type="range"
          min="400" max="3500" step="50"
          value={effectiveRenoPerSqm || 1500}
          onChange={(e) => setRenoPerSqm(parseFloat(e.target.value))}
          className="w-full accent-primary"
          aria-label="Renovation cost per m²"
        />
        <div className="flex justify-between text-[10px] text-gray-400">
          <span>€400 cosmetic</span>
          <span>€1500 mid</span>
          <span>€3500 gut</span>
        </div>
        {renoPerSqm != null && (
          <button
            className="text-[11px] text-primary hover:underline mt-1"
            onClick={() => setRenoPerSqm(null)}
          >
            reset to default
          </button>
        )}
      </div>

      {/* Renovation classifier (PR #85): class + cost + evidence bullets */}
      {listing.renovation_class && (
        <div className="mt-3 text-xs rounded border border-indigo-100 bg-indigo-50/50 p-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-indigo-600 font-medium uppercase tracking-wider text-[10px]">
              Photo analysis
            </span>
            <div className="flex items-center gap-1.5">
              <span className={`text-[10px] px-1.5 py-0.5 rounded text-white font-semibold uppercase ${
                listing.renovation_class === 'turnkey' ? 'bg-emerald-600' :
                listing.renovation_class === 'cosmetic' ? 'bg-amber-500' :
                'bg-red-600'
              }`}>
                {listing.renovation_class.replace('_', ' ')}
              </span>
              {listing.renovation_confidence != null && (
                <span className="text-[10px] text-gray-500">
                  {Math.round(listing.renovation_confidence * 100)}% conf
                </span>
              )}
            </div>
          </div>
          {listing.renovation_cost_estimate_eur_per_sqm != null && (
            <div className="flex justify-between text-gray-600 mt-1">
              <span>Model reno estimate</span>
              <span className="font-medium">
                €{listing.renovation_cost_estimate_eur_per_sqm.toLocaleString('pt-PT')}/m²
                {listing.size_sqm && (
                  <span className="text-gray-400 ml-1">
                    · €{(listing.renovation_cost_estimate_eur_per_sqm * listing.size_sqm).toLocaleString('pt-PT', { maximumFractionDigits: 0 })}
                  </span>
                )}
              </span>
            </div>
          )}
          {renovationEvidence.length > 0 && (
            <ul className="text-gray-700 mt-1 list-disc list-inside space-y-0.5">
              {renovationEvidence.slice(0, 4).map((e, i) => (
                <li key={i} className="leading-snug">{e}</li>
              ))}
            </ul>
          )}
          {renovationNeeds.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {renovationNeeds.map((n, i) => (
                <span key={i} className={`inline-block text-[10px] px-1.5 py-0.5 rounded font-mono ${
                  n.severity === 'full' ? 'bg-red-100 text-red-800' :
                  n.severity === 'cosmetic' ? 'bg-amber-100 text-amber-800' :
                  'bg-gray-100 text-gray-700'
                }`}>
                  {n.item}:{n.severity}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Signal review panel */}
      {expanded && (
        <div className="mt-4 border-t border-gray-100 pt-3">
          <div className="text-xs text-gray-500 mb-2">
            Uncheck to see what each signal contributes. Toggles affect the scores above only; underlying data is unchanged.
            {loading && <span className="ml-2 text-primary">…</span>}
            {error && <span className="ml-2 text-red-500">{error}</span>}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1">
            <SignalGroup title="Positives" keys={POSITIVE_KEYS} values={positives} disabled={disabled} onToggle={toggle} positive />
            <SignalGroup title="Blockers" keys={BLOCKER_KEYS} values={blockers} disabled={disabled} onToggle={toggle} />
          </div>
        </div>
      )}
    </div>
  )
}

function SignalGroup({ title, keys, values, disabled, onToggle, positive = false }) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-1">
        {title}
      </div>
      <ul className="space-y-0.5">
        {keys.map(k => {
          const v = values[k]
          const isDisabled = disabled.has(k)
          const hasValue = v != null
          return (
            <li key={k}
              className={`flex items-center justify-between text-xs py-0.5 pr-1 ${hasValue ? '' : 'opacity-40'}`}>
              <label className="flex items-center gap-1.5 cursor-pointer flex-1 min-w-0">
                <input
                  type="checkbox"
                  checked={!isDisabled}
                  onChange={() => onToggle(k)}
                  disabled={!hasValue}
                  className="shrink-0"
                />
                <span className={`truncate ${isDisabled ? 'line-through text-gray-400' : 'text-gray-700'}`}>
                  {SIGNAL_LABELS[k] || k}
                </span>
              </label>
              <span className={`shrink-0 font-mono tabular-nums text-[11px] ${
                hasValue ? (positive ? 'text-emerald-700' : 'text-red-700') : 'text-gray-300'
              }`}>
                {hasValue ? Math.round(v) : '—'}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
