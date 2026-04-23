import { useEffect, useState } from 'react'
import { fetchAdminTuning, saveScoreBands, resetScoreBands, fetchListingDetail } from '../../api'
import { useScoreBands } from '../../ScoreBandsContext'
import { useAuth } from '../../contexts/AuthContext'
import { supabase } from '../../lib/supabase'
import ListingRefCard from '../ListingRefCard'

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

function ReadOnlyBadge() {
  return (
    <span
      title="Editing this requires re-scoring existing listings; not writable yet."
      className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 border border-gray-200"
    >
      read-only (requires rescore)
    </span>
  )
}

function ScoreBandsEditor({ current, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ A: current.A.min, B: current.B.min, C: current.C.min })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const start = () => {
    setDraft({ A: current.A.min, B: current.B.min, C: current.C.min })
    setError(null)
    setEditing(true)
  }
  const cancel = () => { setEditing(false); setError(null) }

  const validate = () => {
    const { A, B, C } = draft
    if ([A, B, C].some(v => !Number.isFinite(v) || v < 0 || v > 100)) return 'Values must be 0–100'
    if (!(A > B && B > C && C >= 0)) return 'Must satisfy A > B > C ≥ 0'
    return null
  }

  const save = async () => {
    const err = validate()
    if (err) { setError(err); return }
    setSaving(true); setError(null)
    try {
      await saveScoreBands({
        A: { min: Number(draft.A) },
        B: { min: Number(draft.B) },
        C: { min: Number(draft.C) },
      })
      setEditing(false)
      onSaved?.()
    } catch (e) {
      setError(String(e?.message ?? e))
    } finally { setSaving(false) }
  }

  const reset = async () => {
    if (!confirm('Reset score bands to defaults (A=60, B=45, C=30)?')) return
    setSaving(true); setError(null)
    try {
      await resetScoreBands()
      setEditing(false)
      onSaved?.()
    } catch (e) {
      setError(String(e?.message ?? e))
    } finally { setSaving(false) }
  }

  if (!editing) {
    return (
      <button
        onClick={start}
        className="px-2 py-1 text-xs border border-gray-300 rounded bg-white text-gray-700 hover:bg-gray-50 cursor-pointer"
      >
        Edit
      </button>
    )
  }
  return (
    <div className="flex items-center gap-1.5">
      {['A', 'B', 'C'].map(L => (
        <label key={L} className="flex items-center gap-1 text-xs text-gray-600">
          {L} ≥
          <input
            type="number"
            min={0}
            max={100}
            value={draft[L]}
            onChange={e => setDraft(d => ({ ...d, [L]: Number(e.target.value) }))}
            className="w-14 px-1.5 py-0.5 border border-gray-300 rounded text-sm tabular-nums"
          />
        </label>
      ))}
      <button
        onClick={save}
        disabled={saving}
        className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 cursor-pointer"
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
      <button
        onClick={cancel}
        disabled={saving}
        className="px-2 py-1 text-xs border border-gray-300 rounded bg-white text-gray-700 hover:bg-gray-50 cursor-pointer"
      >
        Cancel
      </button>
      <button
        onClick={reset}
        disabled={saving}
        title="Reset to defaults"
        className="px-2 py-1 text-xs border border-gray-300 rounded bg-white text-gray-500 hover:bg-red-50 hover:text-red-600 cursor-pointer"
      >
        Reset
      </button>
      {error && <span className="text-xs text-red-600 ml-2">{error}</span>}
    </div>
  )
}

function WeightBar({ value, max = 0.5 }) {
  const pct = Math.min(100, Math.round((value / max) * 100))
  return (
    <div className="h-1.5 w-24 bg-gray-100 rounded overflow-hidden">
      <div className="h-full bg-blue-500" style={{ width: `${pct}%` }} />
    </div>
  )
}

