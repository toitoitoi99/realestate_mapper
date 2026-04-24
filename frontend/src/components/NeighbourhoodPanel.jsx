import { useState, useMemo } from 'react'
import { useLanguage } from '../LanguageContext'
import { CATEGORIES, getCategory } from '../projectCategories'
import { SECURITY_LAYER_CONFIG } from './SecurityLayer'

function getBBox(feature) {
  let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity
  function visit(c) {
    if (c[1] < minLat) minLat = c[1]
    if (c[1] > maxLat) maxLat = c[1]
    if (c[0] < minLon) minLon = c[0]
    if (c[0] > maxLon) maxLon = c[0]
  }
  function extract(geom) {
    if (!geom) return
    if (geom.type === 'Point') { visit(geom.coordinates); return }
    if (geom.type === 'LineString' || geom.type === 'MultiPoint') { geom.coordinates.forEach(visit); return }
    if (geom.type === 'Polygon' || geom.type === 'MultiLineString') { geom.coordinates.forEach(ring => ring.forEach(visit)); return }
    if (geom.type === 'MultiPolygon') { geom.coordinates.forEach(poly => poly.forEach(ring => ring.forEach(visit))); return }
    if (geom.type === 'GeometryCollection') geom.geometries.forEach(extract)
  }
  extract(feature.geometry)
  if (!isFinite(minLat)) return null
  return { minLat, maxLat, minLon, maxLon }
}

function fmt(n) {
  return n != null ? Math.round(n).toLocaleString('pt-PT') : null
}

function DeltaBadge({ ask, sold }) {
  if (!ask || !sold) return null
  const pct = ((ask - sold) / sold) * 100
  const color = pct > 5 ? 'text-red-500' : pct < -5 ? 'text-green-600' : 'text-gray-500'
  const sign = pct > 0 ? '+' : ''
  return <span className={`text-[10px] font-medium ${color}`}>{sign}{pct.toFixed(1)}% vs sold</span>
}

