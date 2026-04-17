import { useEffect, useState } from 'react'
import { fetchAdminHealth } from '../../api'

function Section({ title, hint, children, right }) {
  return (
    <div className="bg-white rounded border border-gray-200 p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold text-gray-800 text-sm">{title}</h3>
          {hint && <p className="text-xs text-gray-500 mt-0.5">{hint}</p>}
        </div>
        {right}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  )
}

function fmtBytes(n) {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function fmtDate(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function fmtRelative(iso) {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  if (ms < 0) return fmtDate(iso)
  const m = Math.round(ms / 60000)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 48) return `${h}h ago`
  const d = Math.round(h / 24)
  return `${d}d ago`
}

function fmtPrice(n) {
  if (n == null) return '—'
  return '€' + Math.round(n).toLocaleString('pt-PT')
}

function fmtInt(n) {
  if (n == null) return '—'
  return Math.round(n).toLocaleString('pt-PT')
}

export default function DataHealthTab() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    setError(null)
    fetchAdminHealth().then(d => setData(d)).catch(e => setError(String(e?.message ?? e))).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  if (loading && !data) return <div className="p-4 text-sm text-gray-500">Loading health snapshot…</div>
  if (error && !data)   return <div className="p-4 text-sm text-red-600">{error} <button onClick={load} className="underline ml-2">Retry</button></div>
  if (!data) return null

  const dbTotal = (data.db.size_bytes || 0) + (data.db.wal_bytes || 0) + (data.db.shm_bytes || 0)

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-xs text-gray-500">
          Snapshot generated {fmtDate(data.generated_at)} · <code className="font-mono">{data.db.path}</code>
        </div>
        <button
          onClick={load}
          className="px-2 py-1 text-xs border border-gray-300 rounded bg-white text-gray-700 hover:bg-gray-50 cursor-pointer"
        >
          Refresh
        </button>
      </div>

      <Section title="Database file" hint="Main file + WAL + SHM sidecars.">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          <div><div className="text-xs text-gray-500">Main</div><div className="font-mono">{fmtBytes(data.db.size_bytes)}</div></div>
          <div><div className="text-xs text-gray-500">WAL</div><div className="font-mono">{fmtBytes(data.db.wal_bytes)}</div></div>
          <div><div className="text-xs text-gray-500">SHM</div><div className="font-mono">{fmtBytes(data.db.shm_bytes)}</div></div>
          <div><div className="text-xs text-gray-500">Total</div><div className="font-mono font-semibold">{fmtBytes(dbTotal)}</div></div>
        </div>
      </Section>

      <Section title="Row counts">
        <table className="w-full text-sm">
          <tbody>
            {Object.entries(data.tables).map(([t, c]) => (
              <tr key={t} className="border-b border-gray-50 last:border-0">
                <td className="py-1 pr-2 font-mono text-gray-700 text-xs">{t}</td>
                <td className="py-1 text-right font-mono tabular-nums">{fmtInt(c)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Section title="Last scraped · sales" hint="Most recent scraped_at per source.">
          <SourceTable rows={data.last_updated.sales_by_source} />
        </Section>
        <Section title="Last scraped · rentals" hint="Most recent scraped_at per source.">
          <SourceTable rows={data.last_updated.rentals_by_source} />
        </Section>
      </div>

      <Section title="Other data sources">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
          <Kv label="Construction projects" value={data.last_updated.construction_projects} relative />
          <Kv label="Security POIs"         value={data.last_updated.security_pois}         relative />
          <Kv
            label="INE stats"
            value={
              data.last_updated.ine_stats
                ? `${data.last_updated.ine_stats.period ?? '—'} · ${fmtRelative(data.last_updated.ine_stats.fetched_at)}`
                : '—'
            }
          />
        </div>
      </Section>

      <Section title="Stale active listings" hint="Active listings whose scraped_at is older than N days — likely sold/removed.">
        <div className="grid grid-cols-2 gap-4">
          <StaleBlock title="Sales"   stale={data.stale.sales}   />
          <StaleBlock title="Rentals" stale={data.stale.rentals} />
        </div>
      </Section>

      <Section title="Recent price changes" hint="Latest 25 price_amount mutations in listing_history.">
        {data.recent_price_changes.length === 0
          ? <p className="text-xs text-gray-400 italic">No tracked price changes yet.</p>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-gray-200">
                    <th className="py-1 pr-2">When</th>
                    <th className="py-1 pr-2">Listing</th>
                    <th className="py-1 pr-2">Source</th>
                    <th className="py-1 pr-2 text-right">Old</th>
                    <th className="py-1 pr-2 text-right">New</th>
                    <th className="py-1 text-right">Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_price_changes.map((r, i) => {
                    const dir = r.delta == null ? '' : r.delta < 0 ? 'text-emerald-700' : r.delta > 0 ? 'text-red-700' : 'text-gray-500'
                    return (
                      <tr key={i} className="border-b border-gray-50 last:border-0">
                        <td className="py-1 pr-2 whitespace-nowrap" title={r.changed_at}>{fmtRelative(r.changed_at)}</td>
                        <td className="py-1 pr-2 font-mono">{r.listing_type}#{r.listing_id}</td>
                        <td className="py-1 pr-2 uppercase text-gray-500">{r.source || '—'}</td>
                        <td className="py-1 pr-2 text-right tabular-nums">{fmtPrice(r.old_value_num)}</td>
                        <td className="py-1 pr-2 text-right tabular-nums">{fmtPrice(r.new_value_num)}</td>
                        <td className={`py-1 text-right tabular-nums ${dir}`}>
                          {r.delta == null ? '—' : (r.delta > 0 ? '+' : '') + fmtPrice(r.delta).replace('€', '€')}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )
        }
      </Section>

      <Section title="Recent sold transactions" hint="Latest 25 detected sold properties.">
        {data.recent_sold.length === 0
          ? <p className="text-xs text-gray-400 italic">No sold transactions yet.</p>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-gray-200">
                    <th className="py-1 pr-2">Sold</th>
                    <th className="py-1 pr-2">Parish</th>
                    <th className="py-1 pr-2">Type</th>
                    <th className="py-1 pr-2 text-right">Price</th>
                    <th className="py-1 pr-2 text-right">€/m²</th>
                    <th className="py-1 pr-2 text-right">m²</th>
                    <th className="py-1 text-right">Rooms</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_sold.map(r => (
                    <tr key={r.id} className="border-b border-gray-50 last:border-0">
                      <td className="py-1 pr-2 whitespace-nowrap" title={r.created_at}>{r.sold_date || '—'}</td>
                      <td className="py-1 pr-2">{r.parish || '—'}</td>
                      <td className="py-1 pr-2 text-gray-500">{r.property_type || '—'}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{fmtPrice(r.price_amount)}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{r.price_per_sqm ? `€${Math.round(r.price_per_sqm).toLocaleString('pt-PT')}` : '—'}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{r.size_sqm ?? '—'}</td>
                      <td className="py-1 text-right">{r.rooms != null ? `T${r.rooms}` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Section>
    </div>
  )
}

function SourceTable({ rows }) {
  if (!rows?.length) return <p className="text-xs text-gray-400 italic">No data.</p>
  return (
    <table className="w-full text-xs">
      <tbody>
        {rows.map(r => (
          <tr key={r.source} className="border-b border-gray-50 last:border-0">
            <td className="py-1 pr-2 uppercase text-gray-700 font-mono">{r.source}</td>
            <td className="py-1 pr-2 text-gray-500" title={r.last_at}>{fmtRelative(r.last_at)}</td>
            <td className="py-1 text-right tabular-nums text-gray-600">{fmtInt(r.count)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Kv({ label, value, relative }) {
  const display = relative ? fmtRelative(value) : value ?? '—'
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-sm" title={typeof value === 'string' ? value : ''}>{display}</div>
    </div>
  )
}

function StaleBlock({ title, stale }) {
  return (
    <div>
      <div className="text-xs font-semibold text-gray-600 mb-2">{title}</div>
      <div className="grid grid-cols-3 gap-2">
        {[['gt_7d','>7d'], ['gt_14d','>14d'], ['gt_30d','>30d']].map(([k, l]) => (
          <div key={k} className="bg-gray-50 rounded px-2 py-1.5 text-center">
            <div className="text-[11px] text-gray-500">{l}</div>
            <div className="font-mono tabular-nums text-gray-800">{fmtInt(stale[k])}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
