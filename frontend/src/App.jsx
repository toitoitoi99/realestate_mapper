import { useState, useEffect, useCallback } from 'react'
import { fetchStats, fetchNeighborhoods, fetchListings, fetchProjects, triggerScrape, fetchIneStats, fetchSecurity, fetchParishes, fetchSoldTrends } from './api'
import { useFilters } from './useFilters'
import { CATEGORIES } from './projectCategories'
import StatsBar from './components/StatsBar'
import Sidebar from './components/Sidebar'
import Map from './components/Map'
import './index.css'

export default function App() {
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
  const [visibleGroups, setVisibleGroups] = useState({
    historic: true, riverside: true, uptown: true, residential: true, outer: true,
  })
  const [parishFeatures, setParishFeatures] = useState([])
  const [hiddenParishes, setHiddenParishes] = useState(new Set())
  const [showSoldTrends, setShowSoldTrends] = useState(false)
  const [soldTrendsData, setSoldTrendsData] = useState({ trends: [], points: [] })
  const [soldDateRange, setSoldDateRange] = useState({ start: '2024-06-01', end: '2025-12-31' })
  const [loading, setLoading] = useState(false)
  const [scraping, setScraping] = useState(false)
  const [selectedNeighborhood, setSelectedNeighborhood] = useState(null)

  const { filters, setFilter, reset } = useFilters()

  useEffect(() => {
    fetchStats().then(setStats).catch(console.error)
    fetchNeighborhoods().then(d => setNeighborhoods(d.neighborhoods ?? [])).catch(console.error)
    fetchProjects().then(d => setProjects(d.projects ?? [])).catch(console.error)
    fetchSecurity().then(d => setSecurityPois(d.pois ?? [])).catch(console.error)
    fetchParishes().then(d => setParishFeatures(d.features ?? [])).catch(console.error)
    fetchIneStats().then(d => {
      const total = (d.stats ?? []).find(s => s.category === 'H1')
      setIneStats(total ?? null)
    }).catch(console.error)
  }, [])

  useEffect(() => {
    if (!showSoldTrends) return
    fetchSoldTrends(soldDateRange.start, soldDateRange.end)
      .then(d => setSoldTrendsData({ trends: d.trends ?? [], points: d.points ?? [] }))
      .catch(console.error)
  }, [showSoldTrends, soldDateRange])

  useEffect(() => {
    setLoading(true)
    // show_sold and sort_by are UI-only — don't send them to the API
    const { show_sold, sort_by, ...apiFilters } = filters
    if (selectedNeighborhood) apiFilters.neighborhood = selectedNeighborhood
    fetchListings(apiFilters)
      .then(d => {
        let all = d.listings ?? []
        if (!show_sold) all = all.filter(l => l.status === 'active' || !l.status)
        if (sort_by === 'rarity') all = [...all].sort((a, b) => (b.rarity_score ?? 0) - (a.rarity_score ?? 0))
        else if (sort_by === 'price_asc') all = [...all].sort((a, b) => (a.price_amount ?? 0) - (b.price_amount ?? 0))
        else if (sort_by === 'price_desc') all = [...all].sort((a, b) => (b.price_amount ?? 0) - (a.price_amount ?? 0))
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <StatsBar stats={stats} ineStats={ineStats} onScrape={handleScrape} scraping={scraping} />
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Sidebar
          filters={filters}
          setFilter={setFilter}
          reset={reset}
          listings={listings}
          loading={loading}
          selectedNeighborhood={selectedNeighborhood}
          onClearNeighborhood={() => setSelectedNeighborhood(null)}
        />
        <Map
          listings={listings}
          neighborhoods={neighborhoods}
          onSelectNeighborhood={handleSelectNeighborhood}
          selectedNeighborhood={selectedNeighborhood}
          projects={projects}
          showProjects={showProjects}
          onToggleProjects={() => setShowProjects(p => !p)}
          visibleCategories={visibleCategories}
          onToggleCategory={key => setVisibleCategories(prev => ({ ...prev, [key]: !prev[key] }))}
          securityPois={securityPois}
          showSecurity={showSecurity}
          onToggleSecurity={() => setShowSecurity(p => !p)}
          showNeighborhoods={showNeighborhoods}
          onToggleNeighborhoods={() => setShowNeighborhoods(p => !p)}
          visibleGroups={visibleGroups}
          onToggleGroup={toggleGroup}
          parishFeatures={parishFeatures}
          hiddenParishes={hiddenParishes}
          onToggleParish={toggleParish}
          showSoldTrends={showSoldTrends}
          onToggleSoldTrends={() => setShowSoldTrends(p => !p)}
          soldTrendsData={soldTrendsData}
          soldDateRange={soldDateRange}
          onSoldDateRangeChange={setSoldDateRange}
        />
      </div>
    </div>
  )
}
