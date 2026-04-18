import { useLanguage } from '../LanguageContext'

export const SCRAPER_OPTIONS = [
  { key: 'idealista',  label: 'Idealista' },
  { key: 'era',        label: 'ERA' },
  { key: 'remax',      label: 'Remax' },
  { key: 'imovirtual', label: 'Imovirtual' },
  { key: 'olx',        label: 'OLX' },
  { key: 'casa_sapo',  label: 'Casa Sapo' },
]

export default function StatsBar({
  stats, ineStats, onScrape, scraping,
  areas, currentArea, onChangeArea,
  selectedScraper, onSelectScraper,
  onOpenAdmin,
}) {
  const { lang, toggle, t } = useLanguage()
  if (!stats) return null

  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  return (
    <div className="flex items-center gap-6 px-4 py-2 bg-white border-b border-gray-200 text-sm shrink-0">
      <span className="font-semibold text-gray-800">🏠 {t.appTitle}</span>
      {areas && Object.keys(areas).length > 0 && (
        <select
          value={currentArea}
          onChange={e => onChangeArea(e.target.value)}
          className="px-2 py-1 text-xs border border-gray-300 rounded bg-white text-gray-700 cursor-pointer"
        >
          {Object.entries(areas).map(([key, cfg]) => (
            <option key={key} value={key}>{cfg.name}</option>
          ))}
        </select>
      )}
      <div className="flex gap-4 text-gray-600">
        <span><b className="text-gray-900">{fmt(stats.total_listings)}</b> {t.listings}</span>
        <span><b className="text-gray-900">€{fmt(stats.median_price_eur)}</b> {t.medianAsk}</span>
        <span><b className="text-gray-900">€{fmt(stats.median_price_per_sqm)}</b>{t.perSqmAsk}</span>
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

      {/* Language toggle */}
      <div className="flex rounded overflow-hidden border border-gray-200 text-xs ml-auto">
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

      {/* Admin entry point. Full picker, history, and per-source controls live in the admin page. */}
      <div className="flex items-center gap-2">
        <button
          onClick={onOpenAdmin}
          aria-label="Open admin"
          title="Admin"
          className="px-2 py-1 text-xs border border-gray-300 rounded bg-white text-gray-700 hover:bg-gray-50 cursor-pointer"
        >
          ⚙️ Admin
        </button>
      </div>
    </div>
  )
}
