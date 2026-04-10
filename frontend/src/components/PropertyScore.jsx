import { useState, useEffect } from 'react'
import { fetchPropertyScore } from '../api'

function ratingColor(rating) {
  if (rating === 'A') return { bg: 'bg-emerald-100', text: 'text-emerald-700', border: 'border-emerald-300', hex: '#059669' }
  if (rating === 'B') return { bg: 'bg-green-100', text: 'text-green-700', border: 'border-green-300', hex: '#16a34a' }
  if (rating === 'C') return { bg: 'bg-amber-100', text: 'text-amber-700', border: 'border-amber-300', hex: '#d97706' }
  return { bg: 'bg-red-100', text: 'text-red-700', border: 'border-red-300', hex: '#dc2626' }
}

const FEATURE_ICONS = {
  outdoor_space: '☀️',
  elevator: '🛗',
  parking: '🅿️',
  storage: '📦',
  pool: '🏊',
  garden: '🌳',
  view: '👁️',
  energy_a_b_c: '⚡',
  renovated: '✨',
  air_cond: '❄️',
  suite: '🛏️',
  multi_bath: '🚿',
  high_floor_elevator: '🔝',
}

export default function PropertyScore({ listing, listingType = 'sale' }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!listing?.id) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchPropertyScore(listing.id, listingType)
      .then(result => {
        if (!cancelled) {
          if (result.error) setError(result.error)
          else setData(result)
        }
      })
      .catch(() => { if (!cancelled) setError('Failed to load') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [listing?.id, listingType])

  if (loading) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 space-y-2">
        <div className="h-4 w-32 bg-gray-200 rounded animate-pulse" />
        <div className="grid grid-cols-2 gap-2">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-7 bg-gray-200 rounded animate-pulse" />
          ))}
        </div>
      </div>
    )
  }

  if (error || !data || data.property_score == null) return null

  const { property_score, property_rating, property_type_class, features, detected_count, total_features } = data
  const colors = ratingColor(property_rating)
  const detected = features.filter(f => f.detected)
  const missing = features.filter(f => !f.detected)

  // Score bar segments
  const maxWeight = Math.max(...features.map(f => f.weight))

  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Property Score</span>
          <span className="text-[10px] text-gray-400 bg-gray-200 px-1.5 py-0.5 rounded">
            {property_type_class}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="text-white text-xs font-bold px-2 py-0.5 rounded"
            style={{ backgroundColor: colors.hex }}
          >
            {property_rating}
          </span>
          <span className="text-sm font-bold text-gray-800">{Math.round(property_score)}/100</span>
        </div>
      </div>

      {/* Overall progress bar */}
      <div className="relative h-2 bg-gray-200 rounded-full overflow-hidden">
        <div
          className="absolute h-full rounded-full transition-all duration-500"
          style={{ width: `${property_score}%`, backgroundColor: colors.hex }}
        />
      </div>

      {/* Detected features */}
      {detected.length > 0 && (
        <div>
          <div className="text-[10px] text-gray-500 uppercase tracking-wide mb-1.5">
            Detected ({detected_count}/{total_features})
          </div>
          <div className="flex flex-wrap gap-1.5">
            {detected.map(f => (
              <span
                key={f.key}
                className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium ${colors.bg} ${colors.text} ${colors.border} border`}
              >
                <span className="text-[11px]">{FEATURE_ICONS[f.key] || '+'}</span>
                {f.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Feature breakdown bars */}
      <div className="space-y-1">
        {features.map(f => (
          <div key={f.key} className="flex items-center gap-2 text-xs">
            <span className="w-[11px] text-center text-[10px]">{FEATURE_ICONS[f.key] || '·'}</span>
            <span className={`w-28 truncate ${f.detected ? 'text-gray-700 font-medium' : 'text-gray-400'}`}>
              {f.label}
            </span>
            <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${(f.weight / maxWeight) * 100}%`,
                  backgroundColor: f.detected ? colors.hex : '#e5e7eb',
                }}
              />
            </div>
            <span className={`w-4 text-right text-[10px] ${f.detected ? 'text-gray-600' : 'text-gray-300'}`}>
              {f.weight}
            </span>
          </div>
        ))}
      </div>

      {/* Missing features hint */}
      {missing.length > 0 && missing.length <= 4 && (
        <div className="text-[10px] text-gray-400">
          Missing: {missing.map(f => f.label).join(', ')}
        </div>
      )}
    </div>
  )
}
