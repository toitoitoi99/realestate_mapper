import { GeoJSON } from 'react-leaflet'

export default function NeighborhoodLayer({
  showNeighborhoods,
  parishFeatures,   // GeoJSON Feature[] from /api/parishes
  visibleGroups,
  hiddenParishes,   // Set<string> of individually hidden parish names
  neighborhoods,    // neighborhood stats rows (for price info)
  groups,           // dynamic municipality-based groups
  parishToGroup,    // parish name → group key lookup
}) {
  if (!showNeighborhoods || !parishFeatures?.length) return null

  const statsMap = Object.fromEntries(neighborhoods.map(n => [n.name, n]))

  const visibleFeatures = parishFeatures.filter(f => {
    const name = f.properties.name
    const group = parishToGroup?.[name]
    if (!group || !visibleGroups[group]) return false
    if (hiddenParishes?.has(name)) return false
    return true
  })

  if (visibleFeatures.length === 0) return null

  const geojson = { type: 'FeatureCollection', features: visibleFeatures }

  const style = (feature) => {
    const group = parishToGroup?.[feature.properties.name]
    const g = groups?.[group] ?? {}
    return {
      color: g.color ?? '#6b7280',
      fillColor: g.fillColor ?? g.color ?? '#6b7280',
      fillOpacity: 0.25,
      weight: 2,
      opacity: 0.85,
    }
  }

  const onEachFeature = (feature, layer) => {
    const { name, municipality } = feature.properties
    const s = statsMap[name]
    const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

    let priceHtml = ''
    if (s?.avg_price_per_sqm) {
      const hasSold = s.count_sold >= 2 && s.avg_sold_price_per_sqm
      const delta = hasSold
        ? ((s.avg_sold_price_per_sqm - s.avg_price_per_sqm) / s.avg_price_per_sqm * 100)
        : null
      const deltaColor = delta == null ? '' : delta < 0 ? '#16a34a' : '#dc2626'
      const deltaStr = delta != null
        ? `<span style="color:${deltaColor};font-weight:700">${delta > 0 ? '+' : ''}${delta.toFixed(1)}%</span>`
        : ''
      priceHtml = `
        <div style="font-size:11px;color:#6b7280;margin-top:4px">
          Asking: <b>€${fmt(s.avg_price_per_sqm)}/m²</b>
          ${hasSold ? `· Sold: <b>€${fmt(s.avg_sold_price_per_sqm)}/m²</b> ${deltaStr} <span style="color:#9ca3af">(${s.count_sold} sold)</span>` : ''}
        </div>`
    }

    layer.bindPopup(`
      <div style="font-size:13px;font-weight:600">${name}</div>
      <div style="font-size:11px;color:#6b7280">${municipality ?? ''}</div>
      ${priceHtml}
    `, { maxWidth: 240 })

    layer.on('mouseover', () => layer.setStyle({ fillOpacity: 0.45 }))
    layer.on('mouseout', () => layer.setStyle({ fillOpacity: 0.25 }))
  }

  // key forces remount when visibility changes (react-leaflet GeoJSON doesn't hot-update)
  const key = JSON.stringify(visibleGroups) + JSON.stringify([...(hiddenParishes ?? [])])

  return (
    <GeoJSON
      key={key}
      data={geojson}
      style={style}
      onEachFeature={onEachFeature}
    />
  )
}
