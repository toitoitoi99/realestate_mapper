import { useState } from 'react'
import { useLanguage } from '../LanguageContext'
import { CATEGORIES, getCategory } from '../projectCategories'
import { SECURITY_LAYER_CONFIG } from './SecurityLayer'
import { GROUPS } from '../neighborhoodGroups'
import { BASE_MAPS } from '../baseMaps'

export default function MapLegend({
  baseMap, onChangeBaseMap,
  showProjects, onToggleProjects, visibleCategories, onToggleCategory, projects,
  showSecurity, onToggleSecurity, securityPois,
  showNeighborhoods, onToggleNeighborhoods, visibleGroups, onToggleGroup,
  hiddenParishes, onToggleParish,
}) {
  const { t } = useLanguage()
  const [collapsed, setCollapsed] = useState(false)
  const [expandedGroups, setExpandedGroups] = useState({})

  const SECURITY_LAYERS = [
    ['police_psp',       t.pspStations],
    ['police_municipal', t.municipalPolice],
    ['cctv',             t.cctvCameras],
  ]

  const projectCounts = {}
  if (projects) {
    for (const p of projects) {
      const cat = getCategory(p.operation)
      projectCounts[cat] = (projectCounts[cat] ?? 0) + 1
    }
  }

  const securityCounts = {}
  if (securityPois) {
    for (const p of securityPois) {
      securityCounts[p.layer] = (securityCounts[p.layer] ?? 0) + 1
    }
  }

  const toggleExpand = (key) =>
    setExpandedGroups(prev => ({ ...prev, [key]: !prev[key] }))

  // Determine group checkbox state: all checked, none checked, or indeterminate
  const groupCheckState = (key) => {
    const parishes = GROUPS[key].neighborhoods
    const hidden = parishes.filter(n => hiddenParishes?.has(n))
    if (hidden.length === 0) return 'all'
    if (hidden.length === parishes.length) return 'none'
    return 'some'
  }

  // Toggle all parishes in a group
  const toggleGroupParishes = (key) => {
    const state = groupCheckState(key)
    const parishes = GROUPS[key].neighborhoods
    parishes.forEach(name => {
      const isHidden = hiddenParishes?.has(name)
      if (state === 'all' && !isHidden) onToggleParish(name)       // hide all
      else if (state !== 'all' && isHidden) onToggleParish(name)   // show all
    })
  }

  return (
    <div className="absolute bottom-6 right-3 z-[1000] bg-white rounded-lg shadow-md p-3 text-xs min-w-[220px] max-h-[80vh] overflow-y-auto">
      <div
        className="font-semibold text-gray-700 flex items-center justify-between cursor-pointer select-none"
        onClick={() => setCollapsed(c => !c)}
      >
        <span>{t.mapLayers}</span>
        <span className="text-gray-400 text-[10px] ml-2">{collapsed ? '▼' : '▲'}</span>
      </div>

      {collapsed ? null : <>

      {/* Base map selector */}
      <div className="mt-2 mb-2 pb-2 border-b border-gray-100">
        <div className="text-gray-600 font-medium mb-1">{t.baseMap}</div>
        {Object.entries(BASE_MAPS).map(([key, map]) => (
          <label key={key} className="flex items-center gap-2 cursor-pointer select-none py-0.5">
            <input
              type="radio"
              name="baseMap"
              checked={baseMap === key}
              onChange={() => onChangeBaseMap(key)}
              className="cursor-pointer"
            />
            <span className="text-gray-500">{map.name}</span>
          </label>
        ))}
      </div>

      {/* Listings key */}
      <div className="flex items-center gap-2 mb-2">
        <span className="w-3 h-3 rounded-full shrink-0" style={{ background: '#3b82f6' }} />
        <span className="text-gray-600">{t.forSale}</span>
        <span className="w-3 h-3 rounded-full shrink-0 ml-1" style={{ background: '#10b981' }} />
        <span className="text-gray-600">{t.rentLabel}</span>
        <span className="w-3 h-3 rounded-full shrink-0 ml-1" style={{ background: '#f59e0b' }} />
        <span className="text-gray-600">{t.soldLabel}</span>
      </div>

      {/* Neighborhood overlays — master toggle */}
      <div
        className="flex items-center gap-2 cursor-pointer select-none border-t border-gray-100 pt-2 mt-1 mb-1"
        onClick={onToggleNeighborhoods}
      >
        <input type="checkbox" readOnly checked={showNeighborhoods} className="cursor-pointer" />
        <span className="text-gray-700 font-medium">{t.neighborhoods}</span>
      </div>

      {showNeighborhoods && (
        <div className="pl-1 mt-1 space-y-1 border-t border-gray-100 pt-2 mb-2">
          {Object.entries(GROUPS).map(([key, group]) => {
            const checkState = groupCheckState(key)
            const isExpanded = expandedGroups[key]
            return (
              <div key={key}>
                {/* Group row */}
                <div className="flex items-center gap-1">
                  {/* Group master checkbox */}
                  <input
                    type="checkbox"
                    readOnly
                    checked={visibleGroups[key] && checkState !== 'none'}
                    ref={el => { if (el) el.indeterminate = visibleGroups[key] && checkState === 'some' }}
                    className="cursor-pointer shrink-0"
                    onClick={(e) => {
                      e.stopPropagation()
                      if (visibleGroups[key]) {
                        if (checkState !== 'none') toggleGroupParishes(key)
                        else toggleGroupParishes(key) // show all
                      } else {
                        onToggleGroup(key)
                      }
                    }}
                  />
                  {/* Color swatch + label — click to toggle group on/off */}
                  <div
                    className="flex items-center gap-1 flex-1 cursor-pointer select-none"
                    onClick={() => onToggleGroup(key)}
                  >
                    <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: group.color }} />
                    <span className="text-gray-500 leading-tight flex-1">{t[key] ?? group.label}</span>
                  </div>
                  {/* Expand/collapse arrow */}
                  <button
                    className="text-gray-300 hover:text-gray-500 ml-1 leading-none"
                    onClick={(e) => { e.stopPropagation(); toggleExpand(key) }}
                  >
                    {isExpanded ? '▲' : '▼'}
                  </button>
                </div>

                {/* Individual parish checkboxes */}
                {isExpanded && visibleGroups[key] && (
                  <div className="pl-5 mt-0.5 space-y-0.5">
                    {group.neighborhoods.map(name => (
                      <div key={name} className="flex items-center gap-1 cursor-pointer select-none"
                           onClick={() => onToggleParish(name)}>
                        <input
                          type="checkbox"
                          readOnly
                          checked={!hiddenParishes?.has(name)}
                          className="cursor-pointer"
                        />
                        <span className="text-gray-400 leading-tight">{name}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Construction projects */}
      <div
        className="flex items-center gap-2 cursor-pointer select-none border-t border-gray-100 pt-2 mt-1 mb-1"
        onClick={onToggleProjects}
      >
        <input type="checkbox" readOnly checked={showProjects} className="cursor-pointer" />
        <span className="text-gray-700 font-medium">{t.constructionProjects}</span>
      </div>

      {showProjects && (
        <div className="pl-1 mt-1 space-y-1 border-t border-gray-100 pt-2 mb-2">
          {Object.entries(CATEGORIES).map(([key, cat]) => (
            <div
              key={key}
              className="flex items-center gap-2 cursor-pointer select-none"
              onClick={() => onToggleCategory(key)}
            >
              <input
                type="checkbox"
                readOnly
                checked={visibleCategories[key] ?? true}
                className="cursor-pointer"
              />
              <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: cat.color }} />
              <span className="text-gray-500 leading-tight">
                {cat.label}
                {projectCounts[key] != null && (
                  <span className="text-gray-300 ml-1">({projectCounts[key].toLocaleString()})</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Security POIs */}
      <div
        className="flex items-center gap-2 cursor-pointer select-none border-t border-gray-100 pt-2 mt-1"
        onClick={onToggleSecurity}
      >
        <input type="checkbox" readOnly checked={showSecurity} className="cursor-pointer" />
        <span className="text-gray-700 font-medium">{t.security}</span>
      </div>

      {showSecurity && (
        <div className="pl-1 mt-1 space-y-1 border-t border-gray-100 pt-2">
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
        </div>
      )}

      </>}
    </div>
  )
}
