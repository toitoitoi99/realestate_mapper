import { useEffect, useRef, useState } from 'react'
import { TileLayer, CircleMarker, GeoJSON, Popup, useMap } from 'react-leaflet'
import { LeafletContext, createLeafletContext } from '@react-leaflet/core'
import L from 'leaflet'
import { useLanguage } from '../LanguageContext'
import 'leaflet/dist/leaflet.css'
import ProjectLayer from './ProjectLayer'
import SecurityLayer from './SecurityLayer'
import NeighborhoodLayer from './NeighborhoodLayer'
import MapLegend from './MapLegend'
import AddressSearch from './AddressSearch'
import ChatPanel from './ChatPanel'
import SoldTrendsLayer from './SoldTrendsLayer'
import { BASE_MAPS } from '../baseMaps'
import RarityBadge from './RarityBadge'

const DEFAULT_CENTRE = [38.68, -9.10]
const DEFAULT_ZOOM = 10

function priceColor(gradient) {
  if (gradient == null) return '#94a3b8'
  const r = Math.round(gradient * 220)
  const g = Math.round((1 - gradient) * 180)
  return `rgb(${r},${g},60)`
}

function FlyTo({ neighborhood, listings }) {
  const map = useMap()
  useEffect(() => {
    if (neighborhood && listings?.length > 0) {
      const withCoords = listings.filter(l => l.lat && l.lon)
      if (withCoords.length > 0) {
        const lats = withCoords.map(l => l.lat)
        const lons = withCoords.map(l => l.lon)
        map.fitBounds([
          [Math.min(...lats) - 0.005, Math.min(...lons) - 0.005],
          [Math.max(...lats) + 0.005, Math.max(...lons) + 0.005],
        ], { padding: [40, 40] })
      }
    }
  }, [neighborhood, listings, map])
  return null
}

