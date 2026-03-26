import { useEffect, useRef, useState, useMemo } from 'react'
import { CircleMarker, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet.heat'
import { useLanguage } from '../LanguageContext'
import { CATEGORIES, getCategory } from '../projectCategories'

const HEATMAP_ZOOM_THRESHOLD = 15

// Weight by category importance for heatmap intensity
const CATEGORY_WEIGHT = {
  new_construction: 1.0,
  government: 0.9,
  extension: 0.7,
  demolition: 0.8,
  planning: 0.5,
  conservation: 0.4,
  alteration: 0.3,
  unidentified: 0.2,
}

function ProjectHeatmap({ points }) {
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
      radius: 14,
      blur: 26,
      maxZoom: 16,
      max: 1.0,
      minOpacity: 0.15,
      gradient: {
        0.0: 'rgba(0,255,255,0)',
        0.2: '#e0f7fa',
        0.4: '#4dd0e1',
        0.6: '#0097a7',
        0.8: '#01579b',
        1.0: '#0d1b5e',
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

function ZoomTracker({ onZoomChange }) {
  const map = useMap()

  useEffect(() => {
    const handler = () => onZoomChange(map.getZoom())
    map.on('zoomend', handler)
    onZoomChange(map.getZoom())
    return () => map.off('zoomend', handler)
  }, [map, onZoomChange])

  return null
}

export default function ProjectLayer({
  projects, visibleCategories,
  showNeighborhoods, visibleGroups, hiddenParishes, parishToGroup,
}) {
  const { t, translateTerm } = useLanguage()
  const [zoom, setZoom] = useState(10)

  const showDots = zoom >= HEATMAP_ZOOM_THRESHOLD

  // Filter projects once
  const filteredProjects = useMemo(() => {
    return projects.filter(p => {
      if (!p.centroid_lat || !p.centroid_lon) return false
      const catKey = getCategory(p.operation)
      if (!visibleCategories[catKey]) return false
      if (showNeighborhoods && p.parish) {
        const group = parishToGroup?.[p.parish]
        if (!group || !visibleGroups[group]) return false
        if (hiddenParishes?.has(p.parish)) return false
      }
      return true
    })
  }, [projects, visibleCategories, showNeighborhoods, visibleGroups, hiddenParishes])

  // Heatmap data points: [lat, lon, intensity]
  const heatPoints = useMemo(() => {
    if (showDots) return []
    return filteredProjects.map(p => {
      const weight = CATEGORY_WEIGHT[getCategory(p.operation)] ?? 0.3
      return [p.centroid_lat, p.centroid_lon, weight]
    })
  }, [filteredProjects, showDots])

  return (
    <>
      <ZoomTracker onZoomChange={setZoom} />

      {!showDots && <ProjectHeatmap points={heatPoints} />}

      {showDots && filteredProjects.map(p => {
        const catKey = getCategory(p.operation)
        const cat = CATEGORIES[catKey]

        return (
          <CircleMarker
            key={`${p.layer}-${p.source_id}`}
            center={[p.centroid_lat, p.centroid_lon]}
            radius={3}
            pathOptions={{
              color: cat.stroke,
              fillColor: cat.color,
              fillOpacity: 0.55,
              weight: 1,
            }}
          >
            <Popup maxWidth={280}>
              <div className="text-sm space-y-1">
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className="text-xs font-semibold px-1.5 py-0.5 rounded text-white"
                    style={{ background: cat.color }}
                  >
                    {cat.label}
                  </span>
                  <span className="text-xs text-gray-400">
                    {p.layer === 'permit' ? t.issuedPermit : t.pending}
                  </span>
                </div>
                {p.subject && <div className="font-semibold text-gray-900 leading-tight">{translateTerm(p.subject)}</div>}
                {p.operation && <div className="text-gray-600">{translateTerm(p.operation)}</div>}
                {(p.procedure || p.typology) && (
                  <div className="text-gray-400 text-xs">
                    {[p.typology, p.procedure].filter(Boolean).map(translateTerm).join(' · ')}
                  </div>
                )}
                {p.address && <div className="text-gray-500 text-xs">{p.address}</div>}
                {p.parish && <div className="text-gray-400 text-xs">{p.parish}</div>}
                <div className="border-t border-gray-100 pt-1 mt-1 text-xs text-gray-400 space-y-0.5">
                  {p.date_submitted && <div>{t.submitted} {p.date_submitted}</div>}
                  {p.permit_number && <div>{t.permit} {p.permit_number}</div>}
                  {p.date_permit && <div>{t.issued} {p.date_permit}</div>}
                </div>
              </div>
            </Popup>
          </CircleMarker>
        )
      })}
    </>
  )
}
