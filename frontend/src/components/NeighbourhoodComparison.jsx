import { useLanguage } from '../LanguageContext'

const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

function ComparisonCard({ name, stats, onRemove }) {
  const { t } = useLanguage()
  if (!stats) {
    return (
      <div className="bg-gray-50 rounded p-2 text-xs flex-1 min-w-0">
        <div className="flex items-center justify-between mb-1">
          <span className="font-medium text-gray-700 truncate">{name}</span>
          <button onClick={() => onRemove(name)} className="text-gray-400 hover:text-gray-600 cursor-pointer ml-1">✕</button>
        </div>
        <div className="text-gray-400">{t.noStatsAvailable}</div>
      </div>
    )
  }

  return (
    <div className="bg-gray-50 rounded p-2 text-xs flex-1 min-w-0">
      <div className="flex items-center justify-between mb-1.5">
        <span className="font-medium text-blue-700 truncate">{name}</span>
        <button onClick={() => onRemove(name)} className="text-gray-400 hover:text-gray-600 cursor-pointer ml-1">✕</button>
      </div>
      <div className="space-y-1">
        <div>
          <span className="text-gray-400">{t.avgPrice}</span>
          <div className="font-medium text-gray-700">{stats.avg_price_per_sqm ? `€${fmt(stats.avg_price_per_sqm)}/m²` : '—'}</div>
        </div>
        <div>
          <span className="text-gray-400">{t.medianPrice}</span>
          <div className="font-medium text-gray-700">{stats.median_price_per_sqm ? `€${fmt(stats.median_price_per_sqm)}/m²` : '—'}</div>
        </div>
        <div>
          <span className="text-gray-400">{t.listingsForSale}</span>
          <div className="font-medium text-gray-700">{stats.listing_count ?? '—'}</div>
        </div>
        <div>
          <span className="text-gray-400">{t.typicalType}</span>
          <div className="font-medium text-gray-700">{stats.most_common_rooms != null ? `T${stats.most_common_rooms}` : '—'}</div>
        </div>
        {stats.min_price_per_sqm != null && stats.max_price_per_sqm != null && (
          <div>
            <div className="flex justify-between text-gray-400" style={{ fontSize: '9px' }}>
              <span>€{fmt(stats.min_price_per_sqm)}</span>
              <span>€{fmt(stats.max_price_per_sqm)}</span>
            </div>
            <div className="h-1 bg-gradient-to-r from-green-300 to-red-300 rounded-full" />
          </div>
        )}
      </div>
    </div>
  )
}

function ComparisonTable({ parishes, parishStats, onRemove }) {
  const { t } = useLanguage()
  const names = [...parishes]

  const rows = [
    { label: t.avgPrice, key: 'avg_price_per_sqm', format: v => v ? `€${fmt(v)}/m²` : '—' },
    { label: t.medianPrice, key: 'median_price_per_sqm', format: v => v ? `€${fmt(v)}/m²` : '—' },
    { label: t.listingsForSale, key: 'listing_count', format: v => v ?? '—' },
    { label: t.typicalType, key: 'most_common_rooms', format: v => v != null ? `T${v}` : '—' },
    { label: 'Min €/m²', key: 'min_price_per_sqm', format: v => v ? `€${fmt(v)}` : '—' },
    { label: 'Max €/m²', key: 'max_price_per_sqm', format: v => v ? `€${fmt(v)}` : '—' },
    { label: t.avgPrice?.replace('/m²', ''), key: 'avg_price', format: v => v ? `€${fmt(v)}` : '—' },
  ]

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-gray-200">
            <th className="text-left py-1 pr-2 text-gray-500 font-normal sticky left-0 bg-white"></th>
            {names.map(name => (
              <th key={name} className="text-left py-1 px-1 font-medium text-blue-700 whitespace-nowrap">
                <div className="flex items-center gap-1">
                  <span className="truncate max-w-[80px]">{name}</span>
                  <button onClick={() => onRemove(name)} className="text-gray-400 hover:text-gray-600 cursor-pointer shrink-0">✕</button>
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.key} className="border-b border-gray-50">
              <td className="py-1 pr-2 text-gray-400 whitespace-nowrap sticky left-0 bg-white">{row.label}</td>
              {names.map(name => {
                const s = parishStats?.[name]
                return (
                  <td key={name} className="py-1 px-1 text-gray-700 whitespace-nowrap">
                    {s ? row.format(s[row.key]) : '—'}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function NeighbourhoodComparison({ selectedParishes, parishStats, onRemoveParish, onClearAll }) {
  const { t } = useLanguage()
  const count = selectedParishes.size
  if (count === 0) return null

  const parishes = [...selectedParishes]

  return (
    <div className="border-y border-blue-100 bg-blue-50/50 px-3 py-2 shrink-0">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-blue-700">
          {t.comparison ?? 'Comparison'} ({count})
        </span>
        <button
          onClick={onClearAll}
          className="text-xs text-blue-400 hover:text-blue-600 cursor-pointer"
        >
          {t.clearComparison ?? 'Clear'}
        </button>
      </div>

      {count <= 3 ? (
        <div className="flex gap-2">
          {parishes.map(name => (
            <ComparisonCard
              key={name}
              name={name}
              stats={parishStats?.[name]}
              onRemove={onRemoveParish}
            />
          ))}
        </div>
      ) : (
        <ComparisonTable
          parishes={selectedParishes}
          parishStats={parishStats}
          onRemove={onRemoveParish}
        />
      )}
    </div>
  )
}
