// Tiny "?" pill rendered next to the persona insight badge on a listing
// card. Clicking opens a popover that lists the top contributing signals
// with their value and contribution to the persona score — so the user
// can see WHY this listing surfaced where it did.
//
// Uses the same weights vector the backend used (swipe override when set,
// otherwise persona prior), so the breakdown matches the actual rank.

import { useEffect, useRef, useState } from 'react'
import { personaContributions, SIGNAL_LABELS } from '../lib/personaContributions'

const TOP_N = 5

export default function WhyThisRanked({ listing, personaId, weights }) {
  const [open, setOpen] = useState(false)
  const popoverRef = useRef(null)
  const buttonRef = useRef(null)

  // Close on outside click / Escape — popover is a small floating panel.
  useEffect(() => {
    if (!open) return
    function onClick(e) {
      if (popoverRef.current?.contains(e.target)) return
      if (buttonRef.current?.contains(e.target)) return
      setOpen(false)
    }
    function onKey(e) { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const breakdown = personaContributions(listing, personaId, weights)
  if (!breakdown) return null

  return (
    <span className="relative inline-block">
      <button
        ref={buttonRef}
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen(o => !o) }}
        className="ml-1 inline-flex items-center justify-center w-4 h-4 rounded-full bg-gray-100 hover:bg-gray-200 text-gray-500 hover:text-gray-800 text-[10px] font-bold leading-none cursor-pointer"
        title="Why did this listing rank here?"
        aria-expanded={open}
      >
        ?
      </button>

      {open && (
        <div
          ref={popoverRef}
          onClick={(e) => e.stopPropagation()}
          className="absolute z-30 right-0 mt-1 w-64 bg-white border border-gray-200 rounded-lg shadow-lg p-3 text-left"
        >
          <div className="flex items-baseline justify-between">
            <h4 className="text-xs font-semibold text-gray-900">Why this rank</h4>
            <span className="text-[10px] text-gray-400">
              score {Math.round(breakdown.score)}
            </span>
          </div>
          {breakdown.weights_overridden && (
            <div className="mt-1 inline-block px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 text-[9px] font-medium">
              ✨ Personalized weights
            </div>
          )}

          <ul className="mt-2 space-y-1.5">
            {breakdown.items.slice(0, TOP_N).map(it => (
              <li key={it.signal}>
                <div className="flex justify-between text-[11px] text-gray-700">
                  <span className="truncate">{SIGNAL_LABELS[it.signal] || it.signal}</span>
                  <span className="font-mono text-gray-500">
                    {Math.round(it.value)}&nbsp;×&nbsp;{(it.weight * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="h-1 bg-gray-100 rounded overflow-hidden mt-0.5">
                  <div
                    className="h-full bg-primary"
                    style={{ width: `${Math.max(0, Math.min(100, it.value))}%` }}
                  />
                </div>
                <div className="text-right text-[10px] text-gray-400">
                  contributes {it.contribution.toFixed(1)}
                </div>
              </li>
            ))}
          </ul>

          {breakdown.items.length > TOP_N && (
            <div className="mt-2 text-[10px] text-gray-400">
              +{breakdown.items.length - TOP_N} more signal{breakdown.items.length - TOP_N === 1 ? '' : 's'}
            </div>
          )}
        </div>
      )}
    </span>
  )
}
