import { useEffect, useMemo, useRef, useState } from 'react'
import { TileLayer, CircleMarker, GeoJSON, Popup, useMap, useMapEvents } from 'react-leaflet'
import { LeafletContext, createLeafletContext } from '@react-leaflet/core'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import ProjectLayer from './ProjectLayer'
import SecurityLayer from './SecurityLayer'
import NeighborhoodLayer from './NeighborhoodLayer'
import MapLegend from './MapLegend'
import AddressSearch from './AddressSearch'
import SoldTrendsLayer from './SoldTrendsLayer'
import { BASE_MAPS } from '../baseMaps'
import ListingPopupCard from './ListingPopupCard'

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

function FitToParishes({ parishFeatures, hiddenParishes, visibleGroups, parishToGroup, showNeighborhoods }) {
  const map = useMap()
  // Serialize visible parish set for stable dependency tracking
  const visibleKey = useMemo(() => {
    if (!showNeighborhoods || !parishFeatures?.length) return ''
    return parishFeatures
      .filter(f => {
        const name = f.properties?.name
        if (!name) return false
        const group = parishToGroup?.[name]
        if (!group || !visibleGroups[group]) return false
        if (hiddenParishes?.has(name)) return false
        return true
      })
      .map(f => f.properties.name)
      .sort()
      .join('|')
  }, [parishFeatures, hiddenParishes, visibleGroups, parishToGroup, showNeighborhoods])

  useEffect(() => {
    if (!visibleKey || !parishFeatures?.length) return

    const allNames = parishFeatures.map(f => f.properties?.name).filter(Boolean)
    const visibleNames = visibleKey.split('|')

    // Don't zoom if all parishes are visible
    if (visibleNames.length === allNames.length) return

    const visibleFeatures = parishFeatures.filter(f => visibleNames.includes(f.properties?.name))
    if (visibleFeatures.length === 0) return

    const bounds = L.geoJSON(visibleFeatures).getBounds()
    if (bounds.isValid()) {
      const fitZoom = map.getBoundsZoom(bounds, false, [50, 50])
      const zoom = Math.min(fitZoom + 0.6, 15)
      map.flyTo(bounds.getCenter(), zoom, { duration: 0.8 })
    }
  }, [visibleKey, parishFeatures, map])
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

function ZoomTracker({ onZoom }) {
  const map = useMapEvents({
    zoomend: () => onZoom(map.getZoom()),
  })
  useEffect(() => { onZoom(map.getZoom()) }, [map])
  return null
}

function listingMarkerSize(zoom) {
  const radius = Math.max(3, Math.min(10, zoom - 8))
  const weight = zoom >= 15 ? 1.5 : zoom >= 13 ? 1 : 0.5
  return { radius, weight }
}

// Simple ray-casting point-in-polygon for client-side filtering
function pointInPolygon(lat, lon, ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) {
      inside = !inside
    }
  }
  return inside
}

