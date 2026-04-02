import { useLanguage } from '../LanguageContext'

export default function NeighbourhoodStats({ neighborhood, typology, parishNote }) {
  const { t } = useLanguage()
  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  if (!neighborhood && !typology) {
    return (
      <div className="bg-gray-50 rounded p-2 mt-1 mb-1 text-xs text-gray-400">
        {t.noStatsAvailable}
      </div>
    )
  }

  const n = neighborhood || {}

  return (
    <div className="bg-gray-50 rounded p-2 mt-1 mb-1 text-xs space-y-1.5">
      {parishNote && (
        <div className="text-gray-400 italic" style={{ fontSize: '10px' }}>{parishNote}</div>
      )}
      {/* Prices */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        <div>
          <span className="text-gray-400">{t.avgPrice}</span>
          <div className="font-medium text-gray-700">
            {n.avg_price_per_sqm ? `€${fmt(n.avg_price_per_sqm)}/m²` : '—'}
          </div>
        </div>
        <div>
          <span className="text-gray-400">{t.medianPrice}</span>
          <div className="font-medium text-gray-700">
            {n.median_price_per_sqm ? `€${fmt(n.median_price_per_sqm)}/m²` : '—'}
          </div>
        </div>

        {/* Typical type */}
        <div>
          <span className="text-gray-400">{t.typicalType}</span>
          <div className="font-medium text-gray-700">
            {typology ? `T${typology.most_common_rooms}` : '—'}
          </div>
        </div>

        {/* Listings count */}
        <div>
          <span className="text-gray-400">{t.listingsForSale}</span>
          <div className="font-medium text-gray-700">
            {typology ? typology.total : (n.listing_count ?? '—')}
          </div>
        </div>

        {/* Rent */}
        <div>
          <span className="text-gray-400">{t.rentPerSqm}</span>
          <div className="font-medium text-gray-700">
            {n.avg_rent_per_sqm ? `€${fmt(n.avg_rent_per_sqm)}` : '—'}
          </div>
        </div>

        {/* Sold */}
        <div>
          <span className="text-gray-400">{t.soldCount}</span>
          <div className="font-medium text-gray-700">
            {n.avg_sold_price_per_sqm
              ? `€${fmt(n.avg_sold_price_per_sqm)}/m² (${n.count_sold ?? 0})`
              : '—'}
          </div>
        </div>
      </div>

      {/* Price range bar */}
      {n.min_price_per_sqm != null && n.max_price_per_sqm != null && (
        <div>
          <div className="flex justify-between text-gray-400" style={{ fontSize: '9px' }}>
            <span>€{fmt(n.min_price_per_sqm)}/m²</span>
            <span>€{fmt(n.max_price_per_sqm)}/m²</span>
          </div>
          <div className="h-1.5 bg-gradient-to-r from-green-300 to-red-300 rounded-full" />
        </div>
      )}

      {/* Typology distribution mini bar */}
      {typology?.distribution && Object.keys(typology.distribution).length > 0 && (
        <div className="flex gap-0.5 h-3 rounded overflow-hidden">
          {Object.entries(typology.distribution)
            .sort(([a], [b]) => Number(a) - Number(b))
            .map(([rooms, count]) => {
              const pct = (count / typology.total) * 100
              if (pct < 2) return null
              return (
                <div
                  key={rooms}
                  className="relative group"
                  style={{
                    width: `${pct}%`,
                    background: `hsl(${210 + Number(rooms) * 30}, 60%, 55%)`,
                  }}
                  title={`T${rooms}: ${count} (${pct.toFixed(0)}%)`}
                >
                  {pct > 12 && (
                    <span className="absolute inset-0 flex items-center justify-center text-white font-medium" style={{ fontSize: '8px' }}>
                      T{rooms}
                    </span>
                  )}
                </div>
              )
            })}
        </div>
      )}
    </div>
  )
}
