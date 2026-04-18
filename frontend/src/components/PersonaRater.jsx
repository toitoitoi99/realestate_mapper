import { useEffect, useState } from 'react'
import { fetchRatings, setRating as apiSetRating, clearRating as apiClearRating } from '../api'
import { useScoreBands, ratingFor } from '../ScoreBandsContext'

const PERSONA_META = {
  flip: { label: 'Flip', scoreKey: 'flip_score', accent: 'text-orange-700', bg: 'bg-orange-50', border: 'border-orange-200' },
  rent: { label: 'Rent', scoreKey: 'rent_score', accent: 'text-violet-700', bg: 'bg-violet-50', border: 'border-violet-200' },
}

function Row({ persona, score, rating, onAgree, onDisagree, onClear, saving }) {
  const meta = PERSONA_META[persona]
  const { bands } = useScoreBands()
  const letter = ratingFor(score, bands)
  const current = rating?.agree || null
  const [editing, setEditing] = useState(false)
  const [comment, setComment] = useState(rating?.comment || '')

  useEffect(() => { setComment(rating?.comment || '') }, [rating?.comment])

  const click = (target) => {
    if (current === target) onClear()
    else onAgree && target === 'agree' ? onAgree(comment || null) : onDisagree(comment || null)
  }

  const save = () => {
    if (!current) return
    (current === 'agree' ? onAgree : onDisagree)(comment || null)
    setEditing(false)
  }

  return (
    <div className={`${meta.bg} ${meta.border} border rounded-lg p-2 space-y-1.5`}>
      <div className="flex items-center gap-2">
        <span className={`font-semibold text-xs uppercase tracking-wider ${meta.accent}`}>{meta.label}</span>
        {score != null && (
          <span className="text-xs font-mono text-gray-700">
            {letter} · {Math.round(score)}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => click('agree')}
            disabled={saving}
            title={current === 'agree' ? 'Clear' : 'Score is right'}
            className={`px-2 py-0.5 text-xs rounded border cursor-pointer ${
              current === 'agree'
                ? 'bg-green-600 text-white border-green-700'
                : 'bg-white text-gray-600 border-gray-300 hover:bg-green-50 hover:text-green-700'
            }`}
          >✓ agree</button>
          <button
            onClick={() => click('disagree')}
            disabled={saving}
            title={current === 'disagree' ? 'Clear' : 'Score is wrong'}
            className={`px-2 py-0.5 text-xs rounded border cursor-pointer ${
              current === 'disagree'
                ? 'bg-red-600 text-white border-red-700'
                : 'bg-white text-gray-600 border-gray-300 hover:bg-red-50 hover:text-red-700'
            }`}
          >✗ disagree</button>
        </div>
      </div>

      {current && (
        <div>
          {editing ? (
            <>
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder={current === 'disagree' ? 'Why is the score wrong?' : 'Why is the score right?'}
                className="w-full text-xs border border-gray-300 rounded p-1.5 focus:outline-none focus:ring-1 focus:ring-blue-400"
                rows={2}
              />
              <div className="flex gap-2 mt-1">
                <button onClick={save} className="text-xs bg-blue-600 text-white px-2 py-0.5 rounded hover:bg-blue-700 cursor-pointer">Save</button>
                <button
                  onClick={() => { setEditing(false); setComment(rating?.comment || '') }}
                  className="text-xs text-gray-500 hover:text-gray-700 cursor-pointer"
                >Cancel</button>
              </div>
            </>
          ) : rating?.comment ? (
            <div className="flex items-start gap-2">
              <p className="text-xs text-gray-700 italic whitespace-pre-line flex-1">“{rating.comment}”</p>
              <button
                onClick={() => setEditing(true)}
                className="text-xs text-blue-600 hover:text-blue-800 cursor-pointer shrink-0"
              >Edit</button>
            </div>
          ) : (
            <button
              onClick={() => setEditing(true)}
              className="text-xs text-blue-600 hover:text-blue-800 cursor-pointer"
            >Add note</button>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Admin-only: per-persona agreement with the model's score.
 * Rendered inside listing detail when useIsAdmin() is true.
 */
export default function PersonaRater({ listing }) {
  const kind = listing.listing_type || 'sale'
  const [ratings, setRatings] = useState({ flip: null, rent: null })
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchRatings()
      .then(d => {
        if (cancelled) return
        const mine = { flip: null, rent: null }
        for (const r of (d.ratings ?? [])) {
          if (r.listing_kind === kind && r.listing_id === listing.id && PERSONA_META[r.persona]) {
            mine[r.persona] = r
          }
        }
        setRatings(mine)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [listing.id, kind])

  const save = async (persona, agree, comment) => {
    setSaving(true)
    setRatings(prev => ({ ...prev, [persona]: { ...(prev[persona] || {}), persona, agree, comment } }))
    try {
      const row = await apiSetRating(kind, listing.id, persona, agree, comment)
      setRatings(prev => ({ ...prev, [persona]: row }))
    } catch (e) {
      console.error('setRating failed', e)
    } finally { setSaving(false) }
  }

  const clear = async (persona) => {
    setSaving(true)
    setRatings(prev => ({ ...prev, [persona]: null }))
    try {
      await apiClearRating(kind, listing.id, persona)
    } catch (e) {
      console.error('clearRating failed', e)
    } finally { setSaving(false) }
  }

  return (
    <div className="bg-white border border-dashed border-gray-400 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2 text-xs text-gray-600">
        <span className="px-1.5 py-0.5 bg-gray-800 text-white rounded text-[10px] uppercase tracking-wider">Admin</span>
        <span>Rate the model's per-persona score — tunes the ranking.</span>
      </div>
      <Row
        persona="flip"
        score={listing.flip_score}
        rating={ratings.flip}
        saving={saving}
        onAgree={(c) => save('flip', 'agree', c)}
        onDisagree={(c) => save('flip', 'disagree', c)}
        onClear={() => clear('flip')}
      />
      <Row
        persona="rent"
        score={listing.rent_score}
        rating={ratings.rent}
        saving={saving}
        onAgree={(c) => save('rent', 'agree', c)}
        onDisagree={(c) => save('rent', 'disagree', c)}
        onClear={() => clear('rent')}
      />
    </div>
  )
}