function isPointInFeature(lat, lon, feature) {
  const geom = feature?.geometry
  if (!geom) return false
  if (geom.type === 'Polygon') {
    return pointInPolygon(lat, lon, geom.coordinates[0])
  }
  if (geom.type === 'MultiPolygon') {
    return geom.coordinates.some(poly => pointInPolygon(lat, lon, poly[0]))
  }
  return false
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
  selectedParishes,
  onLookupResult,
}) {
  const isParishVisible = (name) => {
    if (!showNeighborhoods || !name) return true
    const group = parishToGroup?.[name]
    if (!group || !visibleGroups[group]) return false
    if (hiddenParishes?.has(name)) return false
    return true
  }

  // Build selected parish features for point-in-polygon filtering
  const selectedParishFeatures = useMemo(() => {
    if (!selectedParishes?.size || !parishFeatures?.length) return null
    return parishFeatures.filter(f => {
      const pname = f.properties?.name || f.properties?.Freguesia
      return pname && selectedParishes.has(pname)
    })
  }, [selectedParishes, parishFeatures])

  // Build visible parish features for point-in-polygon fallback
  const visibleParishFeatures = useMemo(() => {
    if (!showNeighborhoods || !parishFeatures?.length) return null
    return parishFeatures.filter(f => {
      const name = f.properties?.name
      if (!name) return false
      const group = parishToGroup?.[name]
      if (!group || !visibleGroups[group]) return false
      if (hiddenParishes?.has(name)) return false
      return true
    })
  }, [showNeighborhoods, parishFeatures, parishToGroup, visibleGroups, hiddenParishes])

  const allWithCoords = (listings?.filter(l => l.lat && l.lon) ?? [])

  // When parishes are selected for comparison, use point-in-polygon (bypass isParishVisible)
  let withCoords
  if (selectedParishFeatures?.length > 0) {
    withCoords = allWithCoords.filter(l =>
      selectedParishFeatures.some(f => isPointInFeature(l.lat, l.lon, f))
    )
  } else if (showNeighborhoods && visibleParishFeatures?.length > 0) {
    // Use parish name match first; for unknown neighborhoods, fall back to point-in-polygon
    withCoords = allWithCoords.filter(l => {
      if (isParishVisible(l.neighborhood)) return true
      // Neighborhood name not in parishToGroup — check coords against visible parishes
      return visibleParishFeatures.some(f => isPointInFeature(l.lat, l.lon, f))
    })
  } else {
    withCoords = allWithCoords.filter(l => isParishVisible(l.neighborhood))
  }

  const ref = useRef(null)
  const [ctx, setCtx] = useState(null)
  const [mapZoom, setMapZoom] = useState(areaConfig?.zoom || DEFAULT_ZOOM)

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
          <FitToParishes
            parishFeatures={parishFeatures}
            hiddenParishes={hiddenParishes}
            visibleGroups={visibleGroups}
            parishToGroup={parishToGroup}
            showNeighborhoods={showNeighborhoods}
          />
          <FlyToListing listing={selectedListing} />
          <ZoomTracker onZoom={setMapZoom} />

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

            const isSelected = selectedListing && selectedListing.id === l.id

            let markerColor
            if (isSelected) {
              markerColor = { color: '#991b1b', fillColor: '#ef4444' }
            } else if (isSold || isReserved) {
              markerColor = { color: '#000000', fillColor: '#1f2937' }
            } else {
              // Color by flip_score when available — greener = better flip candidate.
              // Band thresholds match backend/scoring_engine.py _rating():
              // A ≥ 60, B ≥ 45, C ≥ 30, D < 30.
              const fs = l.flip_score
              if (fs != null) {
                if (fs >= 60)      markerColor = { color: '#065f46', fillColor: '#10b981' }  // A — emerald
                else if (fs >= 45) markerColor = { color: '#166534', fillColor: '#22c55e' }  // B — green
                else if (fs >= 30) markerColor = { color: '#854d0e', fillColor: '#f59e0b' }  // C — amber
                else               markerColor = { color: '#991b1b', fillColor: '#ef4444' }  // D — red
              } else if (isRent) {
                markerColor = { color: '#6b21a8', fillColor: '#a855f7' }
              } else {
                markerColor = { color: '#475569', fillColor: '#94a3b8' }                    // unscored — slate
              }
            }

            const popupContent = (
              <Popup>
                <ListingPopupCard listing={l} />
              </Popup>
            )

            const handleClick = () => onSelectListing(l)

            if (l.building_geojson) {
              const geojson = typeof l.building_geojson === 'string'
                ? JSON.parse(l.building_geojson) : l.building_geojson
              return (
                <GeoJSON
                  key={`bldg-${l.id}`}
                  data={{ type: 'Feature', geometry: geojson, properties: {} }}
                  style={() => ({ ...markerColor, fillOpacity: (isSold || isReserved) ? 0.5 : 0.35, weight: mapZoom >= 15 ? 3 : 2 })}
                  eventHandlers={{ click: handleClick }}
                >
                  {popupContent}
                </GeoJSON>
              )
            }

            const mSize = listingMarkerSize(mapZoom)
            return (
              <CircleMarker
                key={l.id}
                center={[l.lat, l.lon]}
                radius={mSize.radius}
                pathOptions={{ ...markerColor, fillOpacity: (isSold || isReserved) ? 0.85 : 0.6, weight: (isSold || isReserved) ? mSize.weight + 1 : mSize.weight }}
                eventHandlers={{ click: handleClick }}
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

          <AddressSearch onSelectListing={onSelectListing} onLookupResult={onLookupResult} />
        </LeafletContext.Provider>
      )}

      <MapLegend
        baseMap={baseMap}
        onChangeBaseMap={onChangeBaseMap}
      />
    </div>
  )
}
