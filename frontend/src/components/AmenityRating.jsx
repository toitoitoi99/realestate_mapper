import { useState, useEffect } from 'react'
import { useLanguage } from '../LanguageContext'
import { fetchAmenityRating } from '../api'

const CATEGORIES = {
  green_spaces:    { color: 'bg-green-500',  barBg: 'bg-green-100' },
  convenience:     { color: 'bg-orange-500', barBg: 'bg-orange-100' },
  education:       { color: 'bg-blue-500',   barBg: 'bg-blue-100' },
  transportation:  { color: 'bg-purple-500', barBg: 'bg-purple-100' },
  healthcare:      { color: 'bg-red-500',    barBg: 'bg-red-100' },
}

const CLASS_STYLES = {
  A: 'bg-emerald-600 text-white',
  B: 'bg-blue-600 text-white',
  C: 'bg-amber-600 text-white',
}

export default function AmenityRating({ lat, lon }) {
  const { t } = useLanguage()
  const [rating, setRating] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (lat == null || lon == null) return
    setLoading(true)
    setError(null)
    fetchAmenityRating(lat, lon)
      .then(data => { setRating(data); setError(null) })
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [lat, lon])

  if (lat == null || lon == null) return null

  if (loading) {
    return (
      <div className="text-sm">
        <h3 className="font-semibold text-gray-700 mb-2">{t.amenityRating}</h3>
        <div className="space-y-2">
          {[1, 2, 3, 4, 5].map(i => (
            <div key={i} className="h-4 bg-gray-200 rounded animate-pulse" />
          ))}
        </div>
      </div>
    )
  }

  if (error || !rating) {
    return (
      <div className="text-sm text-gray-400">{t.amenityUnavailable}</div>
    )
  }

  return (
    <div className="text-sm">
      <h3 className="font-semibold text-gray-700 mb-2">{t.amenityRating}</h3>

      {/* Overall badge */}
      <div className="flex items-center gap-2 mb-3">
        <span className={`text-xs font-bold px-2 py-0.5 rounded ${CLASS_STYLES[rating.classification] || CLASS_STYLES.C}`}>
          {t[`class${rating.classification}`] || `Class ${rating.classification}`}
        </span>
        <span className="text-gray-600 text-xs">
          {rating.overall_score}/100
        </span>
      </div>

      {/* Category bars */}
      <div className="space-y-1.5">
        {Object.entries(CATEGORIES).map(([key, style]) => {
          const cat = rating.categories?.[key]
          if (!cat) return null
          return (
            <div key={key} className="flex items-center gap-2">
              <span className="w-24 text-xs text-gray-600 truncate">
                {t[key] || key}
              </span>
              <div className={`flex-1 h-2 rounded-full ${style.barBg}`}>
                <div
                  className={`h-2 rounded-full ${style.color} transition-all duration-500`}
                  style={{ width: `${cat.score}%` }}
                />
              </div>
              <span className="w-6 text-xs text-gray-500 text-right">{cat.score}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
