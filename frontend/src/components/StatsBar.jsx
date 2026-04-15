import { useLanguage } from '../LanguageContext'

export const SCRAPER_OPTIONS = [
  { key: 'idealista',  label: 'Idealista' },
  { key: 'era',        label: 'ERA' },
  { key: 'remax',      label: 'Remax' },
  { key: 'imovirtual', label: 'Imovirtual' },
  { key: 'olx',        label: 'OLX' },
  { key: 'casa_sapo',  label: 'Casa Sapo' },
]

const SCRAPER_LABEL = Object.fromEntries(SCRAPER_OPTIONS.map(s => [s.key, s.label]))

function ScrapeStatus({ status, t }) {
  if (!status) return null
  const label = SCRAPER_LABEL[status.source] ?? status.source
  let dot = '', text = '', cls = 'text-gray-600'
  if (status.status === 'running') {
    dot = '🟡'
    cls = 'text-amber-700'
    const found = status.listings_found ?? 0
    text = `${t.statusRunning} · ${label}${found ? ` · ${found} ${t.statusFound}` : ''}`
  } else if (status.status === 'completed') {
    dot = '🟢'
    cls = 'text-emerald-700'
    const nw = status.listings_new ?? 0
    const er = status.errors ?? 0
    text = `${label} · ${nw} ${t.statusNew}${er ? ` · ${er} ${t.statusErrors}` : ''}`
  } else if (status.status === 'failed') {
    dot = '🔴'
    cls = 'text-red-700'
    text = `${t.statusFailed} · ${label}`
  } else {
    return null
  }
  return (
    <span
      className={`text-xs ${cls} whitespace-nowrap`}
      title={status.notes || `${status.status} (${status.started_at ?? ''})`}
    >
      {dot} {text}
    </span>
  )
}

export default function StatsBar({
  stats, ineStats, onScrape, scraping,
  areas, currentArea, onChangeArea,
  selectedScraper, onSelectScraper, scrapeStatus,
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

      {/* Scraper picker + status + trigger */}
      <div className="flex items-center gap-2">
        <ScrapeStatus status={scrapeStatus} t={t} />
        <select
          value={selectedScraper}
          onChange={e => onSelectScraper(e.target.value)}
          disabled={scraping}
          aria-label={t.scraperPickerAria}
          className="px-2 py-1 text-xs border border-gray-300 rounded bg-white text-gray-700 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {SCRAPER_OPTIONS.map(s => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>
        <button
          onClick={onScrape}
          disabled={scraping}
          className="px-3 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 cursor-pointer disabled:cursor-not-allowed"
        >
          {scraping ? t.scraping : t.runScraper}
        </button>
      </div>
    </div>
  )
}
