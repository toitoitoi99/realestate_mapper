import { useState, useEffect } from 'react'
import { fetchDealScore } from '../api'

const DIMENSIONS = ['value', 'location', 'yield', 'scarcity', 'growth']
const LABELS = { value: 'Value', location: 'Location', yield: 'Yield', scarcity: 'Scarcity', growth: 'Growth' }
const DIM_COLORS = {
  value: '#22c55e',
  location: '#3b82f6',
  yield: '#f59e0b',
  scarcity: '#8b5cf6',
  growth: '#06b6d4',
}

function ratingColor(rating) {
  if (rating === 'A') return { bg: 'bg-emerald-100', text: 'text-emerald-700', border: 'border-emerald-300' }
  if (rating === 'B') return { bg: 'bg-green-100', text: 'text-green-700', border: 'border-green-300' }
  if (rating === 'C') return { bg: 'bg-amber-100', text: 'text-amber-700', border: 'border-amber-300' }
  return { bg: 'bg-red-100', text: 'text-red-700', border: 'border-red-300' }
}

function ratingBgHex(rating) {
  if (rating === 'A') return '#059669'
  if (rating === 'B') return '#16a34a'
  if (rating === 'C') return '#d97706'
  return '#dc2626'
}

/** SVG radar chart — 5-axis pentagon */
function RadarChart({ dimensions }) {
  const cx = 90, cy = 90, r = 70
  const axes = DIMENSIONS.filter(k => dimensions[k]?.weight > 0)
  const n = axes.length

  function polyPoints(scores) {
    return axes.map((_, i) => {
      const angle = (Math.PI * 2 * i) / n - Math.PI / 2
      const val = (scores[i] || 0) / 100
      const x = cx + r * val * Math.cos(angle)
      const y = cy + r * val * Math.sin(angle)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    }).join(' ')
  }

  const rings = [25, 50, 75, 100]

  const axisEnds = axes.map((_, i) => {
    const angle = (Math.PI * 2 * i) / n - Math.PI / 2
    return {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
      lx: cx + (r + 14) * Math.cos(angle),
      ly: cy + (r + 14) * Math.sin(angle),
    }
  })

  const scores = axes.map(k => dimensions[k]?.score || 0)

  return (
    <svg viewBox="0 0 180 180" className="w-full max-w-[200px] mx-auto">
      {rings.map(pct => (
        <polygon
          key={pct}
          points={polyPoints(axes.map(() => pct))}
          fill="none"
          stroke="#e5e7eb"
          strokeWidth="0.5"
        />
      ))}
      {axisEnds.map((pt, i) => (
        <line key={i} x1={cx} y1={cy} x2={pt.x} y2={pt.y} stroke="#d1d5db" strokeWidth="0.5" />
      ))}
      <polygon
        points={polyPoints(scores)}
        fill="rgba(59, 130, 246, 0.15)"
        stroke="#3b82f6"
        strokeWidth="1.5"
      />
      {axes.map((k, i) => {
        const angle = (Math.PI * 2 * i) / n - Math.PI / 2
        const val = scores[i] / 100
        const x = cx + r * val * Math.cos(angle)
        const y = cy + r * val * Math.sin(angle)
        return <circle key={k} cx={x} cy={y} r="2.5" fill={DIM_COLORS[k]} stroke="white" strokeWidth="1" />
      })}
      {axes.map((k, i) => (
        <text
          key={k}
          x={axisEnds[i].lx}
          y={axisEnds[i].ly}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="8"
          fill="#6b7280"
          fontWeight="500"
        >
          {LABELS[k]}
        </text>
      ))}
    </svg>
  )
}

export default function DealScore({ listing, listingType = 'sale' }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!listing?.id) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchDealScore(listing.id, listingType)
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
        <div className="h-4 w-24 bg-gray-200 rounded animate-pulse" />
        <div className="flex justify-center">
          <div className="w-[200px] h-[200px] bg-gray-100 rounded-full animate-pulse" />
        </div>
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-6 bg-gray-200 rounded animate-pulse" />
        ))}
      </div>
    )
  }

  if (error || !data) return null

  const { deal_score, deal_rating, dimensions } = data
  const overallColor = ratingBgHex(deal_rating)
  const activeDims = DIMENSIONS.filter(k => dimensions[k]?.weight > 0)

  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Deal Score</span>
        <div className="flex items-center gap-1.5">
          <span
            className="text-white text-xs font-bold px-2 py-0.5 rounded"
            style={{ backgroundColor: overallColor }}
          >
            {deal_rating}
          </span>
          <span className="text-sm font-bold text-gray-800">{Math.round(deal_score)}/100</span>
        </div>
      </div>

      <RadarChart dimensions={dimensions} />

      <div className="space-y-1.5">
        {activeDims.map(k => {
          const dim = dimensions[k]
          const colors = ratingColor(dim.rating)
          return (
            <div key={k} className="space-y-0.5">
              <div className="flex items-center gap-1.5">
                <span className={`text-[10px] font-bold px-1 py-0 rounded ${colors.bg} ${colors.text} ${colors.border} border`}>
                  {dim.rating}
                </span>
                <span className="text-xs font-medium text-gray-700 w-16 shrink-0">{LABELS[k]}</span>
                <div className="flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.max(2, dim.score)}%`,
                      backgroundColor: DIM_COLORS[k],
                    }}
                  />
                </div>
                <span className="text-[10px] text-gray-500 w-6 text-right tabular-nums">{Math.round(dim.score)}</span>
              </div>
              <p className="text-[10px] text-gray-400 pl-[72px] leading-tight">{dim.detail}</p>
            </div>
          )
        })}
      </div>

      {data.comparables_count > 0 && (
        <p className="text-[9px] text-gray-400 text-right">
          Based on {data.comparables_count} nearby comparables
        </p>
      )}
    </div>
  )
}
