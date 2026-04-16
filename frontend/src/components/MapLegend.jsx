import { useState } from 'react'
import { useLanguage } from '../LanguageContext'
import { BASE_MAPS } from '../baseMaps'

export default function MapLegend({ baseMap, onChangeBaseMap, listingTypeFilter = 'sale' }) {
  const { t } = useLanguage()
  const [collapsed, setCollapsed] = useState(false)
  const [baseMapExpanded, setBaseMapExpanded] = useState(false)

  return (
    <div className="absolute bottom-6 right-3 z-[1000] bg-white rounded-lg shadow-md p-3 text-xs min-w-[180px]"
         onWheel={e => e.stopPropagation()}>
      <div
        className="font-semibold text-white flex items-center justify-between cursor-pointer select-none bg-gray-600 -m-3 mb-0 px-3 py-2 rounded-t-lg"
        onClick={() => setCollapsed(c => !c)}
      >
        <span>{t.mapLayers}</span>
        <span className="text-gray-300 text-[10px] ml-2">{collapsed ? '▼' : '▲'}</span>
      </div>

      {collapsed ? null : <>

      {/* Base map selector */}
      <div className="mt-2 mb-2 pb-2 border-b border-gray-100">
        <div
          className="flex items-center justify-between cursor-pointer select-none"
          onClick={() => setBaseMapExpanded(v => !v)}
        >
          <span className="text-gray-700 font-medium">{t.baseMap}</span>
          <span className="text-gray-400 text-[10px] ml-2">{baseMapExpanded ? '▲' : '▼'}</span>
        </div>
        {baseMapExpanded && (
          <div className="mt-1">
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
        )}
      </div>

      {/* Score color bands */}
      <div className="mb-1">
        <span className="text-gray-700 font-medium">
          {listingTypeFilter === 'rent' ? t.rentScoreLabel
            : listingTypeFilter === 'all' ? t.listingScoreLabel
            : t.flipScoreLabel}
        </span>
      </div>
      <div className="flex flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full shrink-0" style={{ background: '#10b981' }} />
          <span className="text-gray-600">A (≥60)</span>
          <span className="w-3 h-3 rounded-full shrink-0 ml-1" style={{ background: '#22c55e' }} />
          <span className="text-gray-600">B (≥45)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full shrink-0" style={{ background: '#f59e0b' }} />
          <span className="text-gray-600">C (≥30)</span>
          <span className="w-3 h-3 rounded-full shrink-0 ml-1" style={{ background: '#ef4444' }} />
          <span className="text-gray-600">D (&lt;30)</span>
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="w-3 h-3 rounded-full shrink-0" style={{ background: '#94a3b8' }} />
          <span className="text-gray-600">{t.unscoredLabel}</span>
          <span className="w-3 h-3 rounded-full shrink-0 ml-1" style={{ background: '#1f2937' }} />
          <span className="text-gray-600">{t.soldLabel}</span>
        </div>
      </div>

      </>}
    </div>
  )
}
