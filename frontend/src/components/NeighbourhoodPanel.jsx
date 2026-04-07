import { useState } from 'react'
import { useLanguage } from '../LanguageContext'
import { CATEGORIES, getCategory } from '../projectCategories'
import { SECURITY_LAYER_CONFIG } from './SecurityLayer'
import NeighbourhoodStats from './NeighbourhoodStats'

export default function NeighbourhoodPanel({
  // Neighbourhoods
  showNeighborhoods, onToggleNeighborhoods,
  visibleGroups, onToggleGroup,
  neighborhoodGroups, hiddenParishes, onToggleParish,
  neighborhoods, typologies, parishStats,
  onSelectNeighborhood,
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
  const [expandedGroups, setExpandedGroups] = useState({})
  const [neighbourhoodListCollapsed, setNeighbourhoodListCollapsed] = useState(false)
  const [projectsListCollapsed, setProjectsListCollapsed] = useState(false)
  const [priceTrendsCollapsed, setPriceTrendsCollapsed] = useState(false)
  const [securityCollapsed, setSecurityCollapsed] = useState(false)

  // Master toggle: toggles all layers on/off together
  const handleMasterToggle = () => {
    const allOn = showNeighborhoods && showProjects && showSoldTrends && showSecurity
    if (allOn) {
      // Turn everything off
      if (showNeighborhoods) onToggleNeighborhoods()
      if (showProjects) onToggleProjects()
      if (showSoldTrends) onToggleSoldTrends()
      if (showSecurity) onToggleSecurity()
    } else {
      // Turn everything on
      if (!showNeighborhoods) onToggleNeighborhoods()
      if (!showProjects) onToggleProjects()
      if (!showSoldTrends) onToggleSoldTrends()
      if (!showSecurity) onToggleSecurity()
    }
  }

  const toggleExpand = (key) =>
    setExpandedGroups(prev => ({ ...prev, [key]: !prev[key] }))

  // Neighbourhood group checkbox state
  const groupCheckState = (key) => {
    const parishes = neighborhoodGroups[key]?.neighborhoods ?? []
    const hidden = parishes.filter(n => hiddenParishes?.has(n))
    if (hidden.length === 0) return 'all'
    if (hidden.length === parishes.length) return 'none'
    return 'some'
  }

  const toggleGroupParishes = (key) => {
    const state = groupCheckState(key)
    const parishes = neighborhoodGroups[key]?.neighborhoods ?? []
    parishes.forEach(name => {
      const isHidden = hiddenParishes?.has(name)
      if (state === 'all' && !isHidden) onToggleParish(name)
      else if (state !== 'all' && isHidden) onToggleParish(name)
    })
  }

  // Project counts
  const projectCounts = {}
  if (projects) {
    for (const p of projects) {
      const cat = getCategory(p.operation)
      projectCounts[cat] = (projectCounts[cat] ?? 0) + 1
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
    ['police_psp',       t.pspStations],
    ['police_municipal', t.municipalPolice],
    ['cctv',             t.cctvCameras],
  ]

  // Look up neighborhood stats by name
  const getNeighborhoodStats = (name) =>
    neighborhoods?.find(n => n.name === name)

  return (
    <div className="overflow-y-auto p-3 text-xs space-y-3" style={{ maxHeight: '50vh' }}>

      {/* ── Collapse/expand all toggle ── */}
      <div className="flex justify-end">
        <button
          className="text-gray-400 hover:text-gray-600 text-[10px] cursor-pointer select-none"
          onClick={() => {
            const allCollapsed = neighbourhoodListCollapsed && projectsListCollapsed && priceTrendsCollapsed && securityCollapsed
            setNeighbourhoodListCollapsed(!allCollapsed)
            setProjectsListCollapsed(!allCollapsed)
            setPriceTrendsCollapsed(!allCollapsed)
            setSecurityCollapsed(!allCollapsed)
          }}
        >
          {neighbourhoodListCollapsed && projectsListCollapsed && priceTrendsCollapsed && securityCollapsed
            ? '▼ Expand all'
            : '▲ Collapse all'}
        </button>
      </div>

      {/* ── Neighbourhoods ── */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <div
            className="flex items-center gap-2 cursor-pointer select-none flex-1"
            onClick={handleMasterToggle}
          >
            <input
              type="checkbox"
              readOnly
              checked={showNeighborhoods && showProjects && showSoldTrends && showSecurity}
              ref={el => { if (el) el.indeterminate = (showNeighborhoods || showProjects || showSoldTrends || showSecurity) && !(showNeighborhoods && showProjects && showSoldTrends && showSecurity) }}
              className="cursor-pointer"
            />
            <span className="text-gray-700 font-medium text-sm">{t.neighborhoods}</span>
          </div>
          <button
            className="text-gray-400 hover:text-gray-600 text-[10px] cursor-pointer select-none shrink-0"
            onClick={() => setNeighbourhoodListCollapsed(v => !v)}
          >
            {neighbourhoodListCollapsed ? '▼ Show' : '▲ Hide'}
          </button>
        </div>

        {selectedParishes?.size > 0 && (
          <button
            className="text-xs text-blue-500 hover:text-blue-700 cursor-pointer mb-1"
            onClick={onClearSelectedParishes}
          >
            {t.clearComparison ?? 'Clear comparison'} ({selectedParishes.size})
          </button>
        )}

        {!neighbourhoodListCollapsed && <div className="pl-1 mt-1 space-y-1">
          {Object.entries(neighborhoodGroups ?? {}).map(([key, group]) => {
            const checkState = groupCheckState(key)
            const isExpanded = expandedGroups[key]
            return (
              <div key={key}>
                <div className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    readOnly
                    checked={showNeighborhoods && visibleGroups[key] && checkState !== 'none'}
                    ref={el => { if (el) el.indeterminate = showNeighborhoods && visibleGroups[key] && checkState === 'some' }}
                    className="cursor-pointer shrink-0"
                    onClick={(e) => {
                      e.stopPropagation()
                      if (!showNeighborhoods) {
                        // Turn on layer, show only this group
                        Object.keys(neighborhoodGroups).forEach(k => {
                          if (k !== key && visibleGroups[k]) onToggleGroup(k)
                          if (k === key && !visibleGroups[k]) onToggleGroup(k)
                        })
                        onToggleNeighborhoods()
                      } else if (visibleGroups[key]) {
                        toggleGroupParishes(key)
                      } else {
                        onToggleGroup(key)
                      }
                    }}
                  />
                  <div
                    className="flex items-center gap-1 flex-1 cursor-pointer select-none"
                    onClick={() => {
                      if (!showNeighborhoods) {
                        Object.keys(neighborhoodGroups).forEach(k => {
                          if (k !== key && visibleGroups[k]) onToggleGroup(k)
                          if (k === key && !visibleGroups[k]) onToggleGroup(k)
                        })
                        onToggleNeighborhoods()
                      } else {
                        onToggleGroup(key)
                      }
                    }}
                  >
                    <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: group.color }} />
                    <span className="text-gray-500 leading-tight flex-1">{t[key] ?? group.label}</span>
                  </div>
                  <button
                    className="text-gray-300 hover:text-gray-500 ml-1 leading-none cursor-pointer"
                    onClick={(e) => { e.stopPropagation(); toggleExpand(key) }}
                  >
                    {isExpanded ? '▲' : '▼'}
                  </button>
                </div>

                {/* Group stats + parish checkboxes */}
                {isExpanded && (() => {
                  const visibleParishes = group.neighborhoods.filter(n => !hiddenParishes?.has(n))
                  const visibleCount = visibleParishes.length
                  const totalCount = group.neighborhoods.length
                  const allVisible = visibleCount === totalCount

                  // When subset selected, aggregate parish-level stats
                  let statsData = null
                  let typologyData = null
                  let parishNote = null

                  if (allVisible) {
                    // Full municipality stats
                    statsData = getNeighborhoodStats(group.label)
                    typologyData = typologies?.[group.label]
                  } else if (visibleCount > 0 && parishStats) {
                    // Aggregate stats for visible parishes only
                    const matched = visibleParishes.map(n => parishStats[n]).filter(Boolean)
                    if (matched.length > 0) {
                      const totalListings = matched.reduce((s, p) => s + (p.listing_count || 0), 0)
                      const allPsm = matched.filter(p => p.avg_price_per_sqm)
                      const allMedianPsm = matched.filter(p => p.median_price_per_sqm)
                      const allMinPsm = matched.filter(p => p.min_price_per_sqm != null)
                      const allMaxPsm = matched.filter(p => p.max_price_per_sqm != null)

                      // Weighted average by listing count
                      const wavg = (arr, key) => {
                        const total = arr.reduce((s, p) => s + (p.listing_count || 0), 0)
                        if (!total) return null
                        return arr.reduce((s, p) => s + (p[key] || 0) * (p.listing_count || 0), 0) / total
                      }

                      // Aggregate room distributions
                      const roomsDist = {}
                      let mostCommonRooms = null
                      matched.forEach(p => {
                        if (p.rooms_distribution) {
                          Object.entries(p.rooms_distribution).forEach(([r, c]) => {
                            roomsDist[r] = (roomsDist[r] || 0) + c
                          })
                        }
                      })
                      if (Object.keys(roomsDist).length > 0) {
                        mostCommonRooms = Number(Object.entries(roomsDist).sort((a, b) => b[1] - a[1])[0][0])
                      }

                      statsData = {
                        listing_count: totalListings,
                        avg_price_per_sqm: allPsm.length ? Math.round(wavg(allPsm, 'avg_price_per_sqm')) : null,
                        median_price_per_sqm: allMedianPsm.length ? Math.round(wavg(allMedianPsm, 'median_price_per_sqm')) : null,
                        min_price_per_sqm: allMinPsm.length ? Math.min(...allMinPsm.map(p => p.min_price_per_sqm)) : null,
                        max_price_per_sqm: allMaxPsm.length ? Math.max(...allMaxPsm.map(p => p.max_price_per_sqm)) : null,
                      }
                      typologyData = {
                        most_common_rooms: mostCommonRooms,
                        total: totalListings,
                        distribution: roomsDist,
                      }
                    }
                    parishNote = `${visibleCount} of ${totalCount} parishes`
                  }

                  return <>
                    <NeighbourhoodStats
                      neighborhood={statsData}
                      typology={typologyData}
                      parishNote={parishNote}
                    />

                    {/* Individual parish checkboxes */}
                    <div className="pl-5 mt-0.5 space-y-0.5">
                      {group.neighborhoods.map(name => {
                        const isHidden = hiddenParishes?.has(name)
                        const handleParishToggle = () => {
                          if (!showNeighborhoods) {
                            // Turn on layer, show only this parish's group
                            Object.keys(neighborhoodGroups).forEach(k => {
                              if (k !== key && visibleGroups[k]) onToggleGroup(k)
                              if (k === key && !visibleGroups[k]) onToggleGroup(k)
                            })
                            // Hide all parishes in this group except the clicked one
                            group.neighborhoods.forEach(n => {
                              if (n !== name && !hiddenParishes?.has(n)) onToggleParish(n)
                              if (n === name && hiddenParishes?.has(n)) onToggleParish(n)
                            })
                            onToggleNeighborhoods()
                          } else {
                            onToggleParish(name)
                          }
                        }
                        return (
                          <div key={name} className="flex items-center gap-1">
                            <input
                              type="checkbox"
                              readOnly
                              checked={showNeighborhoods && !isHidden}
                              className="cursor-pointer"
                              onClick={(e) => { e.stopPropagation(); handleParishToggle() }}
                            />
                            <span
                              className={`leading-tight cursor-pointer select-none flex-1 ${
                                selectedParishes?.has(name)
                                  ? 'text-blue-600 font-medium'
                                  : 'text-gray-400 hover:text-gray-600'
                              }`}
                              onClick={() => onToggleSelectedParish?.(name)}
                            >
                              {name}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  </>
                })()}
              </div>
            )
          })}
        </div>}
      </div>

      {/* ── Construction Projects ── */}
      <div className="border-t border-gray-100 pt-2">
        <div className="flex items-center gap-2 mb-1">
          <div
            className="flex items-center gap-2 cursor-pointer select-none flex-1"
            onClick={onToggleProjects}
          >
            <input type="checkbox" readOnly checked={showProjects} className="cursor-pointer" />
            <span className="text-gray-700 font-medium text-sm">{t.constructionProjects}</span>
          </div>
          <button
            className="text-gray-400 hover:text-gray-600 text-[10px] cursor-pointer select-none shrink-0"
            onClick={() => setProjectsListCollapsed(v => !v)}
          >
            {projectsListCollapsed ? '▼ Show' : '▲ Hide'}
          </button>
        </div>

        {!projectsListCollapsed && <div className="pl-1 mt-1 space-y-1">
          {/* Density gradient */}
          <div className="mb-2">
            <div className="text-gray-400 mb-1">{t.density}</div>
            <div
              className="h-2 rounded-full"
              style={{ background: 'linear-gradient(to right, #e0f7fa, #4dd0e1, #0097a7, #01579b, #0d1b5e)' }}
            />
            <div className="flex justify-between text-gray-300 mt-0.5" style={{ fontSize: '9px' }}>
              <span>{t.low}</span>
              <span>{t.high}</span>
            </div>
          </div>
          {Object.entries(CATEGORIES).map(([key, cat]) => (
            <div
              key={key}
              className="flex items-center gap-2 cursor-pointer select-none"
              onClick={() => onToggleCategory(key)}
            >
              <input type="checkbox" readOnly checked={visibleCategories[key] ?? true} className="cursor-pointer" />
              <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: cat.color }} />
              <span className="text-gray-500 leading-tight">
                {cat.label}
                {projectCounts[key] != null && (
                  <span className="text-gray-300 ml-1">({projectCounts[key].toLocaleString()})</span>
                )}
              </span>
            </div>
          ))}
        </div>}
      </div>

      {/* ── Price Trends (Sold) ── */}
      <div className="border-t border-gray-100 pt-2">
        <div className="flex items-center gap-2 mb-1">
          <div
            className="flex items-center gap-2 cursor-pointer select-none flex-1"
            onClick={onToggleSoldTrends}
          >
            <input type="checkbox" readOnly checked={showSoldTrends} className="cursor-pointer" />
            <span className="text-gray-700 font-medium text-sm">{t.soldTrends}</span>
          </div>
          <button
            className="text-gray-400 hover:text-gray-600 text-[10px] cursor-pointer select-none shrink-0"
            onClick={() => setPriceTrendsCollapsed(v => !v)}
          >
            {priceTrendsCollapsed ? '▼ Show' : '▲ Hide'}
          </button>
        </div>

        {!priceTrendsCollapsed && <div className="pl-1 mt-1 space-y-2">
          <div className="space-y-1">
            <div className="text-gray-400">{t.dateRange}</div>
            <div className="flex gap-1 items-center">
              <input
                type="month"
                value={soldDateRange.start.slice(0, 7)}
                onChange={e => {
                  const val = e.target.value
                  if (val) onSoldDateRangeChange(prev => ({ ...prev, start: val + '-01' }))
                }}
                className="text-xs border border-gray-200 rounded px-1 py-0.5 w-[110px]"
              />
              <span className="text-gray-300">→</span>
              <input
                type="month"
                value={soldDateRange.end.slice(0, 7)}
                onChange={e => {
                  const val = e.target.value
                  if (val) onSoldDateRangeChange(prev => ({ ...prev, end: val + '-31' }))
                }}
                className="text-xs border border-gray-200 rounded px-1 py-0.5 w-[110px]"
              />
            </div>
          </div>

          <div>
            <div
              className="h-2 rounded-full"
              style={{ background: 'linear-gradient(to right, #3b82f6, #93c5fd, #ffffff, #fca5a5, #dc2626)' }}
            />
            <div className="flex justify-between text-gray-300 mt-0.5" style={{ fontSize: '9px' }}>
              <span>−15%</span>
              <span>0%</span>
              <span>+15%</span>
            </div>
          </div>

          {soldTrendsData?.trends?.length > 0 && (
            <div className="text-gray-400" style={{ fontSize: '10px' }}>
              {soldTrendsData.trends.length} {t.parishesWithData}
            </div>
          )}
        </div>}
      </div>

      {/* ── Security ── */}
      <div className="border-t border-gray-100 pt-2">
        <div className="flex items-center gap-2 mb-1">
          <div
            className="flex items-center gap-2 cursor-pointer select-none flex-1"
            onClick={onToggleSecurity}
          >
            <input type="checkbox" readOnly checked={showSecurity} className="cursor-pointer" />
            <span className="text-gray-700 font-medium text-sm">{t.security}</span>
          </div>
          <button
            className="text-gray-400 hover:text-gray-600 text-[10px] cursor-pointer select-none shrink-0"
            onClick={() => setSecurityCollapsed(v => !v)}
          >
            {securityCollapsed ? '▼ Show' : '▲ Hide'}
          </button>
        </div>

        {!securityCollapsed && <div className="pl-1 mt-1 space-y-1">
          {SECURITY_LAYERS.map(([key, label]) => {
            const cfg = SECURITY_LAYER_CONFIG[key]
            return (
              <div key={key} className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: cfg.color }} />
                <span className="text-gray-500 leading-tight">
                  {label}
                  {securityCounts[key] != null && (
                    <span className="text-gray-300 ml-1">({securityCounts[key]})</span>
                  )}
                </span>
              </div>
            )
          })}
        </div>}
      </div>
    </div>
  )
}
