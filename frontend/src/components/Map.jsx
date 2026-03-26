import { useEffect, useRef } from 'react'
import { MapContainer, TileLayer, CircleMarker, GeoJSON, Popup, useMap } from 'react-leaflet'
import { useLanguage } from '../LanguageContext'
import 'leaflet/dist/leaflet.css'
import ProjectLayer from './ProjectLayer'
import SecurityLayer from './SecurityLayer'
import NeighborhoodLayer from './NeighborhoodLayer'
import MapLegend from './MapLegend'
import AddressSearch from './AddressSearch'
import ChatPanel from './ChatPanel'
import SoldTrendsLayer from './SoldTrendsLayer'
import { PARISH_TO_GROUP } from '../neighborhoodGroups'
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

export default function Map({
  areaConfig,
  baseMap, onChangeBaseMap,
  listings, neighborhoods,
  onSelectNeighborhood, selectedNeighborhood,
  onSelectListing,
  projects, showProjects, onToggleProjects,
  visibleCategories, onToggleCategory,
  securityPois, showSecurity, onToggleSecurity,
  showNeighborhoods, onToggleNeighborhoods, visibleGroups, onToggleGroup,
  parishFeatures, hiddenParishes, onToggleParish,
  showSoldTrends, onToggleSoldTrends, soldTrendsData, soldDateRange, onSoldDateRangeChange,
}) {
  const { t } = useLanguage()
  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  const isParishVisible = (name) => {
    if (!showNeighborhoods || !name) return true
    const group = PARISH_TO_GROUP[name]
    if (!group || !visibleGroups[group]) return false
    if (hiddenParishes?.has(name)) return false
    return true
  }

  const withCoords = (listings?.filter(l => l.lat && l.lon) ?? [])
    .filter(l => isParishVisible(l.neighborhood))

  return (
    <div style={{ flex: 1, position: 'relative', isolation: 'isolate' }}>
      <MapContainer center={areaConfig?.center || DEFAULT_CENTRE} zoom={areaConfig?.zoom || DEFAULT_ZOOM} scrollWheelZoom={true} style={{ height: '100%', width: '100%', zIndex: 0 }}>
        <TileLayer
          key={baseMap}
          attribution={BASE_MAPS[baseMap].attribution}
          url={BASE_MAPS[baseMap].url}
          maxZoom={BASE_MAPS[baseMap].maxZoom}
        />

        <FlyToArea areaConfig={areaConfig} />
        <FlyTo neighborhood={selectedNeighborhood} listings={withCoords} />

        {/* Neighborhood polygon overlays */}
        <NeighborhoodLayer
          showNeighborhoods={showNeighborhoods}
          parishFeatures={parishFeatures}
          visibleGroups={visibleGroups}
          hiddenParishes={hiddenParishes}
          neighborhoods={neighborhoods}
        />

        {/* Listing markers — building outlines when available, dots as fallback */}
        {withCoords.map(l => {
          const isRent = l.listing_type === 'rent'
          const isSold = l.status === 'sold'
          const isReserved = l.status === 'reserved'

          let markerColor
          if (isSold || isReserved) {
            markerColor = { color: '#92400e', fillColor: '#f59e0b' }   // amber = sold/reserved
          } else if (isRent) {
            markerColor = { color: '#065f46', fillColor: '#10b981' }   // green = rent
          } else {
            markerColor = { color: '#1d4ed8', fillColor: '#3b82f6' }   // blue = sale
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

        {/* Construction project markers */}
        {showProjects && (
          <ProjectLayer
            projects={projects ?? []}
            visibleCategories={visibleCategories}
            showNeighborhoods={showNeighborhoods}
            visibleGroups={visibleGroups}
            hiddenParishes={hiddenParishes}
          />
        )}

        {/* Security POI markers */}
        {showSecurity && (
          <SecurityLayer
            pois={securityPois ?? []}
            visibleLayers={{ police_psp: true, police_municipal: true, cctv: true }}
            showNeighborhoods={showNeighborhoods}
            visibleGroups={visibleGroups}
            hiddenParishes={hiddenParishes}
          />
        )}

        {/* Sold price trends overlay */}
        {showSoldTrends && (
          <SoldTrendsLayer
            parishFeatures={parishFeatures}
            trends={soldTrendsData.trends}
            points={soldTrendsData.points}
          />
        )}

        <AddressSearch />
      </MapContainer>

      <ChatPanel />

      <MapLegend
        baseMap={baseMap}
        onChangeBaseMap={onChangeBaseMap}
        showProjects={showProjects}
        onToggleProjects={onToggleProjects}
        visibleCategories={visibleCategories}
        onToggleCategory={onToggleCategory}
        projects={projects}
        showSecurity={showSecurity}
        onToggleSecurity={onToggleSecurity}
        securityPois={securityPois}
        showNeighborhoods={showNeighborhoods}
        onToggleNeighborhoods={onToggleNeighborhoods}
        visibleGroups={visibleGroups}
        onToggleGroup={onToggleGroup}
        hiddenParishes={hiddenParishes}
        onToggleParish={onToggleParish}
        showSoldTrends={showSoldTrends}
        onToggleSoldTrends={onToggleSoldTrends}
        soldDateRange={soldDateRange}
        onSoldDateRangeChange={onSoldDateRangeChange}
        soldTrendsData={soldTrendsData}
      />
    </div>
  )
}
