import { CircleMarker, Popup } from 'react-leaflet'
import { useLanguage } from '../LanguageContext'
import { CATEGORIES, getCategory } from '../projectCategories'
import { PARISH_TO_GROUP } from '../neighborhoodGroups'

export default function ProjectLayer({
  projects, visibleCategories,
  showNeighborhoods, visibleGroups, hiddenParishes,
}) {
  const { t, translateTerm } = useLanguage()

  return projects.map(p => {
    if (!p.centroid_lat || !p.centroid_lon) return null

    const catKey = getCategory(p.operation)
    if (!visibleCategories[catKey]) return null

    // If neighborhood overlay is active, filter projects to visible parishes
    if (showNeighborhoods && p.parish) {
      const group = PARISH_TO_GROUP[p.parish]
      if (!group || !visibleGroups[group]) return null
      if (hiddenParishes?.has(p.parish)) return null
    }

    const cat = CATEGORIES[catKey]

    return (
      <CircleMarker
        key={`${p.layer}-${p.source_id}`}
        center={[p.centroid_lat, p.centroid_lon]}
        radius={6}
        pathOptions={{
          color: cat.stroke,
          fillColor: cat.color,
          fillOpacity: 0.85,
          weight: 1.5,
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
  })
}
