import { useEffect, useRef, useState, useCallback } from 'react'
import { useAuth } from '../../contexts/AuthContext'
import { supabase } from '../../lib/supabase'
import { fetchListings } from '../../api'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const GRADE = { A: 'bg-emerald-100 text-emerald-800', B: 'bg-primary-tint text-primary', C: 'bg-amber-100 text-amber-800', D: 'bg-red-100 text-red-800' }

function PhotoCarousel({ images }) {
  const [idx, setIdx] = useState(0)
  const urls = (() => { try { return images ? JSON.parse(images) : [] } catch { return [] } })()
  useEffect(() => setIdx(0), [images])
  if (!urls.length) return (
    <div className="w-full h-52 bg-gray-100 grid place-items-center text-gray-400 text-sm rounded-t-lg">No photos</div>
  )
  return (
    <div className="relative w-full h-52 bg-gray-100 overflow-hidden rounded-t-lg">
      <img src={urls[idx]} alt="" className="w-full h-full object-cover" loading="lazy" />
      {urls.length > 1 && (
        <>
          <button onClick={() => setIdx(i => (i - 1 + urls.length) % urls.length)}
            className="absolute left-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/40 hover:bg-black/60 text-white flex items-center justify-center text-lg leading-none">‹</button>
          <button onClick={() => setIdx(i => (i + 1) % urls.length)}
            className="absolute right-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/40 hover:bg-black/60 text-white flex items-center justify-center text-lg leading-none">›</button>
          <div className="absolute bottom-2 right-3 bg-black/40 text-white text-[10px] px-1.5 py-0.5 rounded-full">
            {idx + 1} / {urls.length}
          </div>
        </>
      )}
    </div>
  )
}

function ListingMiniMap({ lat, lon }) {
  const ref = useRef(null)
  const mapRef = useRef(null)
  useEffect(() => {
    if (!ref.current || !lat || !lon) return
    if (mapRef.current) { mapRef.current.remove(); mapRef.current = null }
    const map = L.map(ref.current, { zoomControl: false, attributionControl: false, scrollWheelZoom: false, dragging: false })
    map.setView([lat, lon], 14)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png').addTo(map)
    L.circleMarker([lat, lon], { radius: 8, color: '#1d4ed8', fillColor: '#3b82f6', fillOpacity: 0.9, weight: 2 }).addTo(map)
    mapRef.current = map
    setTimeout(() => map.invalidateSize(), 50)
    return () => { map.remove(); mapRef.current = null }
  }, [lat, lon])
  if (!lat || !lon) return (
    <div className="w-full h-24 bg-gray-50 grid place-items-center text-xs text-gray-400">No location data</div>
  )
  return <div ref={ref} className="w-full h-36" />
}

function gradeFor(score) {
  if (score == null) return null
  return score >= 60 ? 'A' : score >= 45 ? 'B' : score >= 30 ? 'C' : 'D'
}

function parseFactor(raw) {
  if (!raw) return null
  try { return typeof raw === 'string' ? JSON.parse(raw) : raw } catch { return null }
}

