import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchAreas, fetchStats, fetchNeighborhoods, fetchListings, fetchProjects, triggerScrape, fetchIneStats, fetchSecurity, fetchParishes, fetchSoldTrends, fetchNeighbourhoodTypologies, fetchParishStats, fetchScrapeRuns, fetchReactions, setReaction as apiSetReaction, clearReaction as apiClearReaction } from './api'
import { useAuth } from './contexts/AuthContext'
import { getPersona } from './lib/personas'
import { computeWeights, weightsToQueryParam, MIN_SWIPES_FOR_OVERRIDE } from './lib/swipeWeights'
import { reactionsToSwipes, indexListingsByKey } from './lib/reactionWeights'
import { supabase } from './lib/supabase'
import { preferencesToFilters } from './lib/preferences'
import { useFilters } from './useFilters'
import { CATEGORIES } from './projectCategories'
import { DEFAULT_BASE_MAP } from './baseMaps'
import { buildGroups } from './neighborhoodGroups'
import StatsBar from './components/StatsBar'
import Sidebar from './components/Sidebar'
import Map from './components/Map'
import PersonaBar from './components/PersonaBar'
import AdminPage from './components/AdminPage'
import MyListingsPage from './components/MyListingsPage'
import './index.css'

export default function App() {
  const [areas, setAreas] = useState({})
  const [currentArea, setCurrentArea] = useState('aml')
  const [stats, setStats] = useState(null)
  const [ineStats, setIneStats] = useState(null)
  const [neighborhoods, setNeighborhoods] = useState([])
  const [listings, setListings] = useState([])
  const [projects, setProjects] = useState([])
  const [showProjects, setShowProjects] = useState(true)
  const [securityPois, setSecurityPois] = useState([])
  const [showSecurity, setShowSecurity] = useState(false)
  const [visibleCategories, setVisibleCategories] = useState(
    Object.fromEntries(Object.entries(CATEGORIES).map(([k, v]) => [k, v.defaultVisible]))
  )
  const [showNeighborhoods, setShowNeighborhoods] = useState(false)
  const [visibleGroups, setVisibleGroups] = useState({})
  const [parishFeatures, setParishFeatures] = useState([])
  const [hiddenParishes, setHiddenParishes] = useState(new Set())
  const [showSoldTrends, setShowSoldTrends] = useState(false)
  const [soldTrendsData, setSoldTrendsData] = useState({ trends: [], points: [] })
  const [soldDateRange, setSoldDateRange] = useState({ start: '2024-06-01', end: '2025-12-31' })
  const [baseMap, setBaseMap] = useState(DEFAULT_BASE_MAP)
  const [loading, setLoading] = useState(false)
  const [scraping, setScraping] = useState(false)
  const [selectedScraper, setSelectedScraper] = useState('idealista')
  const [scrapeStatus, setScrapeStatus] = useState(null)  // latest scrape_runs row for selectedScraper, or null
  const pollRef = useRef(null)
  const fadeRef = useRef(null)
  const [selectedNeighborhood, setSelectedNeighborhood] = useState(null)
  const [selectedListing, setSelectedListing] = useState(null)
  const [highlightedListing, setHighlightedListing] = useState(null)
  const [sidebarTab, setSidebarTab] = useState('listings')
  const [neighbourhoodTypologies, setNeighbourhoodTypologies] = useState({})
  const [parishStats, setParishStats] = useState({})
  const [selectedParishes, setSelectedParishes] = useState(new Set())
  // Map of `${kind}-${id}` → { reaction, comment }
  const [reactions, setReactions] = useState({})
  const [showDisliked, setShowDisliked] = useState(false)
  const [view, setView] = useState('map')  // 'map' | 'admin' | 'my-listings'

  const { filters, setFilter, reset, replaceAll: replaceAllFilters } = useFilters()

  // Auth + persona — when a user is signed in with a persona, listings get
  // a `persona_score` column and the map ranks/colors by that.
  const { profile, user, signOut } = useAuth()
  const navigate = useNavigate()
  const activePersonaId = profile?.persona ?? null
  const activePersona = getPersona(activePersonaId)
  const lastAppliedPersona = useRef(null)

  // Personalization signal sources:
  //   - profile_swipes (onboarding deck) — fetched once per user/persona
  //   - listing_reactions (👍/👎 in the wild) — from existing `reactions`
  //     state, converted to swipe-shape via reactionWeights.reactionsToSwipes
  // Both flow through the same computeWeights() math.
  const [rawSwipes, setRawSwipes] = useState([])
  useEffect(() => {
    if (!user || !activePersonaId || !supabase) {
      setRawSwipes([])
      return
    }
    let cancelled = false
    supabase
      .from('profile_swipes')
      .select('action, factor_positives, persona, created_at')
      .eq('user_id', user.id)
      .eq('persona', activePersonaId)
      .order('created_at', { ascending: false })
      .limit(200)
      .then(({ data, error }) => {
        if (cancelled) return
        if (error) {
          console.warn('swipes load error:', error)
          setRawSwipes([])
          return
        }
        setRawSwipes(data ?? [])
      })
    return () => { cancelled = true }
  }, [user?.id, activePersonaId])

  // Derive the override weights from raw swipes + reaction-pseudo-swipes.
  // Memoized — re-runs when the user reacts to a listing or a fresh listing
  // batch arrives (so a like immediately nudges future ranking).
  const listingsByKey = useMemo(() => indexListingsByKey(listings), [listings])
  const reactionPseudoSwipes = useMemo(
    () => reactionsToSwipes(reactions, listingsByKey, activePersonaId),
    [reactions, listingsByKey, activePersonaId]
  )
  const combinedSignals = useMemo(
    () => [...rawSwipes, ...reactionPseudoSwipes],
    [rawSwipes, reactionPseudoSwipes]
  )
  const personaWeights = useMemo(
    () => computeWeights(activePersonaId, combinedSignals),
    [activePersonaId, combinedSignals]
  )
  // Surface "Personalized by N signals" only when the override actually fires.
  const activeSwipeCount = personaWeights
    ? combinedSignals.filter(s => s.action === 'like' || s.action === 'dislike').length
    : 0

  // When the persona changes, default the listing_type filter to its preferred view.
  // Only fires on actual persona change, so the user can still toggle freely after.
  useEffect(() => {
    if (activePersona && lastAppliedPersona.current !== activePersonaId) {
      setFilter('listing_type', activePersona.defaultView === 'rent' ? 'rent' : 'sale')
      lastAppliedPersona.current = activePersonaId
    }
  }, [activePersonaId, activePersona, setFilter])

  useEffect(() => {
    fetchAreas().then(setAreas).catch(console.error)
  }, [])

  useEffect(() => {
    fetchStats().then(setStats).catch(console.error)
    fetchNeighborhoods().then(d => setNeighborhoods(d.neighborhoods ?? [])).catch(console.error)
    fetchProjects().then(d => setProjects(d.projects ?? [])).catch(console.error)
    fetchSecurity().then(d => setSecurityPois(d.pois ?? [])).catch(console.error)
    fetchIneStats().then(d => {
      const total = (d.stats ?? []).find(s => s.category === 'H1')
      setIneStats(total ?? null)
    }).catch(console.error)
    fetchNeighbourhoodTypologies().then(d => setNeighbourhoodTypologies(d.typologies ?? {})).catch(console.error)
    fetchParishStats(currentArea).then(d => setParishStats(d.stats ?? {})).catch(console.error)
    fetchReactions().then(d => {
      const map = {}
      for (const r of (d.reactions ?? [])) {
        map[`${r.listing_kind}-${r.listing_id}`] = { reaction: r.reaction, comment: r.comment }
      }
      setReactions(map)
    }).catch(console.error)
  }, [])

  useEffect(() => {
    fetchParishes(currentArea).then(d => {
      const features = d.features ?? []
      setParishFeatures(features)
      // Reset visibility: all groups visible, no hidden parishes
      const { groups } = buildGroups(features)
      setVisibleGroups(Object.fromEntries(Object.keys(groups).map(k => [k, true])))
      setHiddenParishes(new Set())
    }).catch(console.error)
  }, [currentArea])

  const { groups: neighborhoodGroups, parishToGroup } = useMemo(
    () => buildGroups(parishFeatures), [parishFeatures]
  )

  useEffect(() => {
    if (!showSoldTrends) return
    fetchSoldTrends(soldDateRange.start, soldDateRange.end)
      .then(d => setSoldTrendsData({ trends: d.trends ?? [], points: d.points ?? [] }))
      .catch(console.error)
  }, [showSoldTrends, soldDateRange])

  useEffect(() => {
    setLoading(true)
    // show_sold and sort_by are UI-only — don't send them to the API
    const { show_sold, sort_by, listing_type, min_flip_score, min_rent_score, ...apiFilters } = filters
    // 'all' means no listing_type filter (backend unions both tables)
    if (listing_type && listing_type !== 'all') apiFilters.listing_type = listing_type
    if (min_flip_score > 0) apiFilters.min_flip_score = min_flip_score
    if (min_rent_score > 0) apiFilters.min_rent_score = min_rent_score
    if (selectedNeighborhood) apiFilters.neighborhood = selectedNeighborhood
    if (activePersonaId) apiFilters.persona = activePersonaId
    if (personaWeights) {
      const wq = weightsToQueryParam(personaWeights)
      if (wq) apiFilters.weights = wq
    }
    // Persona preferences (saved on profile) layer in as additional filters.
    // UI filters always win — only fill in keys the UI hasn't set.
    const prefFilters = preferencesToFilters(profile?.preferences)
    for (const [k, v] of Object.entries(prefFilters)) {
      if (apiFilters[k] === undefined || apiFilters[k] === '') apiFilters[k] = v
    }
    fetchListings(apiFilters)
      .then(d => {
        let all = d.listings ?? []
        if (show_sold === 'active' || !show_sold) all = all.filter(l => l.status === 'active' || !l.status)
        else if (show_sold === 'sold') all = all.filter(l => l.status === 'sold' || l.status === 'reserved')
        // When a persona is active and user hasn't picked a sort, default to persona_score desc
        if (!sort_by && activePersonaId) {
          all = [...all].sort((a, b) => (b.persona_score ?? -1) - (a.persona_score ?? -1))
        }
        if (sort_by === 'persona') all = [...all].sort((a, b) => (b.persona_score ?? -1) - (a.persona_score ?? -1))
        else if (sort_by === 'flip') all = [...all].sort((a, b) => (b.flip_score ?? 0) - (a.flip_score ?? 0))
        else if (sort_by === 'rent_score') all = [...all].sort((a, b) => (b.rent_score ?? 0) - (a.rent_score ?? 0))
        else if (sort_by === 'price_asc') all = [...all].sort((a, b) => (a.price_amount ?? 0) - (b.price_amount ?? 0))
        else if (sort_by === 'price_desc') all = [...all].sort((a, b) => (b.price_amount ?? 0) - (a.price_amount ?? 0))
        else if (sort_by === 'psm_gross_asc') all = [...all].sort((a, b) => {
          const aP = a.price_amount, aA = a.gross_area_sqm || a.size_sqm
          const bP = b.price_amount, bA = b.gross_area_sqm || b.size_sqm
          const aV = (aP && aA) ? aP / aA : Infinity
          const bV = (bP && bA) ? bP / bA : Infinity
          return aV - bV
        })
        else if (sort_by === 'psm_gross_desc') all = [...all].sort((a, b) => {
          const aP = a.price_amount, aA = a.gross_area_sqm || a.size_sqm
          const bP = b.price_amount, bA = b.gross_area_sqm || b.size_sqm
          const aV = (aP && aA) ? aP / aA : -Infinity
          const bV = (bP && bA) ? bP / bA : -Infinity
          return bV - aV
        })
        else if (sort_by === 'psm_living_asc') all = [...all].sort((a, b) => {
          const aV = (a.price_amount && a.size_sqm) ? a.price_amount / a.size_sqm : Infinity
          const bV = (b.price_amount && b.size_sqm) ? b.price_amount / b.size_sqm : Infinity
          return aV - bV
        })
        else if (sort_by === 'psm_living_desc') all = [...all].sort((a, b) => {
          const aV = (a.price_amount && a.size_sqm) ? a.price_amount / a.size_sqm : -Infinity
          const bV = (b.price_amount && b.size_sqm) ? b.price_amount / b.size_sqm : -Infinity
          return bV - aV
        })
        else if (sort_by === 'biggest') all = [...all].sort((a, b) => (b.gross_area_sqm || b.size_sqm || 0) - (a.gross_area_sqm || a.size_sqm || 0))
        else if (sort_by === 'smallest') all = [...all].sort((a, b) => (a.gross_area_sqm || a.size_sqm || 0) - (b.gross_area_sqm || b.size_sqm || 0))
        setListings(all)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [filters, selectedNeighborhood, activePersonaId, profile?.preferences, personaWeights])

  // Poll a single source's latest scrape_runs row; transition UI state
  // when it reaches a terminal status.
  const pollOnce = useCallback(async (source) => {
    try {
      const { runs = [] } = await fetchScrapeRuns({ source, limit: 1 })
      const row = runs[0] ?? null
      if (row) setScrapeStatus(row)
      return row
    } catch (e) {
      console.error('poll scrape-runs failed', e)
      return null
    }
  }, [])

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const startPolling = useCallback((source) => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      const row = await pollOnce(source)
      if (row && (row.status === 'completed' || row.status === 'failed')) {
        stopPolling()
        setScraping(false)
        fetchStats().then(setStats).catch(() => {})
        // Keep the finished status visible briefly, then fade.
        if (fadeRef.current) clearTimeout(fadeRef.current)
        fadeRef.current = setTimeout(() => setScrapeStatus(null), 30000)
      }
    }, 3000)
  }, [pollOnce, stopPolling])

  const handleScrape = useCallback(async () => {
    if (scraping) return
    setScraping(true)
    // Provisional row so the UI flips to "running" immediately; real row
    // overwrites this on the next poll tick.
    setScrapeStatus({
      source: selectedScraper,
      status: 'running',
      listings_found: 0,
      listings_new: 0,
      errors: 0,
      started_at: new Date().toISOString(),
    })
    if (fadeRef.current) { clearTimeout(fadeRef.current); fadeRef.current = null }
    try {
      await triggerScrape({ source: selectedScraper, maxPages: 10 })
      startPolling(selectedScraper)
    } catch (e) {
      setScraping(false)
      setScrapeStatus({
        source: selectedScraper,
        status: 'failed',
        notes: String(e?.message ?? e),
      })
    }
  }, [scraping, selectedScraper, startPolling])

  // Rehydrate on mount / when the selected scraper changes: if the latest
  // row for the selected source is already `running` (e.g. a scrape kicked
  // off in another tab, or the user reloaded mid-run), resume polling.
  useEffect(() => {
    let cancelled = false
    pollOnce(selectedScraper).then((row) => {
      if (cancelled) return
      if (row && row.status === 'running') {
        setScraping(true)
        startPolling(selectedScraper)
      }
    })
    return () => { cancelled = true }
  }, [selectedScraper, pollOnce, startPolling])

  // Cleanup timers on unmount
  useEffect(() => () => {
    stopPolling()
    if (fadeRef.current) clearTimeout(fadeRef.current)
  }, [stopPolling])

  const reactionFor = useCallback((listing) => {
    if (!listing) return null
    return reactions[`${listing.listing_type || 'sale'}-${listing.id}`] || null
  }, [reactions])

  const filteredListings = useMemo(() => {
    let base = listings
    // Hide disliked listings by default — toggle in MapLegend brings them back.
    if (!showDisliked) {
      base = base.filter(l => {
        const r = reactions[`${l.listing_type || 'sale'}-${l.id}`]
        return r?.reaction !== 'dislike'
      })
    }

    if (!showNeighborhoods) return base

    // If parishes selected for comparison, show only those
    if (selectedParishes?.size > 0) {
      return base.filter(l => l.neighborhood && selectedParishes.has(l.neighborhood))
    }

    return base.filter(l => {
      if (!l.neighborhood) return true
      const group = parishToGroup?.[l.neighborhood]
      if (!group) return true           // unknown parish → keep visible
      if (!visibleGroups[group]) return false
      if (hiddenParishes.has(l.neighborhood)) return false
      return true
    })
  }, [listings, reactions, showDisliked, showNeighborhoods, hiddenParishes, parishToGroup, visibleGroups, selectedParishes])

  const handleSetReaction = useCallback(async (listing, reaction, comment) => {
    const kind = listing.listing_type || 'sale'
    const key = `${kind}-${listing.id}`
    // Optimistic update
    setReactions(prev => ({ ...prev, [key]: { reaction, comment: comment ?? null } }))
    try {
      const row = await apiSetReaction(kind, listing.id, reaction, comment ?? null)
      setReactions(prev => ({ ...prev, [key]: { reaction: row.reaction, comment: row.comment } }))
    } catch (e) {
      console.error('setReaction failed', e)
    }
  }, [])

  const handleClearReaction = useCallback(async (listing) => {
    const kind = listing.listing_type || 'sale'
    const key = `${kind}-${listing.id}`
    setReactions(prev => {
      const next = { ...prev }
      delete next[key]
      return next
    })
    try {
      await apiClearReaction(kind, listing.id)
    } catch (e) {
      console.error('clearReaction failed', e)
    }
  }, [])

  const toggleGroup = useCallback((key) => {
    setVisibleGroups(prev => ({ ...prev, [key]: !prev[key] }))
  }, [])

  const toggleParish = useCallback((name) => {
    setHiddenParishes(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])

  const handleSelectNeighborhood = useCallback((name) => {
    setSelectedNeighborhood(prev => prev === name ? null : name)
  }, [])

  const toggleSelectedParish = useCallback((name) => {
    setSelectedParishes(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])

  const clearSelectedParishes = useCallback(() => {
    setSelectedParishes(new Set())
  }, [])

  const viewListingOnMap = (listing) => {
    // Seed into the current listings array if not present so the pin
    // renders even when the active filters would exclude it.
    setListings(prev => {
      const key = `${listing.id}-${listing.listing_type}`
      if (prev.some(l => `${l.id}-${l.listing_type}` === key)) return prev
      return [...prev, listing]
    })
    setSelectedListing(listing)
    setHighlightedListing(listing)
    setView('map')
  }

  if (view === 'admin') {
    return (
      <AdminPage
        onBack={() => setView('map')}
        selectedScraper={selectedScraper}
        onSelectScraper={setSelectedScraper}
        scraping={scraping}
        scrapeStatus={scrapeStatus}
        onScrape={handleScrape}
        onViewListing={viewListingOnMap}
      />
    )
  }

  if (view === 'my-listings') {
    return (
      <MyListingsPage
        onBack={() => setView('map')}
        onViewListing={viewListingOnMap}
      />
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <PersonaBar
        persona={activePersona}
        preferences={profile?.preferences}
        userEmail={user?.email}
        listingCount={filteredListings.length}
        totalCount={
          // Match the active listing_type — sale/rent/all — so the ratio is meaningful.
          filters.listing_type === 'rent' ? stats?.rentals_count
            : filters.listing_type === 'all' ? stats?.total_listings
            : stats?.sales_count
        }
        filters={filters}
        area={currentArea}
        swipeCount={activeSwipeCount}
        onApplySavedSearch={({ filters: f, area }) => {
          replaceAllFilters(f)
          if (area) setCurrentArea(area)
        }}
        onEditProfile={() => navigate('/onboarding')}
        onSignOut={async () => { await signOut(); navigate('/') }}
        onOpenMyListings={() => setView('my-listings')}
      />
      <StatsBar
        stats={stats}
        ineStats={ineStats}
        onScrape={handleScrape}
        scraping={scraping}
        areas={areas}
        currentArea={currentArea}
        onChangeArea={setCurrentArea}
        selectedScraper={selectedScraper}
        onSelectScraper={setSelectedScraper}
        onOpenAdmin={() => setView('admin')}
      />
      <div style={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <Sidebar
          filters={filters}
          setFilter={setFilter}
          reset={reset}
          listings={filteredListings}
          loading={loading}
          preferences={profile?.preferences}
          personaId={activePersonaId}
          personaWeights={personaWeights}
          selectedNeighborhood={selectedNeighborhood}
          onClearNeighborhood={() => setSelectedNeighborhood(null)}
          selectedListing={selectedListing}
          onSelectListing={(l) => { setSelectedListing(l); setHighlightedListing(l); }}
          highlightedListing={highlightedListing}
          onClearHighlight={() => setHighlightedListing(null)}
          sidebarTab={sidebarTab}
          onChangeTab={setSidebarTab}
          // Neighbourhood panel props
          neighborhoods={neighborhoods}
          neighbourhoodTypologies={neighbourhoodTypologies}
          parishStats={parishStats}
          ineStats={ineStats}
          showNeighborhoods={showNeighborhoods}
          onToggleNeighborhoods={() => setShowNeighborhoods(p => !p)}
          visibleGroups={visibleGroups}
          onToggleGroup={toggleGroup}
          neighborhoodGroups={neighborhoodGroups}
          hiddenParishes={hiddenParishes}
          onToggleParish={toggleParish}
          onSelectNeighborhood={handleSelectNeighborhood}
          showProjects={showProjects}
          onToggleProjects={() => setShowProjects(p => !p)}
          visibleCategories={visibleCategories}
          onToggleCategory={key => setVisibleCategories(prev => ({ ...prev, [key]: !prev[key] }))}
          projects={projects}
          showSoldTrends={showSoldTrends}
          onToggleSoldTrends={() => setShowSoldTrends(p => !p)}
          soldDateRange={soldDateRange}
          onSoldDateRangeChange={setSoldDateRange}
          soldTrendsData={soldTrendsData}
          showSecurity={showSecurity}
          onToggleSecurity={() => setShowSecurity(p => !p)}
          securityPois={securityPois}
          selectedParishes={selectedParishes}
          onToggleSelectedParish={toggleSelectedParish}
          onClearSelectedParishes={clearSelectedParishes}
          reactionFor={reactionFor}
          onSetReaction={handleSetReaction}
          onClearReaction={handleClearReaction}
          scoreShow={activePersona?.primaryScore ?? 'both'}
        />
        <Map
          areaConfig={areas[currentArea]}
          baseMap={baseMap}
          onChangeBaseMap={setBaseMap}
          listings={filteredListings}
          listingTypeFilter={filters.listing_type || 'sale'}
          personalized={Boolean(personaWeights)}
          neighborhoods={neighborhoods}
          onSelectNeighborhood={handleSelectNeighborhood}
          selectedNeighborhood={selectedNeighborhood}
          onSelectListing={(l) => {
            setHighlightedListing(l)
            setSelectedListing(prev => prev ? l : prev)
          }}
          selectedListing={highlightedListing}
          projects={projects}
          showProjects={showProjects}
          visibleCategories={visibleCategories}
          securityPois={securityPois}
          showSecurity={showSecurity}
          showNeighborhoods={showNeighborhoods}
          visibleGroups={visibleGroups}
          neighborhoodGroups={neighborhoodGroups}
          parishToGroup={parishToGroup}
          parishFeatures={parishFeatures}
          hiddenParishes={hiddenParishes}
          showSoldTrends={showSoldTrends}
          soldTrendsData={soldTrendsData}
          selectedParishes={selectedParishes}
          reactionFor={reactionFor}
          showDisliked={showDisliked}
          onToggleShowDisliked={() => setShowDisliked(p => !p)}
          onLookupResult={(newListings) => {
            setListings(prev => {
              const ids = new Set(prev.map(l => `${l.id}-${l.listing_type}`))
              const toAdd = newListings.filter(l => !ids.has(`${l.id}-${l.listing_type}`))
              return toAdd.length ? [...prev, ...toAdd] : prev
            })
          }}
        />
      </div>
    </div>
  )
}
