import { useMemo } from 'react'
import { GeoJSON } from 'react-leaflet'
import { useLanguage } from '../LanguageContext'

function pctChangeColor(pct) {
  const clamped = Math.max(-15, Math.min(15, pct))
  const t = (clamped + 15) / 30 // 0 = -15%, 0.5 = flat, 1 = +15%

  if (t >= 0.5) {
    // 0% to +15%: white → green (rising)
    const s = (t - 0.5) * 2
    return `rgb(${Math.round(255 * (1 - s * 0.9))},${Math.round(180 + 75 * (1 - s))},${Math.round(255 * (1 - s * 0.9))})`
  } else {
    // -15% to 0%: red → white (falling)
    const s = t * 2
    return `rgb(255,${Math.round(255 * s)},${Math.round(255 * s)})`
  }
}

export default function SoldTrendsLayer({ parishFeatures, trends }) {
  const { t } = useLanguage()

  const trendMap = useMemo(() => {
    const map = {}
    for (const tr of trends) map[tr.parish] = tr
    return map
  }, [trends])

  const visibleFeatures = useMemo(() => {
    if (!parishFeatures?.length) return []
    return parishFeatures.filter(f => trendMap[f.properties.name])
  }, [parishFeatures, trendMap])

  if (visibleFeatures.length === 0) return null

  const geojson = { type: 'FeatureCollection', features: visibleFeatures }

  const style = (feature) => {
    const tr = trendMap[feature.properties.name]
    return {
      fillColor: pctChangeColor(tr ? tr.pct_change : 0),
      fillOpacity: 0.45,
      color: '#6b7280',
      weight: 1.5,
      opacity: 0.6,
    }
  }

  const onEachFeature = (feature, layer) => {
    const { name } = feature.properties
    const tr = trendMap[name]
    if (!tr) return

    const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'
    const sign = tr.pct_change >= 0 ? '+' : ''
    const changeColor = tr.pct_change >= 0 ? '#16a34a' : '#dc2626'

    layer.bindPopup(`
      <div style="font-size:13px;font-weight:600">${name}</div>
      <div style="font-size:18px;font-weight:700;color:${changeColor};margin:4px 0">
        ${sign}${tr.pct_change.toFixed(1)}%
      </div>
      <div style="font-size:11px;color:#6b7280">
        Before: <b>€${fmt(tr.avg_early)}/m²</b>
      </div>
      <div style="font-size:11px;color:#6b7280">
        After: <b>€${fmt(tr.avg_late)}/m²</b>
      </div>
      <div style="font-size:10px;color:#9ca3af;margin-top:4px">
        ${tr.count} price change${tr.count !== 1 ? 's' : ''} tracked
      </div>
    `, { maxWidth: 260 })

    layer.on('mouseover', () => layer.setStyle({ fillOpacity: 0.65 }))
    layer.on('mouseout', () => layer.setStyle({ fillOpacity: 0.45 }))
  }

  const key = JSON.stringify(trends.map(tr => [tr.parish, tr.pct_change]).sort())

  return (
    <GeoJSON
      key={key}
      data={geojson}
      style={style}
      onEachFeature={onEachFeature}
    />
  )
}