function NeighbourhoodCard({
  name, parishStats, neighborhoods, typologies, parishToGroup, parishFeatures, projects, onClear,
}) {
  const municipalityName = parishToGroup?.[name] ?? name
  const parishStat = parishStats?.[name]
  const munStat = neighborhoods?.find(n => n.name === municipalityName) ?? neighborhoods?.find(n => n.name === name)
  const typology = typologies?.[municipalityName]

  // Merge: parish stats for prices, municipality stats for sold/rent
  const listingCount = parishStat?.listing_count ?? munStat?.listing_count ?? 0
  const medianAsk = parishStat?.median_price_per_sqm ?? munStat?.median_price_per_sqm
  const avgAsk = parishStat?.avg_price_per_sqm ?? munStat?.avg_price_per_sqm
  const minPsm = parishStat?.min_price_per_sqm ?? munStat?.min_price_per_sqm
  const maxPsm = parishStat?.max_price_per_sqm ?? munStat?.max_price_per_sqm
  const soldPsm = munStat?.avg_sold_price_per_sqm
  const countSold = munStat?.count_sold
  const rentPsm = munStat?.avg_rent_per_sqm
  const avgSize = parishStat?.avg_size
  const mostCommonRooms = parishStat?.most_common_rooms ?? typology?.most_common_rooms
  const roomsDist = parishStat?.rooms_distribution ?? typology?.distribution

  // Count nearby construction projects using bbox of matching parish features
  const nearbyProjects = useMemo(() => {
    if (!projects?.length || !parishFeatures?.length) return null
    const features = parishFeatures.filter(f =>
      f.properties?.name === name || f.properties?.municipality === name
    )
    if (!features.length) return null
    let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity
    for (const f of features) {
      const bb = getBBox(f)
      if (!bb) continue
      minLat = Math.min(minLat, bb.minLat)
      maxLat = Math.max(maxLat, bb.maxLat)
      minLon = Math.min(minLon, bb.minLon)
      maxLon = Math.max(maxLon, bb.maxLon)
    }
    if (!isFinite(minLat)) return null
    const nearby = projects.filter(p =>
      p.lat >= minLat && p.lat <= maxLat && p.lon >= minLon && p.lon <= maxLon
    )
    const byType = {}
    for (const p of nearby) {
      const cat = getCategory(p.operation)
      byType[cat] = (byType[cat] ?? 0) + 1
    }
    return { total: nearby.length, byType }
  }, [name, projects, parishFeatures])

  const roomsTotal = roomsDist ? Object.values(roomsDist).reduce((a, b) => a + b, 0) : 0

  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-primary-tint border-b border-primary-border">
        <div>
          <div className="font-semibold text-gray-800 text-sm">{name}</div>
          {municipalityName !== name && (
            <div className="text-[10px] text-gray-400">{municipalityName}</div>
          )}
        </div>
        <button
          onClick={onClear}
          className="text-gray-400 hover:text-gray-600 text-lg leading-none cursor-pointer px-1"
          title="Clear selection"
        >×</button>
      </div>

      <div className="p-3 space-y-3 text-xs">
        {/* Key headline */}
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-gray-50 rounded p-2">
            <div className="text-gray-400 text-[10px]">Active listings</div>
            <div className="font-bold text-gray-800 text-base">{listingCount}</div>
          </div>
          <div className="bg-gray-50 rounded p-2">
            <div className="text-gray-400 text-[10px]">Typical type</div>
            <div className="font-bold text-gray-800 text-base">
              {mostCommonRooms != null ? `T${mostCommonRooms}` : '—'}
            </div>
          </div>
        </div>

        {/* Prices */}
        <div>
          <div className="font-medium text-gray-600 mb-1">Sale prices</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1">
            <div>
              <div className="text-gray-400 text-[10px]">Median ask</div>
              <div className="font-semibold text-gray-700">
                {medianAsk ? `€${fmt(medianAsk)}/m²` : '—'}
              </div>
            </div>
            <div>
              <div className="text-gray-400 text-[10px]">Avg ask</div>
              <div className="font-semibold text-gray-700">
                {avgAsk ? `€${fmt(avgAsk)}/m²` : '—'}
              </div>
            </div>
            <div>
              <div className="text-gray-400 text-[10px]">Avg sold</div>
              <div className="font-semibold text-gray-700">
                {soldPsm ? `€${fmt(soldPsm)}/m²` : '—'}
                {countSold ? <span className="text-gray-400 font-normal ml-1">({countSold})</span> : null}
              </div>
            </div>
            <div className="flex items-end">
              <DeltaBadge ask={medianAsk ?? avgAsk} sold={soldPsm} />
            </div>
          </div>

          {/* Price range */}
          {minPsm != null && maxPsm != null && (
            <div className="mt-2">
              <div className="h-1.5 bg-gradient-to-r from-green-300 via-yellow-200 to-red-300 rounded-full" />
              <div className="flex justify-between text-gray-400 mt-0.5" style={{ fontSize: '9px' }}>
                <span>€{fmt(minPsm)}/m²</span>
                <span>€{fmt(maxPsm)}/m²</span>
              </div>
            </div>
          )}
        </div>

        {/* Rental */}
        {rentPsm != null && (
          <div>
            <div className="font-medium text-gray-600 mb-1">Rental</div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-gray-700">€{fmt(rentPsm)}/m²/month</span>
              {medianAsk && rentPsm && (
                <span className="text-[10px] text-gray-400">
                  ~{((rentPsm * 12) / medianAsk * 100).toFixed(1)}% gross yield
                </span>
              )}
            </div>
          </div>
        )}

        {/* Avg size */}
        {avgSize != null && (
          <div className="flex items-center gap-1.5 text-gray-500">
            <span className="text-gray-400 text-[10px]">Avg size</span>
            <span className="font-medium">{Math.round(avgSize)} m²</span>
          </div>
        )}

        {/* Room distribution */}
        {roomsDist && roomsTotal > 0 && (
          <div>
            <div className="font-medium text-gray-600 mb-1">Room distribution</div>
            <div className="flex gap-0.5 h-4 rounded overflow-hidden mb-1">
              {Object.entries(roomsDist)
                .sort(([a], [b]) => Number(a) - Number(b))
                .map(([rooms, count]) => {
                  const pct = (count / roomsTotal) * 100
                  if (pct < 2) return null
                  return (
                    <div
                      key={rooms}
                      className="relative"
                      style={{ width: `${pct}%`, background: `hsl(${210 + Number(rooms) * 28}, 58%, 52%)` }}
                      title={`T${rooms}: ${count} (${pct.toFixed(0)}%)`}
                    >
                      {pct > 10 && (
                        <span className="absolute inset-0 flex items-center justify-center text-white font-medium" style={{ fontSize: '8px' }}>
                          T{rooms}
                        </span>
                      )}
                    </div>
                  )
                })}
            </div>
            <div className="flex gap-2 flex-wrap">
              {Object.entries(roomsDist)
                .sort(([a], [b]) => Number(a) - Number(b))
                .map(([rooms, count]) => {
                  const pct = (count / roomsTotal) * 100
                  if (pct < 2) return null
                  return (
                    <span key={rooms} className="text-[10px] text-gray-400">
                      T{rooms} {pct.toFixed(0)}%
                    </span>
                  )
                })}
            </div>
          </div>
        )}

        {/* Construction activity */}
        {nearbyProjects != null && nearbyProjects.total > 0 && (
          <div>
            <div className="font-medium text-gray-600 mb-1">Construction activity</div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-gray-700">{nearbyProjects.total} projects</span>
            </div>
            <div className="flex flex-wrap gap-1 mt-1">
              {Object.entries(nearbyProjects.byType)
                .sort(([, a], [, b]) => b - a)
                .slice(0, 4)
                .map(([cat, count]) => {
                  const catDef = CATEGORIES[cat]
                  return catDef ? (
                    <span
                      key={cat}
                      className="text-[10px] px-1.5 py-0.5 rounded-full text-white"
                      style={{ background: catDef.color }}
                    >
                      {catDef.label} {count}
                    </span>
                  ) : null
                })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function NeighbourhoodPanel({
  // Selection (new primary interaction)
  selectedNeighborhood, onSelectNeighborhood,
  // Data
  neighborhoods, typologies, parishStats, parishFeatures,
  // Groups/parishes
  showNeighborhoods, onToggleNeighborhoods,
  visibleGroups, onToggleGroup,
  neighborhoodGroups, hiddenParishes, onToggleParish,
  parishToGroup,
  // Comparison (legacy)
  selectedParishes, onToggleSelectedParish, onClearSelectedParishes,
  // Construction projects
  showProjects, onToggleProjects,
  visibleCategories, onToggleCategory, projects,
  // Price trends
  showSoldTrends, onToggleSoldTrends,
  soldDateRange, onSoldDateRangeChange, soldTrendsData,
  // Security
  showSecurity, onToggleSecurity, securityPois,
}) {
  const { t } = useLanguage()
  const [search, setSearch] = useState('')
  const [expandedGroups, setExpandedGroups] = useState({})
  const [layersCollapsed, setLayersCollapsed] = useState(true)

  const toggleExpand = (key) =>
    setExpandedGroups(prev => ({ ...prev, [key]: !prev[key] }))

  const handleSelect = (name) => {
    if (selectedNeighborhood === name) {
      onSelectNeighborhood(null)
    } else {
      onSelectNeighborhood(name)
    }
  }

  // Security counts
  const securityCounts = {}
  if (securityPois) {
    for (const p of securityPois) {
      securityCounts[p.layer] = (securityCounts[p.layer] ?? 0) + 1
    }
  }

  const SECURITY_LAYERS = [
    ['police_psp', t.pspStations],
    ['police_municipal', t.municipalPolice],
    ['cctv', t.cctvCameras],
  ]

  const projectCounts = {}
  if (projects) {
    for (const p of projects) {
      const cat = getCategory(p.operation)
      projectCounts[cat] = (projectCounts[cat] ?? 0) + 1
    }
  }

  // Filtered group entries for the browser
  const searchLower = search.toLowerCase()
  const filteredGroups = useMemo(() => {
    if (!neighborhoodGroups) return []
    return Object.entries(neighborhoodGroups).filter(([key, group]) => {
      if (!searchLower) return true
      if (group.label.toLowerCase().includes(searchLower)) return true
      return group.neighborhoods.some(n => n.toLowerCase().includes(searchLower))
    })
  }, [neighborhoodGroups, searchLower])

  return (
    <div className="flex flex-col overflow-hidden" style={{ maxHeight: '100%' }}>
      <div className="overflow-y-auto flex-1 p-3 space-y-3">

        {/* ── Selected neighbourhood stats card ── */}
        {selectedNeighborhood ? (
          <NeighbourhoodCard
            name={selectedNeighborhood}
            parishStats={parishStats}
            neighborhoods={neighborhoods}
            typologies={typologies}
            parishToGroup={parishToGroup}
            parishFeatures={parishFeatures}
            projects={projects}
            onClear={() => onSelectNeighborhood(null)}
          />
        ) : (
          /* ── Neighbourhood browser ── */
          <div>
            <div className="text-xs font-medium text-gray-600 mb-2">Select a neighbourhood</div>

            {/* Search */}
            <input
              type="text"
              placeholder="Search..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary mb-2"
            />

            {/* Municipality + parish list */}
            <div className="space-y-0.5">
              {filteredGroups.map(([key, group]) => {
                const isExpanded = expandedGroups[key] || (searchLower && group.neighborhoods.some(n => n.toLowerCase().includes(searchLower)))
                const munStat = neighborhoods?.find(n => n.name === group.label)
                const munCount = munStat?.listing_count

                return (
                  <div key={key}>
                    {/* Municipality row */}
                    <div className="flex items-center gap-1.5 py-1 rounded hover:bg-gray-50 group cursor-pointer"
                      onClick={() => toggleExpand(key)}
                    >
                      <span
                        className="w-2 h-2 rounded-sm shrink-0"
                        style={{ background: group.color }}
                      />
                      <span
                        className="flex-1 text-xs font-medium text-gray-700 leading-tight"
                        onClick={(e) => { e.stopPropagation(); handleSelect(group.label) }}
                      >
                        {t[key] ?? group.label}
                      </span>
                      {munCount != null && (
                        <span className="text-[10px] text-gray-300 shrink-0">{munCount}</span>
                      )}
                      <span className="text-gray-300 text-[10px] shrink-0">{isExpanded ? '▲' : '▼'}</span>
                    </div>

                    {/* Parish rows */}
                    {isExpanded && (
                      <div className="pl-4 space-y-0.5 mb-1">
                        {group.neighborhoods
                          .filter(n => !searchLower || n.toLowerCase().includes(searchLower) || group.label.toLowerCase().includes(searchLower))
                          .map(name => {
                            const ps = parishStats?.[name]
                            const isSelected = selectedNeighborhood === name
                            return (
                              <div
                                key={name}
                                className={`flex items-center justify-between py-0.5 px-1.5 rounded cursor-pointer text-xs leading-tight transition-colors ${
                                  isSelected
                                    ? 'bg-primary-tint text-primary font-medium'
                                    : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700'
                                }`}
                                onClick={() => handleSelect(name)}
                              >
                                <span>{name}</span>
                                {ps?.listing_count != null && (
                                  <span className="text-[10px] text-gray-300 ml-1 shrink-0">{ps.listing_count}</span>
                                )}
                              </div>
                            )
                          })}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* ── Map layers (collapsible) ── */}
        <div className="border-t border-gray-100 pt-2">
          <button
            className="flex items-center justify-between w-full text-xs font-medium text-gray-500 hover:text-gray-700 cursor-pointer"
            onClick={() => setLayersCollapsed(v => !v)}
          >
            <span>Map layers</span>
            <span className="text-[10px]">{layersCollapsed ? '▼ Show' : '▲ Hide'}</span>
          </button>

          {!layersCollapsed && (
            <div className="mt-2 space-y-3">
              {/* Neighbourhood overlay */}
              <div>
                <div
                  className="flex items-center gap-2 cursor-pointer select-none"
                  onClick={onToggleNeighborhoods}
                >
                  <input type="checkbox" readOnly checked={showNeighborhoods} className="cursor-pointer" />
                  <span className="text-xs text-gray-600">Parish overlay</span>
                </div>
                {showNeighborhoods && (
                  <div className="pl-5 mt-1 space-y-0.5">
                    {Object.entries(neighborhoodGroups ?? {}).map(([key, group]) => (
                      <div
                        key={key}
                        className="flex items-center gap-1.5 cursor-pointer select-none"
                        onClick={() => onToggleGroup(key)}
                      >
                        <input
                          type="checkbox"
                          readOnly
                          checked={visibleGroups[key] ?? true}
                          className="cursor-pointer"
                        />
                        <span className="w-2 h-2 rounded-sm" style={{ background: group.color }} />
                        <span className="text-xs text-gray-500">{t[key] ?? group.label}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Construction projects */}
              <div>
                <div
                  className="flex items-center gap-2 cursor-pointer select-none"
                  onClick={onToggleProjects}
                >
                  <input type="checkbox" readOnly checked={showProjects} className="cursor-pointer" />
                  <span className="text-xs text-gray-600">{t.constructionProjects}</span>
                </div>
                {showProjects && (
                  <div className="pl-5 mt-1 space-y-0.5">
                    {Object.entries(CATEGORIES).map(([key, cat]) => (
                      <div
                        key={key}
                        className="flex items-center gap-2 cursor-pointer select-none"
                        onClick={() => onToggleCategory(key)}
                      >
                        <input type="checkbox" readOnly checked={visibleCategories[key] ?? true} className="cursor-pointer" />
                        <span className="w-2 h-2 rounded-full" style={{ background: cat.color }} />
                        <span className="text-[11px] text-gray-500">
                          {cat.label}
                          {projectCounts[key] != null && <span className="text-gray-300 ml-1">({projectCounts[key].toLocaleString()})</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Price trends */}
              <div>
                <div
                  className="flex items-center gap-2 cursor-pointer select-none"
                  onClick={onToggleSoldTrends}
                >
                  <input type="checkbox" readOnly checked={showSoldTrends} className="cursor-pointer" />
                  <span className="text-xs text-gray-600">{t.soldTrends}</span>
                </div>
                {showSoldTrends && (
                  <div className="pl-5 mt-1 space-y-2">
                    <div className="flex gap-1 items-center">
                      <input
                        type="month"
                        value={soldDateRange.start.slice(0, 7)}
                        onChange={e => { const v = e.target.value; if (v) onSoldDateRangeChange(prev => ({ ...prev, start: v + '-01' })) }}
                        className="text-xs border border-gray-200 rounded px-1 py-0.5 w-[100px]"
                      />
                      <span className="text-gray-300">→</span>
                      <input
                        type="month"
                        value={soldDateRange.end.slice(0, 7)}
                        onChange={e => { const v = e.target.value; if (v) onSoldDateRangeChange(prev => ({ ...prev, end: v + '-31' })) }}
                        className="text-xs border border-gray-200 rounded px-1 py-0.5 w-[100px]"
                      />
                    </div>
                    <div className="h-1.5 rounded-full" style={{ background: 'linear-gradient(to right, #3b82f6, #93c5fd, #ffffff, #fca5a5, #dc2626)' }} />
                    <div className="flex justify-between text-gray-300" style={{ fontSize: '9px' }}>
                      <span>−15%</span><span>0%</span><span>+15%</span>
                    </div>
                    {soldTrendsData?.trends?.length > 0 && (
                      <div className="text-gray-400" style={{ fontSize: '10px' }}>
                        {soldTrendsData.trends.length} {t.parishesWithData}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Security */}
              <div>
                <div
                  className="flex items-center gap-2 cursor-pointer select-none"
                  onClick={onToggleSecurity}
                >
                  <input type="checkbox" readOnly checked={showSecurity} className="cursor-pointer" />
                  <span className="text-xs text-gray-600">{t.security}</span>
                </div>
                {showSecurity && (
                  <div className="pl-5 mt-1 space-y-0.5">
                    {SECURITY_LAYERS.map(([key, label]) => {
                      const cfg = SECURITY_LAYER_CONFIG[key]
                      return (
                        <div key={key} className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full" style={{ background: cfg.color }} />
                          <span className="text-[11px] text-gray-500">
                            {label}
                            {securityCounts[key] != null && <span className="text-gray-300 ml-1">({securityCounts[key]})</span>}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
