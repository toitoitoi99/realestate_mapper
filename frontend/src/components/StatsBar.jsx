import { useLanguage } from '../LanguageContext'

export default function StatsBar({ stats, ineStats, onScrape, scraping }) {
  const { lang, toggle, t } = useLanguage()
  if (!stats) return null

  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  return (
    <div className="flex items-center gap-6 px-4 py-2 bg-white border-b border-gray-200 text-sm shrink-0">
      <span className="font-semibold text-gray-800">🏠 {t.appTitle}</span>
      <div className="flex gap-4 text-gray-600">
        <span><b className="text-gray-900">{fmt(stats.total_listings)}</b> {t.listings}</span>
        <span><b className="text-gray-900">€{fmt(stats.avg_price_eur)}</b> {t.avgAsk}</span>
        <span><b className="text-gray-900">€{fmt(stats.avg_price_per_sqm)}</b>{t.perSqmAsk}</span>
        {ineStats && (
          <span title={`INE median transaction price · ${ineStats.period_label}`}>
            <b className="text-emerald-700">€{fmt(ineStats.median_price_per_sqm)}</b>
            <span className="text-gray-500">{t.perSqmSold}</span>
            <span className="text-gray-400 text-xs ml-1">
              ({ineStats.period_label.replace(' Quarter ', 'Q').replace('st','').replace('nd','').replace('rd','').replace('th','')})
            </span>
          </span>
        )}
        <span><b className="text-gray-900">{fmt(stats.neighborhood_count)}</b> {t.neighborhoods}</span>
      </div>

      {stats.last_scrape && (
        <span className="text-gray-400 text-xs ml-auto hidden md:block">
          {t.lastScrape} {new Date(stats.last_scrape).toLocaleDateString()}
        </span>
      )}

      {/* Language toggle */}
      <div className="flex rounded overflow-hidden border border-gray-200 text-xs ml-auto md:ml-0">
        {['en', 'pt'].map(l => (
          <button
            key={l}
            onClick={() => lang !== l && toggle()}
            className={`px-2 py-1 ${lang === l ? 'bg-gray-800 text-white' : 'bg-white text-gray-500 hover:bg-gray-50'}`}
          >
            {l.toUpperCase()}
          </button>
        ))}
      </div>

      <button
        onClick={onScrape}
        disabled={scraping}
        className="px-3 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 cursor-pointer"
      >
        {scraping ? t.scraping : t.runScraper}
      </button>
    </div>
  )
}
