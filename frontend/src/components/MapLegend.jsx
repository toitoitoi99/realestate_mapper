import { useState } from 'react'
import { useLanguage } from '../LanguageContext'
import { BASE_MAPS } from '../baseMaps'

export default function MapLegend({ baseMap, onChangeBaseMap }) {
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

      {/* Listings key */}
      <div className="flex items-center gap-2">
        <span className="w-3 h-3 rounded-full shrink-0" style={{ background: '#3b82f6' }} />
        <span className="text-gray-600">{t.forSale}</span>
        <span className="w-3 h-3 rounded-full shrink-0 ml-1" style={{ background: '#a855f7' }} />
        <span className="text-gray-600">{t.rentLabel}</span>
        <span className="w-3 h-3 rounded-full shrink-0 ml-1" style={{ background: '#1f2937' }} />
        <span className="text-gray-600">{t.soldLabel}</span>
      </div>

      </>}
    </div>
  )
}