function WeightTable({ weights }) {
  const entries = Object.entries(weights).sort((a, b) => b[1] - a[1])
  const max = Math.max(...entries.map(([, v]) => v), 0.01)
  return (
    <table className="w-full text-xs">
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k} className="border-b border-gray-50 last:border-0">
            <td className="py-1 pr-2 text-gray-700 font-mono">{k}</td>
            <td className="py-1 pr-2 w-24"><WeightBar value={v} max={max} /></td>
            <td className="py-1 text-right font-mono text-gray-800 tabular-nums">{v.toFixed(2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ScoreBands({ bands }) {
  const colors = {
    A: 'bg-emerald-100 text-emerald-800 border-emerald-300',
    B: 'bg-lime-100 text-lime-800 border-lime-300',
    C: 'bg-amber-100 text-amber-800 border-amber-300',
    D: 'bg-red-100 text-red-800 border-red-300',
  }
  const order = ['A', 'B', 'C', 'D']
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
      {order.map((letter, i) => {
        const b = bands[letter]
        if (!b) return null
        const prev = order[i - 1]
        const range = i === 0
          ? `≥ ${b.min}`
          : letter === 'D'
            ? `< ${bands['C'].min}`
            : `${b.min} – ${bands[prev].min - 1}`
        return (
          <div key={letter} className={`rounded border px-3 py-2 ${colors[letter]}`}>
            <div className="font-bold text-lg">{letter}</div>
            <div className="text-xs font-mono">{range}</div>
            <div className="text-[11px] opacity-75 mt-1">{b.label}</div>
          </div>
        )
      })}
    </div>
  )
}

function Profile({ name, profile }) {
  return (
    <details className="bg-gray-50 rounded border border-gray-200" open={name === 'urban_dense'}>
      <summary className="px-3 py-2 cursor-pointer text-sm font-semibold text-gray-800 hover:bg-gray-100">
        <span className="font-mono text-xs bg-white rounded px-1.5 py-0.5 border border-gray-200 mr-2">{name}</span>
        <span className="text-xs text-gray-500 font-normal">{profile.description}</span>
      </summary>
      <div className="px-3 pb-3 grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <h4 className="text-xs font-semibold text-gray-600 mb-1">Flip weights</h4>
          <WeightTable weights={profile.flip_weights} />
        </div>
        <div>
          <h4 className="text-xs font-semibold text-gray-600 mb-1">Rent weights</h4>
          <WeightTable weights={profile.rent_weights} />
        </div>
        <div>
          <h4 className="text-xs font-semibold text-gray-600 mb-1">Blocker weights (flip)</h4>
          <WeightTable weights={profile.blocker_weights_flip} />
        </div>
        <div>
          <h4 className="text-xs font-semibold text-gray-600 mb-1">Blocker weights (rent)</h4>
          <WeightTable weights={profile.blocker_weights_rent} />
        </div>
        <div className="md:col-span-2 flex gap-4 text-xs text-gray-600 pt-2 border-t border-gray-200">
          <span><b>default_reno_tier:</b> <code className="font-mono">{profile.default_reno_tier}</code></span>
          <span><b>reno_cost_multiplier:</b> <code className="font-mono">{profile.reno_cost_multiplier.toFixed(2)}×</code></span>
        </div>
      </div>
    </details>
  )
}

function DisagreementsPanel({ onViewListing }) {
  const { user } = useAuth()
  const [ratings, setRatings] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = async () => {
    if (!user || !supabase) { setRatings([]); setLoading(false); return }
    setLoading(true); setError(null)
    try {
      const { data, error: err } = await supabase
        .from('listing_ratings')
        .select('listing_kind, listing_id, persona, agree, comment, updated_at, created_at')
        .eq('user_id', user.id)
        .order('updated_at', { ascending: false })
      if (err) throw err
      const enriched = await Promise.all((data ?? []).map(async r => {
        try {
          const d = await fetchListingDetail(r.listing_id, r.listing_kind === 'rent' ? 'rent' : 'sale')
          return { ...r, listing: d.listing ?? null }
        } catch { return { ...r, listing: null } }
      }))
      setRatings(enriched)
    } catch (e) {
      setError(String(e?.message ?? e))
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { load() }, [user?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleClear = async (item) => {
    if (!user || !supabase) return
    const prev = ratings
    setRatings(ratings.filter(r => !(
      r.listing_kind === item.listing_kind &&
      r.listing_id === item.listing_id &&
      r.persona === item.persona
    )))
    const { error: err } = await supabase.from('listing_ratings')
      .delete()
      .eq('user_id', user.id)
      .eq('listing_kind', item.listing_kind)
      .eq('listing_id', item.listing_id)
      .eq('persona', item.persona)
    if (err) { setRatings(prev); setError(`Failed to clear: ${err.message}`) }
  }

  const grouped = {
    flip: { agree: [], disagree: [] },
    rent: { agree: [], disagree: [] },
  }
  for (const r of ratings) {
    if (grouped[r.persona]) grouped[r.persona][r.agree]?.push(r)
  }
  const sortByDate = (arr) => [...arr].sort((a, b) =>
    new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0)
  )

  const totals = {
    flip: { agree: grouped.flip.agree.length, disagree: grouped.flip.disagree.length },
    rent: { agree: grouped.rent.agree.length, disagree: grouped.rent.disagree.length },
  }

  if (loading) return <div className="text-xs text-gray-500">Loading ratings…</div>
  if (error) return <div className="text-xs text-red-600">{error}</div>
  if (ratings.length === 0) {
    return (
      <div className="text-xs text-gray-500">
        No per-persona ratings yet. Use the <b>Calibrate</b> tab to rate listings.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-4 text-xs">
        {['flip', 'rent'].map(p => (
          <div key={p} className="bg-white border border-gray-200 rounded px-3 py-1.5">
            <span className="font-semibold uppercase tracking-wider text-gray-700">{p}</span>{' '}
            <span className="text-green-700">{totals[p].agree} ✓</span>{' · '}
            <span className="text-red-700">{totals[p].disagree} ✗</span>
          </div>
        ))}
      </div>
      {['flip', 'rent'].map(p => {
        const disagrees = sortByDate(grouped[p].disagree)
        if (!disagrees.length) return null
        return (
          <div key={p}>
            <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">
              {p} — disagreements ({disagrees.length})
            </h4>
            <div className="space-y-2">
              {disagrees.map(item => (
                <ListingRefCard
                  key={`${item.persona}-${item.listing_kind}-${item.listing_id}`}
                  listing={item.listing}
                  listingKind={item.listing_kind}
                  comment={item.comment}
                  updatedAt={item.updated_at || item.created_at}
                  commentPrefix="✗"
                  topBadges={[{
                    text: `${p} score wrong`,
                    className: 'bg-red-100 text-red-700',
                  }]}
                  onViewOnMap={item.listing && onViewListing ? onViewListing : null}
                  actions={
                    <button
                      onClick={() => handleClear(item)}
                      className="px-2 py-1 text-xs border border-gray-300 rounded text-gray-500 hover:bg-red-50 hover:text-red-600"
                    >Clear</button>
                  }
                />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function TuningTab({ onViewListing }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const { refresh: refreshBands } = useScoreBands()

  const load = () => fetchAdminTuning().then(setData).catch(e => setError(String(e?.message ?? e)))
  useEffect(() => { load() }, [])

  const handleBandsSaved = async () => {
    await load()          // refresh tuning snapshot
    await refreshBands()  // refresh global bands so map/scorecard re-render
  }

  if (error) return <div className="p-4 text-sm text-red-600">Failed to load tuning: {error}</div>
  if (!data) return <div className="p-4 text-sm text-gray-500">Loading tuning…</div>

  return (
    <div className="p-4 space-y-4">
      <div className="text-xs text-gray-500">
        Read-only snapshot of scoring/tuning constants as the backend currently runs.
        Editing is planned for v2 (persisted in an <code className="font-mono">app_settings</code> table).
      </div>

      <Section
        title="Score bands"
        hint="A/B/C/D thresholds applied to flip_score and rent_score. Live — edits affect every listing badge immediately."
        right={<ScoreBandsEditor current={data.score_bands} onSaved={handleBandsSaved} />}
      >
        <ScoreBands bands={data.score_bands} />
      </Section>

      <Section
        title="Disagreements"
        hint="Listings where you marked the model's score wrong (per persona). Use these to spot patterns before re-weighting."
      >
        <DisagreementsPanel onViewListing={onViewListing} />
      </Section>

      <Section
        title="Rarity score weights"
        hint="Computed in database.py when aggregating neighborhood stats."
        right={<ReadOnlyBadge />}
      >
        <div className="max-w-md">
          <WeightTable weights={data.rarity_weights} />
        </div>
      </Section>

      <Section
        title="Log1p transform"
        hint={data.log1p_scale.description}
        right={<ReadOnlyBadge />}
      >
        <div className="text-xs text-gray-700 space-y-1">
          <div><b>k:</b> <code className="font-mono">{data.log1p_scale.k}</code></div>
          <div>
            <b>applied to:</b>{' '}
            {data.log1p_scale.signals.map(s => (
              <code key={s} className="font-mono bg-gray-100 rounded px-1.5 py-0.5 mr-1">{s}</code>
            ))}
          </div>
          <div><b>blocker penalty scale:</b> <code className="font-mono">{data.blocker_penalty_scale}</code> <span className="text-gray-500">(worst blocker = −{Math.round(data.blocker_penalty_scale * 100)} pts)</span></div>
        </div>
      </Section>

      <Section
        title="Region profiles"
        hint="Flip/rent/blocker weights per region profile. Profile assigned per listing by parish or municipality."
        right={<ReadOnlyBadge />}
      >
        <div className="space-y-2">
          {Object.entries(data.profiles).map(([name, profile]) => (
            <Profile key={name} name={name} profile={profile} />
          ))}
        </div>
      </Section>

      <Section
        title="Renovation cost tiers"
        hint="Base €/m² by scope of work. Multiplied by profile's reno_cost_multiplier at scoring time."
        right={<ReadOnlyBadge />}
      >
        <table className="w-full max-w-md text-xs">
          <tbody>
            {Object.entries(data.reno_cost_tiers).map(([tier, cost]) => (
              <tr key={tier} className="border-b border-gray-50 last:border-0">
                <td className="py-1 pr-2 font-mono text-gray-700">{tier}</td>
                <td className="py-1 text-right font-mono text-gray-800 tabular-nums">€{cost.toLocaleString('pt-PT')}/m²</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Scrapers" hint="Operational gates for scheduled runs.">
        <div className="text-xs text-gray-700">
          <b>Leg cooldown:</b> <code className="font-mono">{data.scraper_cooldown_sec}s</code>{' '}
          <span className="text-gray-500">({Math.round(data.scraper_cooldown_sec / 60)} min between sale and rent legs)</span>
        </div>
      </Section>
    </div>
  )
}
