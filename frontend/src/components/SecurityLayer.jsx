import { CircleMarker, Popup } from 'react-leaflet'
import { useLanguage } from '../LanguageContext'

const LAYER_CONFIG = {
  police_psp: {
    color: '#4f46e5',
    stroke: '#312e81',
    radius: 7,
  },
  police_municipal: {
    color: '#7c3aed',
    stroke: '#4a1d96',
    radius: 7,
  },
  cctv: {
    color: '#ea580c',
    stroke: '#7c2d12',
    radius: 5,
  },
}

export const SECURITY_LAYER_CONFIG = LAYER_CONFIG

export default function SecurityLayer({ pois, visibleLayers, showNeighborhoods, visibleGroups, hiddenParishes, parishToGroup }) {
  const { t } = useLanguage()

  const LAYER_LABELS = {
    police_psp:       t.pspStation,
    police_municipal: t.municipalPoliceLabel,
    cctv:             t.cctvCamera,
  }

  return pois.map(poi => {
    if (!poi.lat || !poi.lon) return null
    if (!visibleLayers[poi.layer]) return null

    if (showNeighborhoods && poi.parish) {
      const group = parishToGroup?.[poi.parish]
      if (!group || !visibleGroups[group]) return null
      if (hiddenParishes?.has(poi.parish)) return null
    }

    const cfg = LAYER_CONFIG[poi.layer] ?? LAYER_CONFIG.police_psp
    const label = LAYER_LABELS[poi.layer] ?? poi.layer

    return (
      <CircleMarker
        key={`security-${poi.layer}-${poi.id}`}
        center={[poi.lat, poi.lon]}
        radius={cfg.radius}
        pathOptions={{
          color: cfg.stroke,
          fillColor: cfg.color,
          fillOpacity: 0.9,
          weight: 1.5,
        }}
      >
        <Popup maxWidth={240}>
          <div className="text-sm space-y-1">
            <div
              className="text-xs font-semibold px-1.5 py-0.5 rounded text-white inline-block mb-1"
              style={{ background: cfg.color }}
            >
              {label}
            </div>
            {poi.name && <div className="font-semibold text-gray-900 leading-tight">{poi.name}</div>}
            {poi.address && <div className="text-gray-500 text-xs">{poi.address}</div>}
            {poi.parish && <div className="text-gray-400 text-xs">{poi.parish}</div>}
            {poi.phone && (
              <div className="text-gray-400 text-xs border-t border-gray-100 pt-1 mt-1">
                {poi.phone}
              </div>
            )}
          </div>
        </Popup>
      </CircleMarker>
    )
  })
}