function SignalBreakdown({ factors }) {
  const f = parseFactor(factors)
  const bd = f?.weighted_breakdown
  if (!bd) return <p className="text-xs text-gray-400 italic">No signal breakdown</p>
  const entries = Object.entries(bd).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 6)
  return (
    <div className="space-y-0.5">
      {entries.map(([name, val]) => (
        <div key={name} className="flex justify-between text-xs">
          <span className="text-gray-500 capitalize truncate pr-2">{name.replace(/_/g, ' ')}</span>
          <span className={`font-mono shrink-0 ${val >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
            {val >= 0 ? '+' : ''}{val.toFixed(1)}
          </span>
        </div>
      ))}
      {f?.missing_signals?.length > 0 && (
        <p className="text-[10px] text-gray-400 mt-1">Missing: {f.missing_signals.join(', ')}</p>
      )}
    </div>
  )
}

function RatingWidget({ label, score, factors, existing, agree, onAgree, comment, onComment }) {
  const f = parseFactor(factors)
  const grade = f?.rating || gradeFor(score)
  return (
    <div className="flex-1 min-w-0 border border-gray-200 rounded-lg p-3 bg-white space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold text-gray-700">{label}</span>
        {grade ? (
          <span className={`text-xs px-2 py-0.5 rounded-full font-bold ${GRADE[grade]}`}>
            {grade} {score != null ? Math.round(score) : '—'}
          </span>
        ) : (
          <span className="text-xs text-gray-400 italic">unscored</span>
        )}
        {existing && (
          <span className={`ml-auto text-xs ${existing.agree === 'agree' ? 'text-emerald-600' : 'text-red-500'}`}>
            Previously: {existing.agree}
          </span>
        )}
      </div>

      <SignalBreakdown factors={factors} />

      <div className="flex gap-1.5">
        <button
          onClick={() => onAgree(agree === 'agree' ? null : 'agree')}
          className={`flex-1 py-1.5 text-xs rounded border cursor-pointer transition-colors ${
            agree === 'agree' ? 'bg-emerald-500 text-white border-emerald-500' : 'border-gray-300 text-gray-600 hover:border-emerald-400 hover:text-emerald-600'
          }`}
        >✓ Agree</button>
        <button
          onClick={() => onAgree(agree === 'disagree' ? null : 'disagree')}
          className={`flex-1 py-1.5 text-xs rounded border cursor-pointer transition-colors ${
            agree === 'disagree' ? 'bg-red-500 text-white border-red-500' : 'border-gray-300 text-gray-600 hover:border-red-400 hover:text-red-600'
          }`}
        >✗ Disagree</button>
      </div>

      <textarea
        value={comment}
        onChange={e => onComment(e.target.value)}
        placeholder={agree === 'disagree' ? 'Why? e.g. "no comps within 500m", "yield too optimistic"' : 'Optional note…'}
        rows={3}
        className="w-full text-xs border border-gray-200 rounded p-2 resize-none focus:outline-none focus:border-primary-border placeholder-gray-300"
      />
    </div>
  )
}

export default function CalibrateTab({ onViewListing }) {
  const { user } = useAuth()
  const [queue, setQueue] = useState('unreviewed')
  const [allListings, setAllListings] = useState([])
  const [userRatings, setUserRatings] = useState([])
  const [candidates, setCandidates] = useState([])
  const [idx, setIdx] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [sessionCount, setSessionCount] = useState(0)

  const [flipAgree, setFlipAgree] = useState(null)
  const [flipComment, setFlipComment] = useState('')
  const [rentAgree, setRentAgree] = useState(null)
  const [rentComment, setRentComment] = useState('')

  const buildQueue = useCallback((listings, ratings, mode) => {
    const ratedPairs = new Set(ratings.map(r => `${r.listing_kind}-${r.listing_id}-${r.persona}`))
    let pool
    if (mode === 'unreviewed') {
      // Keep listings where at least one of flip/rent is unrated
      pool = listings.filter(l => {
        const k = l.listing_type || 'sale'
        const flipRated = ratedPairs.has(`${k}-${l.id}-flip`)
        const rentRated = ratedPairs.has(`${k}-${l.id}-rent`)
        return !flipRated || !rentRated
      })
    } else {
      // disagreements: at least one disagree
      const disagreeIds = new Set(ratings.filter(r => r.agree === 'disagree').map(r => r.listing_id))
      pool = listings.filter(l => disagreeIds.has(l.id))
    }
    setCandidates([...pool].sort(() => Math.random() - 0.5))
    setIdx(0)
  }, [])

  // Initial load
  useEffect(() => {
    if (!user || !supabase) { setLoading(false); return }
    let cancelled = false
    setLoading(true)
    Promise.all([
      fetchListings({ listing_type: 'sale', limit: 2000 }),
      supabase.from('listing_ratings').select('*').eq('user_id', user.id),
    ]).then(([listData, { data: ratingsData }]) => {
      if (cancelled) return
      const scored = (listData.listings ?? []).filter(l => l.flip_score != null || l.rent_score != null)
      setAllListings(scored)
      setUserRatings(ratingsData ?? [])
      setLoaded(true)
    }).catch(console.error).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [user?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Rebuild queue when data or mode changes
  useEffect(() => {
    if (loaded) buildQueue(allListings, userRatings, queue)
  }, [loaded, queue, buildQueue]) // allListings and userRatings intentionally excluded — rebuilt only on load/queue change

  // Pre-fill draft when listing changes
  const current = candidates[idx] || null
  useEffect(() => {
    if (!current) return
    const kind = current.listing_type || 'sale'
    const flipR = userRatings.find(r => r.listing_id === current.id && r.listing_kind === kind && r.persona === 'flip')
    const rentR = userRatings.find(r => r.listing_id === current.id && r.listing_kind === kind && r.persona === 'rent')
    setFlipAgree(flipR?.agree ?? null)
    setFlipComment(flipR?.comment ?? '')
    setRentAgree(rentR?.agree ?? null)
    setRentComment(rentR?.comment ?? '')
  }, [current?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const saveRatings = useCallback(async () => {
    if (!user || !supabase || !current) return
    const kind = current.listing_type || 'sale'
    const rows = []
    if (flipAgree) rows.push({ user_id: user.id, listing_kind: kind, listing_id: current.id, persona: 'flip', agree: flipAgree, comment: flipComment || null, updated_at: new Date().toISOString() })
    if (rentAgree) rows.push({ user_id: user.id, listing_kind: kind, listing_id: current.id, persona: 'rent', agree: rentAgree, comment: rentComment || null, updated_at: new Date().toISOString() })
    if (!rows.length) return
    setSaving(true)
    const { error } = await supabase.from('listing_ratings')
      .upsert(rows, { onConflict: 'user_id,listing_kind,listing_id,persona' })
    if (error) { console.error('save rating failed', error) }
    else {
      setUserRatings(prev => {
        const filtered = prev.filter(r => !(r.listing_id === current.id && r.listing_kind === kind && rows.some(rr => rr.persona === r.persona)))
        return [...filtered, ...rows]
      })
      setSessionCount(c => c + 1)
    }
    setSaving(false)
  }, [user, current, flipAgree, flipComment, rentAgree, rentComment])

  const goNext = async (save = true) => {
    if (save) await saveRatings()
    setIdx(i => i + 1)
  }

  if (!user) return <div className="p-8 text-center text-sm text-gray-500">Sign in to calibrate scores.</div>
  if (loading) return <div className="p-8 text-center text-sm text-gray-500">Loading scored listings…</div>

  if (!current) return (
    <div className="p-8 text-center space-y-2">
      <p className="text-sm text-gray-600">{candidates.length === 0 ? 'No listings in this queue.' : 'Queue complete!'}</p>
      <p className="text-xs text-gray-400">{sessionCount} rated this session</p>
      <button onClick={() => { setLoaded(false); setLoading(true); /* re-trigger load */ setAllListings([]); setUserRatings([]) }}
        className="mt-2 px-4 py-2 text-xs bg-gray-800 text-white rounded cursor-pointer">
        Reload
      </button>
    </div>
  )

  const kind = current.listing_type || 'sale'
  const existingFlip = userRatings.find(r => r.listing_id === current.id && r.listing_kind === kind && r.persona === 'flip')
  const existingRent = userRatings.find(r => r.listing_id === current.id && r.listing_kind === kind && r.persona === 'rent')

  return (
    <div className="p-4 space-y-3 max-w-3xl">
      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex gap-1">
          {[['unreviewed', 'Unreviewed'], ['disagreements', 'Disagreements']].map(([m, label]) => (
            <button key={m} onClick={() => setQueue(m)}
              className={`px-3 py-1 text-xs rounded cursor-pointer ${queue === m ? 'bg-gray-800 text-white' : 'bg-white border border-gray-200 text-gray-600 hover:bg-gray-50'}`}>
              {label}
            </button>
          ))}
        </div>
        <span className="text-xs text-gray-400">
          {idx + 1} / {candidates.length} · {sessionCount} rated this session
        </span>
        <button onClick={() => setIdx(i => Math.max(i - 1, 0))} disabled={idx === 0}
          className="ml-auto px-2 py-1 text-xs border border-gray-200 rounded text-gray-500 disabled:opacity-30 cursor-pointer">
          ← Prev
        </button>
      </div>

      {/* Listing card */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <PhotoCarousel images={current.images} />
        <ListingMiniMap lat={current.lat} lon={current.lon} />
        <div className="p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-gray-800 truncate">
              {current.title || `Sale #${current.id}`}
            </p>
            <p className="text-xs text-gray-500 mt-0.5">
              {[current.neighborhood, current.parish, current.city].filter(Boolean).join(' · ')}
            </p>
          </div>
          <div className="text-right shrink-0">
            <p className="text-sm font-bold text-gray-900">
              {current.price_amount ? `€${current.price_amount.toLocaleString()}` : '—'}
            </p>
            <p className="text-xs text-gray-500">
              {[current.size_sqm && `${current.size_sqm} m²`, current.bedrooms && `${current.bedrooms}bd`, current.price_per_sqm && `€${Math.round(current.price_per_sqm)}/m²`].filter(Boolean).join(' · ')}
            </p>
          </div>
        </div>
        <div className="mt-2 flex gap-3">
          {current.url && (
            <a href={current.url} target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">
              View on {current.source || 'source'} ↗
            </a>
          )}
          {onViewListing && (
            <button onClick={() => onViewListing(current)} className="text-xs text-gray-500 hover:text-gray-800 cursor-pointer">
              View on map
            </button>
          )}
        </div>
        </div>
      </div>

      {/* Dual rating widgets */}
      <div className="flex gap-3">
        <RatingWidget
          label="Flip score" score={current.flip_score} factors={current.flip_factors}
          existing={existingFlip} agree={flipAgree} onAgree={setFlipAgree}
          comment={flipComment} onComment={setFlipComment}
        />
        <RatingWidget
          label="Rent score" score={current.rent_score} factors={current.rent_factors}
          existing={existingRent} agree={rentAgree} onAgree={setRentAgree}
          comment={rentComment} onComment={setRentComment}
        />
      </div>

      {/* Actions */}
      <div className="flex gap-2 justify-end">
        <button onClick={() => goNext(false)}
          className="px-4 py-2 text-xs border border-gray-300 rounded text-gray-600 hover:bg-gray-50 cursor-pointer">
          Skip →
        </button>
        <button onClick={() => goNext(true)} disabled={saving}
          className="px-4 py-2 text-xs bg-gray-800 text-white rounded hover:bg-gray-700 cursor-pointer disabled:opacity-50">
          {saving ? 'Saving…' : 'Save & Next →'}
        </button>
      </div>
    </div>
  )
}
