import { useEffect } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet'
import { useLanguage } from '../LanguageContext'
import 'leaflet/dist/leaflet.css'
import ProjectLayer from './ProjectLayer'
import SecurityLayer from './SecurityLayer'
import NeighborhoodLayer from './NeighborhoodLayer'
import MapLegend from './MapLegend'
import AddressSearch from './AddressSearch'
import { PARISH_TO_GROUP } from '../neighborhoodGroups'
import RarityBadge from './RarityBadge'

const CENTRE = [38.68, -9.10]
const ZOOM = 10

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

export default function Map({
  listings, neighborhoods,
  onSelectNeighborhood, selectedNeighborhood,
  onSelectListing,
  projects, showProjects, onToggleProjects,
  visibleCategories, onToggleCategory,
  securityPois, showSecurity, onToggleSecurity,
  showNeighborhoods, onToggleNeighborhoods, visibleGroups, onToggleGroup,
  parishFeatures, hiddenParishes, onToggleParish,
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
    <div style={{ flex: 1, position: 'relative' }}>
      <MapContainer center={CENTRE} zoom={ZOOM} style={{ height: '100%', width: '100%' }}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        <FlyTo neighborhood={selectedNeighborhood} listings={withCoords} />

        {/* Neighborhood polygon overlays */}
        <NeighborhoodLayer
          showNeighborhoods={showNeighborhoods}
          parishFeatures={parishFeatures}
          visibleGroups={visibleGroups}
          hiddenParishes={hiddenParishes}
          neighborhoods={neighborhoods}
        />

        {/* Listing markers */}
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

          return (
            <CircleMarker
              key={l.id}
              center={[l.lat, l.lon]}
              radius={5}
              pathOptions={{ ...markerColor, fillOpacity: 0.8, weight: 1 }}
            >
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
                  <button
                    onClick={() => onSelectListing(l)}
                    className="text-blue-600 hover:text-blue-800 cursor-pointer bg-transparent border-none p-0 text-sm"
                  >
                    {t.viewDetails}
                  </button>
                </div>
              </Popup>
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

        <AddressSearch />
      </MapContainer>

      <MapLegend
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
      />
    </div>
  )
}
