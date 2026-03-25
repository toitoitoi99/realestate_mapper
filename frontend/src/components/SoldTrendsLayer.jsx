import { useEffect, useRef, useState, useMemo } from 'react'
import { GeoJSON, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet.heat'
import { useLanguage } from '../LanguageContext'

/**
 * Color scale for price change: red (increase) through white (flat) to blue (decrease).
 * Matches real estate conventions where "hot" = rising prices.
 */
function pctChangeColor(pct) {
  // Clamp to [-15, +15] range for color mapping
  const clamped = Math.max(-15, Math.min(15, pct))
  const t = (clamped + 15) / 30 // 0 = -15%, 0.5 = 0%, 1 = +15%

  if (t >= 0.5) {
    // 0% to +15%: white → red
    const s = (t - 0.5) * 2 // 0 to 1
    const r = 255
    const g = Math.round(255 * (1 - s * 0.8))
    const b = Math.round(255 * (1 - s * 0.9))
    return `rgb(${r},${g},${b})`
  } else {
    // -15% to 0%: blue → white
    const s = t * 2 // 0 to 1
    const r = Math.round(255 * s)
    const g = Math.round(200 + 55 * s)
    const b = 255
    return `rgb(${r},${g},${b})`
  }
}

function SoldHeatmap({ points }) {
  const map = useMap()
  const heatLayerRef = useRef(null)

  useEffect(() => {
    if (heatLayerRef.current) {
      map.removeLayer(heatLayerRef.current)
    }

    if (points.length === 0) {
      heatLayerRef.current = null
      return
    }

    heatLayerRef.current = L.heatLayer(points, {
      radius: 18,
      blur: 25,
      maxZoom: 16,
      max: 1.0,
      minOpacity: 0.2,
      gradient: {
        0.0: 'rgba(59,130,246,0)',
        0.15: '#93c5fd',
        0.35: '#dbeafe',
        0.5: '#fef3c7',
        0.7: '#fca5a5',
        0.85: '#ef4444',
        1.0: '#991b1b',
      },
    }).addTo(map)

    return () => {
      if (heatLayerRef.current) {
        map.removeLayer(heatLayerRef.current)
      }
    }
  }, [points, map])

  return null
}

export default function SoldTrendsLayer({
  parishFeatures,
  trends,
  points,
}) {
  const { t } = useLanguage()

  // Build a trend lookup by parish
  const trendMap = useMemo(() => {
    const map = {}
    for (const tr of trends) {
      map[tr.parish] = tr
    }
    return map
  }, [trends])

  // Heatmap points: [lat, lon, intensity]
  // Intensity is based on the parish's pct_change, normalized to 0-1
  const heatPoints = useMemo(() => {
    if (!points || points.length === 0) return []

    return points
      .filter(p => {
        const tr = trendMap[p.parish]
        return tr && p.lat && p.lon
      })
      .map(p => {
        const tr = trendMap[p.parish]
        // Map pct_change to 0-1: -15% → 0, 0% → 0.5, +15% → 1
        const intensity = Math.max(0, Math.min(1, (tr.pct_change + 15) / 30))
        return [p.lat, p.lon, intensity]
      })
  }, [points, trendMap])

  // Choropleth overlay on parish polygons
  const visibleFeatures = useMemo(() => {
    if (!parishFeatures?.length) return []
    return parishFeatures.filter(f => trendMap[f.properties.name])
  }, [parishFeatures, trendMap])

  if (visibleFeatures.length === 0 && heatPoints.length === 0) return null

  const geojson = { type: 'FeatureCollection', features: visibleFeatures }

  const style = (feature) => {
    const tr = trendMap[feature.properties.name]
    const pct = tr ? tr.pct_change : 0
    return {
      fillColor: pctChangeColor(pct),
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
    const changeColor = tr.pct_change >= 0 ? '#dc2626' : '#2563eb'

    layer.bindPopup(`
      <div style="font-size:13px;font-weight:600">${name}</div>
      <div style="font-size:18px;font-weight:700;color:${changeColor};margin:4px 0">
        ${sign}${tr.pct_change.toFixed(1)}%
      </div>
      <div style="font-size:11px;color:#6b7280">
        ${t.earlyPeriod ?? 'Early period'}: <b>€${fmt(tr.avg_early)}/m²</b>
        <span style="color:#9ca3af">(${tr.count_early} ${t.sales ?? 'sales'})</span>
      </div>
      <div style="font-size:11px;color:#6b7280">
        ${t.latePeriod ?? 'Late period'}: <b>€${fmt(tr.avg_late)}/m²</b>
        <span style="color:#9ca3af">(${tr.count_late} ${t.sales ?? 'sales'})</span>
      </div>
      <div style="font-size:10px;color:#9ca3af;margin-top:4px">
        ${tr.count} ${t.totalSales ?? 'total sales'}
      </div>
    `, { maxWidth: 260 })

    layer.on('mouseover', () => layer.setStyle({ fillOpacity: 0.65 }))
    layer.on('mouseout', () => layer.setStyle({ fillOpacity: 0.45 }))
  }

  const key = JSON.stringify(Object.keys(trendMap).sort())

  return (
    <>
      <GeoJSON
        key={key}
        data={geojson}
        style={style}
        onEachFeature={onEachFeature}
      />
      <SoldHeatmap points={heatPoints} />
    </>
  )
}
