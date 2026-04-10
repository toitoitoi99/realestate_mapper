import { useState, useEffect, useCallback, useMemo } from 'react'
import { fetchAreas, fetchStats, fetchNeighborhoods, fetchListings, fetchProjects, triggerScrape, fetchIneStats, fetchSecurity, fetchParishes, fetchSoldTrends, fetchNeighbourhoodTypologies, fetchParishStats } from './api'
import { useFilters } from './useFilters'
import { CATEGORIES } from './projectCategories'
import { DEFAULT_BASE_MAP } from './baseMaps'
import { buildGroups } from './neighborhoodGroups'
import StatsBar from './components/StatsBar'
import Sidebar from './components/Sidebar'
import Map from './components/Map'
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
  const [selectedNeighborhood, setSelectedNeighborhood] = useState(null)
  const [selectedListing, setSelectedListing] = useState(null)
  const [highlightedListing, setHighlightedListing] = useState(null)
  const [sidebarTab, setSidebarTab] = useState('listings')
  const [neighbourhoodTypologies, setNeighbourhoodTypologies] = useState({})
  const [parishStats, setParishStats] = useState({})
  const [selectedParishes, setSelectedParishes] = useState(new Set())

  const { filters, setFilter, reset } = useFilters()

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
    const { show_sold, sort_by, listing_type, min_deal_score, min_rarity_score, ...apiFilters } = filters
    // 'all' means no listing_type filter (backend unions both tables)
    if (listing_type && listing_type !== 'all') apiFilters.listing_type = listing_type
    if (min_deal_score > 0) apiFilters.min_deal_score = min_deal_score
    if (min_rarity_score > 0) apiFilters.min_rarity_score = min_rarity_score
    if (selectedNeighborhood) apiFilters.neighborhood = selectedNeighborhood
    fetchListings(apiFilters)
      .then(d => {
        let all = d.listings ?? []
        if (show_sold === 'active' || !show_sold) all = all.filter(l => l.status === 'active' || !l.status)
        else if (show_sold === 'sold') all = all.filter(l => l.status === 'sold' || l.status === 'reserved')
        if (sort_by === 'rating') all = [...all].sort((a, b) => (b.deal_score ?? 0) - (a.deal_score ?? 0))
        else if (sort_by === 'rarity') all = [...all].sort((a, b) => (b.rarity_score ?? 0) - (a.rarity_score ?? 0))
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
  }, [filters, selectedNeighborhood])

  const handleScrape = useCallback(async () => {
    setScraping(true)
    try {
      await triggerScrape(10)
      setTimeout(() => {
        fetchStats().then(setStats)
        setScraping(false)
      }, 5000)
    } catch {
      setScraping(false)
    }
  }, [])

  const filteredListings = useMemo(() => {
    const base = listings
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
  }, [listings, showNeighborhoods, hiddenParishes, parishToGroup, visibleGroups, selectedParishes])

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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <StatsBar stats={stats} ineStats={ineStats} onScrape={handleScrape} scraping={scraping} areas={areas} currentArea={currentArea} onChangeArea={setCurrentArea} />
      <div style={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <Sidebar
          filters={filters}
          setFilter={setFilter}
          reset={reset}
          listings={filteredListings}
          loading={loading}
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
        />
        <Map
          areaConfig={areas[currentArea]}
          baseMap={baseMap}
          onChangeBaseMap={setBaseMap}
          listings={filteredListings}
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
