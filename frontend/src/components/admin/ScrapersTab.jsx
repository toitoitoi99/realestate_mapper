import { useEffect, useState, useCallback } from 'react'
import { useLanguage } from '../../LanguageContext'
import { fetchScrapeRuns, triggerScore } from '../../api'
import { SCRAPER_OPTIONS } from '../StatsBar'

function StatusPill({ status }) {
  if (!status) return <span className="text-xs text-gray-400">idle</span>
  if (status === 'running')   return <span className="text-xs text-amber-700">🟡 running</span>
  if (status === 'completed') return <span className="text-xs text-emerald-700">🟢 completed</span>
  if (status === 'failed')    return <span className="text-xs text-red-700">🔴 failed</span>
  return <span className="text-xs text-gray-500">{status}</span>
}

function fmtDate(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function durationMs(startIso, endIso) {
  if (!startIso || !endIso) return null
  try { return new Date(endIso) - new Date(startIso) } catch { return null }
}

function fmtDuration(ms) {
  if (ms == null) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${s % 60}s`
}

function ScraperCard({ source, label, selected, onSelect, running, onRun, latest }) {
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchScrapeRuns({ source, limit: 10 })
      .then(d => { if (!cancelled) setHistory(d.runs ?? []) })
      .catch(() => { if (!cancelled) setHistory([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [source, latest])

  const lastRun = history[0]

  return (
    <div className={`bg-white rounded border ${selected ? 'border-primary' : 'border-gray-200'} p-4`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <input
            type="radio"
            name="selected-scraper"
            checked={selected}
            onChange={() => onSelect(source)}
            className="cursor-pointer"
          />
          <h3 className="font-semibold text-gray-800">{label}</h3>
        </div>
        <button
          onClick={() => onRun(source)}
          disabled={running}
          className="px-3 py-1 text-xs bg-primary text-white rounded hover:bg-primary-hover disabled:opacity-50 cursor-pointer disabled:cursor-not-allowed"
        >
          {running ? 'Running…' : 'Run now'}
        </button>
      </div>

      <div className="flex items-center gap-4 text-xs text-gray-600 mb-3">
        <StatusPill status={lastRun?.status} />
        {lastRun?.started_at && <span>started {fmtDate(lastRun.started_at)}</span>}
        {lastRun?.listings_new != null && <span><b>{lastRun.listings_new}</b> new</span>}
        {lastRun?.listings_found != null && <span><b>{lastRun.listings_found}</b> found</span>}
        {lastRun?.errors ? <span className="text-red-600"><b>{lastRun.errors}</b> errors</span> : null}
      </div>

      <details>
        <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700">
          {loading ? 'Loading history…' : `Last ${history.length} run${history.length === 1 ? '' : 's'}`}
        </summary>
        {history.length > 0 && (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-200">
                  <th className="py-1 pr-2">Started</th>
                  <th className="py-1 pr-2">Status</th>
                  <th className="py-1 pr-2">Duration</th>
                  <th className="py-1 pr-2 text-right">Found</th>
                  <th className="py-1 pr-2 text-right">New</th>
                  <th className="py-1 pr-2 text-right">Errors</th>
                  <th className="py-1 pr-2">Notes</th>
                </tr>
              </thead>
              <tbody>
                {history.map(r => (
                  <tr key={r.id} className="border-b border-gray-100">
                    <td className="py-1 pr-2 whitespace-nowrap">{fmtDate(r.started_at)}</td>
                    <td className="py-1 pr-2"><StatusPill status={r.status} /></td>
                    <td className="py-1 pr-2">{fmtDuration(durationMs(r.started_at, r.completed_at))}</td>
                    <td className="py-1 pr-2 text-right">{r.listings_found ?? '—'}</td>
                    <td className="py-1 pr-2 text-right">{r.listings_new ?? '—'}</td>
                    <td className="py-1 pr-2 text-right">{r.errors ?? 0}</td>
                    <td className="py-1 pr-2 text-gray-500 truncate max-w-[300px]" title={r.notes || ''}>
                      {r.notes || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </div>
  )
}

function ScorerCard() {
  const [scoring, setScoring] = useState(false)
  const [history, setHistory] = useState([])
  const [tick, setTick] = useState(0)

  const loadHistory = useCallback(() => {
    fetchScrapeRuns({ source: 'scorer', limit: 5 })
      .then(d => setHistory(d.runs ?? []))
      .catch(() => {})
  }, [])

  useEffect(() => { loadHistory() }, [loadHistory, tick])

  // Poll while running
  useEffect(() => {
    if (!scoring) return
    const id = setInterval(() => setTick(t => t + 1), 3000)
    return () => clearInterval(id)
  }, [scoring])

  // Detect completion from history
  useEffect(() => {
    const latest = history[0]
    if (scoring && latest && (latest.status === 'completed' || latest.status === 'failed')) {
      setScoring(false)
    }
  }, [history, scoring])

  const handleRun = async () => {
    if (scoring) return
    setScoring(true)
    try {
      await triggerScore()
      setTick(t => t + 1)
    } catch (e) {
      console.error('Score trigger failed', e)
      setScoring(false)
    }
  }

  const latest = history[0]

  return (
    <div className="bg-white rounded border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="font-semibold text-gray-800">Score listings</h3>
          <p className="text-xs text-gray-500 mt-0.5">Rarity + flip/rent signals for unscored &amp; stale listings</p>
        </div>
        <button
          onClick={handleRun}
          disabled={scoring}
          className="px-3 py-1 text-xs bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 cursor-pointer disabled:cursor-not-allowed"
        >
          {scoring ? 'Running…' : 'Run now'}
        </button>
      </div>

      <div className="flex items-center gap-4 text-xs text-gray-600 mb-3">
        <StatusPill status={latest?.status} />
        {latest?.started_at && <span>last run {fmtDate(latest.started_at)}</span>}
        {latest?.notes && <span className="text-gray-500 truncate max-w-[240px]" title={latest.notes}>{latest.notes}</span>}
      </div>

      <details>
        <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-700">
          Last {history.length} run{history.length === 1 ? '' : 's'}
        </summary>
        {history.length > 0 && (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-200">
                  <th className="py-1 pr-2">Started</th>
                  <th className="py-1 pr-2">Status</th>
                  <th className="py-1 pr-2">Duration</th>
                  <th className="py-1 pr-2">Notes</th>
                </tr>
              </thead>
              <tbody>
                {history.map(r => (
                  <tr key={r.id} className="border-b border-gray-100">
                    <td className="py-1 pr-2 whitespace-nowrap">{fmtDate(r.started_at)}</td>
                    <td className="py-1 pr-2"><StatusPill status={r.status} /></td>
                    <td className="py-1 pr-2">{fmtDuration(durationMs(r.started_at, r.completed_at))}</td>
                    <td className="py-1 pr-2 text-gray-500 truncate max-w-[300px]" title={r.notes || ''}>{r.notes || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </div>
  )
}

export default function ScrapersTab({ selectedScraper, onSelectScraper, scraping, scrapeStatus, onScrape }) {
  const { t } = useLanguage()

  const handleRun = (source) => {
    if (source !== selectedScraper) onSelectScraper(source)
    onScrape()
  }

  return (
    <div className="p-4 space-y-3">
      <div className="text-xs text-gray-500">
        Trigger scrapers and review recent run history. The selected source is what the shared
        trigger runs; switching here updates the global selection.
      </div>

      {scrapeStatus && (
        <div className="bg-white rounded border border-gray-200 p-3 text-xs">
          <b className="text-gray-700">Current status</b>
          <div className="mt-1 flex items-center gap-3">
            <StatusPill status={scrapeStatus.status} />
            <span className="text-gray-600">{scrapeStatus.source}</span>
            {scrapeStatus.listings_found != null && (
              <span className="text-gray-500">{scrapeStatus.listings_found} {t.statusFound}</span>
            )}
            {scrapeStatus.notes && (
              <span className="text-gray-500 truncate">{scrapeStatus.notes}</span>
            )}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {SCRAPER_OPTIONS.map(opt => (
          <ScraperCard
            key={opt.key}
            source={opt.key}
            label={opt.label}
            selected={selectedScraper === opt.key}
            onSelect={onSelectScraper}
            running={scraping && selectedScraper === opt.key}
            onRun={handleRun}
            latest={selectedScraper === opt.key ? scrapeStatus : null}
          />
        ))}
      </div>

      <ScorerCard />
    </div>
  )
}
