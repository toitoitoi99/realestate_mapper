import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { fetchScoreBands } from './api'

const DEFAULT_BANDS = {
  A: { min: 60 },
  B: { min: 45 },
  C: { min: 30 },
  D: { min:  0 },
}

const ScoreBandsContext = createContext(null)

export function ScoreBandsProvider({ children }) {
  const [bands, setBands] = useState(DEFAULT_BANDS)

  const refresh = useCallback(async () => {
    try {
      const d = await fetchScoreBands()
      if (d?.score_bands) setBands(d.score_bands)
    } catch (e) {
      console.error('fetchScoreBands failed', e)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  return (
    <ScoreBandsContext.Provider value={{ bands, refresh, setBands }}>
      {children}
    </ScoreBandsContext.Provider>
  )
}

export function useScoreBands() {
  return useContext(ScoreBandsContext) ?? { bands: DEFAULT_BANDS, refresh: () => {}, setBands: () => {} }
}

/** Rate a numeric score using current bands. Returns 'A' | 'B' | 'C' | 'D' | null. */
export function ratingFor(score, bands) {
  if (score == null) return null
  const b = bands || DEFAULT_BANDS
  if (score >= b.A.min) return 'A'
  if (score >= b.B.min) return 'B'
  if (score >= b.C.min) return 'C'
  return 'D'
}
