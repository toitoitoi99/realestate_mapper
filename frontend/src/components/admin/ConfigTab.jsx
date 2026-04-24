import { useEffect, useState } from 'react'
import { fetchAreas } from '../../api'
import { BASE_MAPS, DEFAULT_BASE_MAP } from '../../baseMaps'
import { CATEGORIES } from '../../projectCategories'

function Section({ title, hint, children }) {
  return (
    <div className="bg-white rounded border border-gray-200 p-4">
      <h3 className="font-semibold text-gray-800 text-sm">{title}</h3>
      {hint && <p className="text-xs text-gray-500 mt-0.5">{hint}</p>}
      <div className="mt-3">{children}</div>
    </div>
  )
}

function Flag({ on, label }) {
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] ${on ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-gray-50 text-gray-500 border border-gray-200'}`}>
      <span>{on ? '✓' : '—'}</span>{label}
    </span>
  )
}

function AreaCard({ id, area }) {
  return (
    <div className="bg-gray-50 rounded border border-gray-200 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs bg-white rounded px-1.5 py-0.5 border border-gray-200">{id}</span>
            <span className="font-semibold text-gray-800 truncate">{area.name}</span>
          </div>
          <div className="mt-1 flex items-center gap-3 text-xs text-gray-600">
            <span>center <code className="font-mono">{area.center?.[0]?.toFixed(3)}, {area.center?.[1]?.toFixed(3)}</code></span>
            <span>zoom <code className="font-mono">{area.zoom}</code></span>
            {area.ine_geocod && <span>ine_geocod <code className="font-mono">{area.ine_geocod}</code></span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-1 shrink-0">
          <Flag on={!!area.has_projects} label="projects" />
          <Flag on={!!area.has_security} label="security" />
        </div>
      </div>
      {area.idealista_urls && (
        <div className="mt-2 text-[11px] text-gray-500 space-y-0.5 font-mono truncate">
          <div title={area.idealista_urls.sale}>sale: <a href={area.idealista_urls.sale} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{area.idealista_urls.sale}</a></div>
          <div title={area.idealista_urls.rent}>rent: <a href={area.idealista_urls.rent} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">{area.idealista_urls.rent}</a></div>
        </div>
      )}
      {area.parishes_geojson && (
        <div className="mt-1 text-[11px] text-gray-500 font-mono">
          parishes: <code>{area.parishes_geojson}</code>
        </div>
      )}
    </div>
  )
}

function BaseMapCard({ id, map, isDefault }) {
  return (
    <div className={`rounded border p-3 ${isDefault ? 'border-primary bg-primary-tint/30' : 'border-gray-200 bg-gray-50'}`}>
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs bg-white rounded px-1.5 py-0.5 border border-gray-200">{id}</span>
        <span className="font-semibold text-gray-800">{map.name}</span>
        {isDefault && <span className="px-1.5 py-0.5 text-[10px] rounded bg-primary text-white">default</span>}
      </div>
      <div className="mt-1 text-[11px] text-gray-500 font-mono truncate" title={map.url}>{map.url}</div>
      {map.maxZoom && <div className="mt-0.5 text-[11px] text-gray-500">maxZoom: <code className="font-mono">{map.maxZoom}</code></div>}
    </div>
  )
}

function CategoryRow({ id, cat }) {
  return (
    <tr className="border-b border-gray-50 last:border-0">
      <td className="py-2 pr-2">
        <span className="inline-block w-4 h-4 rounded-full align-middle mr-2" style={{ backgroundColor: cat.color, border: `2px solid ${cat.stroke}` }} />
        <span className="font-mono text-xs text-gray-700">{id}</span>
      </td>
      <td className="py-2 pr-2 text-gray-800">{cat.label}</td>
      <td className="py-2 pr-2 font-mono text-xs text-gray-500">{cat.color}</td>
      <td className="py-2 pr-2 font-mono text-xs text-gray-500">{cat.stroke}</td>
      <td className="py-2 text-right">
        <Flag on={cat.defaultVisible} label="visible" />
      </td>
    </tr>
  )
}

export default function ConfigTab() {
  const [areas, setAreas] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchAreas().then(setAreas).catch(e => setError(String(e?.message ?? e)))
  }, [])

  return (
    <div className="p-4 space-y-4">
      <div className="text-xs text-gray-500">
        Read-only view of app configuration. Edits to areas require changing <code className="font-mono">backend/areas.json</code> and restarting;
        base maps and project categories are defined in <code className="font-mono">frontend/src/</code>.
      </div>

      <Section title="Areas" hint="From backend/areas.json — defines the area switcher in the header.">
        {error && <p className="text-xs text-red-600">{error}</p>}
        {!areas && !error && <p className="text-xs text-gray-500">Loading…</p>}
        {areas && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(areas).map(([id, area]) => (
              <AreaCard key={id} id={id} area={area} />
            ))}
          </div>
        )}
      </Section>

      <Section title="Base maps" hint="Map tile providers. The default is loaded on first render.">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {Object.entries(BASE_MAPS).map(([id, map]) => (
            <BaseMapCard key={id} id={id} map={map} isDefault={id === DEFAULT_BASE_MAP} />
          ))}
        </div>
      </Section>

      <Section title="Project categories" hint="Construction-project classification used on the map legend.">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
                <th className="py-2 pr-2">Key</th>
                <th className="py-2 pr-2">Label</th>
                <th className="py-2 pr-2">Fill</th>
                <th className="py-2 pr-2">Stroke</th>
                <th className="py-2 text-right">Default</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(CATEGORIES).map(([id, cat]) => (
                <CategoryRow key={id} id={id} cat={cat} />
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  )
}