function InvalidateOnResize() {
  const map = useMap()
  useEffect(() => {
    const container = map.getContainer()
    const observer = new ResizeObserver(() => {
      map.invalidateSize()
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [map])
  return null
}

function FlyToListing({ listing }) {
  const map = useMap()
  useEffect(() => {
    if (listing?.lat && listing?.lon) {
      map.flyTo([listing.lat, listing.lon], 16, { duration: 1 })
    }
  }, [listing?.id, map])
  return null
}

function FlyToArea({ areaConfig }) {
  const map = useMap()
  const initialRef = useRef(true)
  useEffect(() => {
    if (!areaConfig?.center) return
    if (initialRef.current) {
      initialRef.current = false
      return
    }
    map.flyTo(areaConfig.center, areaConfig.zoom || DEFAULT_ZOOM, { duration: 1.5 })
  }, [areaConfig?.center?.[0], areaConfig?.center?.[1], areaConfig?.zoom, map])
  return null
}

// Swap tile layer when baseMap changes by removing old and adding new
function DynamicTileLayer({ baseMap }) {
  const map = useMap()
  const layerRef = useRef(null)

  useEffect(() => {
    if (layerRef.current) {
      map.removeLayer(layerRef.current)
    }
    const config = BASE_MAPS[baseMap]
    const opts = { attribution: config.attribution }
    if (config.maxZoom != null) opts.maxZoom = config.maxZoom
    layerRef.current = L.tileLayer(config.url, opts).addTo(map)
    return () => {
      if (layerRef.current) map.removeLayer(layerRef.current)
    }
  }, [baseMap, map])

  return null
}

export default function Map({
  areaConfig,
  baseMap, onChangeBaseMap,
  listings, neighborhoods,
  onSelectNeighborhood, selectedNeighborhood,
  onSelectListing,
  projects, showProjects,
  visibleCategories,
  securityPois, showSecurity,
  showNeighborhoods, visibleGroups,
  neighborhoodGroups, parishToGroup,
  parishFeatures, hiddenParishes,
  selectedListing,
  showSoldTrends, soldTrendsData,
}) {
  const { t } = useLanguage()
  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  const isParishVisible = (name) => {
    if (!showNeighborhoods || !name) return true
    const group = parishToGroup?.[name]
    if (!group || !visibleGroups[group]) return false
    if (hiddenParishes?.has(name)) return false
    return true
  }

  const withCoords = (listings?.filter(l => l.lat && l.lon) ?? [])
    .filter(l => isParishVisible(l.neighborhood))

  const ref = useRef(null)
  const [ctx, setCtx] = useState(null)

  useEffect(() => {
    if (!ref.current) return
    const center = areaConfig?.center || DEFAULT_CENTRE
    const zoom = areaConfig?.zoom || DEFAULT_ZOOM
    const map = L.map(ref.current).setView(center, zoom)
    setCtx(createLeafletContext(map))
    return () => map.remove()
  }, [])

  return (
    <div style={{ flex: 1, position: 'relative', isolation: 'isolate' }}>
      <div ref={ref} style={{ height: '100%', width: '100%' }} />

      {ctx && (
        <LeafletContext.Provider value={ctx}>
          <DynamicTileLayer baseMap={baseMap} />
          <InvalidateOnResize />
          <FlyToArea areaConfig={areaConfig} />
          <FlyTo neighborhood={selectedNeighborhood} listings={withCoords} />
          <FlyToListing listing={selectedListing} />

          <NeighborhoodLayer
            showNeighborhoods={showNeighborhoods}
            parishFeatures={parishFeatures}
            visibleGroups={visibleGroups}
            hiddenParishes={hiddenParishes}
            neighborhoods={neighborhoods}
            groups={neighborhoodGroups}
            parishToGroup={parishToGroup}
          />

          {withCoords.map(l => {
            const isRent = l.listing_type === 'rent'
            const isSold = l.status === 'sold'
            const isReserved = l.status === 'reserved'

            let markerColor
            if (isSold || isReserved) {
              markerColor = { color: '#92400e', fillColor: '#f59e0b' }
            } else if (isRent) {
              markerColor = { color: '#065f46', fillColor: '#10b981' }
            } else {
              markerColor = { color: '#1d4ed8', fillColor: '#3b82f6' }
            }

            const popupContent = (
              <Popup>
                <div className="text-sm">
                  {(isSold || isReserved) && (
                    <span className="text-xs font-semibold text-amber-700 uppercase tracking-wide">
                      {isSold ? `${t.sold} · ` : `${t.reserved} · `}
                    </span>
                  )}
                  <b>€{fmt(l.price_amount)}{isRent ? '/mo' : ''}</b>
                  {l.size_sqm && <> · {fmt(l.size_sqm)} m²</>}
                  {l.rooms != null && <> · T{l.rooms}</>}<br />
                  {l.neighborhood && <span className="text-gray-500">{l.neighborhood}</span>}
                  <br />
                  <RarityBadge score={l.rarity_score} factors={l.rarity_factors} compact />
                  {l.rarity_score != null && ' '}
                  <a href={l.url} target="_blank" rel="noopener noreferrer" className="text-blue-600">
                    {t.viewListing}
                  </a>
                </div>
              </Popup>
            )

            if (l.building_geojson) {
              const geojson = typeof l.building_geojson === 'string'
                ? JSON.parse(l.building_geojson) : l.building_geojson
              return (
                <GeoJSON
                  key={`bldg-${l.id}`}
                  data={{ type: 'Feature', geometry: geojson, properties: {} }}
                  style={() => ({ ...markerColor, fillOpacity: 0.35, weight: 2 })}
                >
                  {popupContent}
                </GeoJSON>
              )
            }

            return (
              <CircleMarker
                key={l.id}
                center={[l.lat, l.lon]}
                radius={3}
                pathOptions={{ ...markerColor, fillOpacity: 0.6, weight: 0.5 }}
              >
                {popupContent}
              </CircleMarker>
            )
          })}

          {showProjects && (
            <ProjectLayer
              projects={projects ?? []}
              visibleCategories={visibleCategories}
              showNeighborhoods={showNeighborhoods}
              visibleGroups={visibleGroups}
              hiddenParishes={hiddenParishes}
              parishToGroup={parishToGroup}
            />
          )}

          {showSecurity && (
            <SecurityLayer
              pois={securityPois ?? []}
              visibleLayers={{ police_psp: true, police_municipal: true, cctv: true }}
              showNeighborhoods={showNeighborhoods}
              visibleGroups={visibleGroups}
              hiddenParishes={hiddenParishes}
              parishToGroup={parishToGroup}
            />
          )}

          {showSoldTrends && (
            <SoldTrendsLayer
              parishFeatures={parishFeatures}
              trends={soldTrendsData.trends}
              points={soldTrendsData.points}
            />
          )}

          <AddressSearch />
        </LeafletContext.Provider>
      )}

      <ChatPanel />

      <MapLegend
        baseMap={baseMap}
        onChangeBaseMap={onChangeBaseMap}
      />
    </div>
  )
}
