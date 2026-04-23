import { useState, useEffect, useMemo } from 'react'
import { useLanguage } from '../LanguageContext'
import FilterPanel from './FilterPanel'
import ListingCard from './ListingCard'
import ListingDetail from './ListingDetail'
import SidebarTabs from './SidebarTabs'
import NeighbourhoodPanel from './NeighbourhoodPanel'
import NeighbourhoodComparison from './NeighbourhoodComparison'

const CollapseChevron = ({ collapsed }) => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    {collapsed
      ? <polyline points="9 6 15 12 9 18" />
      : <polyline points="15 6 9 12 15 18" />}
  </svg>
)

export default function Sidebar({
  filters, setFilter, reset,
  listings, loading,
  selectedNeighborhood, onClearNeighborhood,
  selectedListing, onSelectListing,
  highlightedListing, onClearHighlight,
  sidebarTab, onChangeTab,
  // Neighbourhood panel props
  neighborhoods, neighbourhoodTypologies, parishStats, ineStats,
  showNeighborhoods, onToggleNeighborhoods,
  visibleGroups, onToggleGroup,
  neighborhoodGroups, hiddenParishes, onToggleParish,
  onSelectNeighborhood,
  showProjects, onToggleProjects,
  visibleCategories, onToggleCategory, projects,
  showSoldTrends, onToggleSoldTrends,
  soldDateRange, onSoldDateRangeChange, soldTrendsData,
  showSecurity, onToggleSecurity, securityPois,
  selectedParishes, onToggleSelectedParish, onClearSelectedParishes,
  reactionFor, onSetReaction, onClearReaction,
  preferences, personaId, personaWeights,
  scoreShow = 'both',
}) {
  const { t } = useLanguage()
  const [collapsed, setCollapsed] = useState(false)
  const [filtersCollapsed, setFiltersCollapsed] = useState(false)
  const PAGE_SIZE = 50
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)

  const [showSimilar, setShowSimilar] = useState(true)

  // Reset pagination when listings change
  useEffect(() => { setVisibleCount(PAGE_SIZE) }, [listings])

  // Compute 10 most similar listings to the highlighted one
  const similarListings = useMemo(() => {
    if (!highlightedListing || !listings?.length) return []
    const hl = highlightedListing
    if (!hl.lat || !hl.lon) return []

    // Haversine distance in km
    const toRad = d => d * Math.PI / 180
    const haversine = (lat1, lon1, lat2, lon2) => {
      const dLat = toRad(lat2 - lat1), dLon = toRad(lon2 - lon1)
      const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
      return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
    }

    const maxDist = 5 // km — cap normalization at 5km
    const scored = listings
      .filter(l => l.id !== hl.id && l.lat && l.lon)
      .map(l => {
        const dist = haversine(hl.lat, hl.lon, l.lat, l.lon)
        const distScore = 1 - Math.min(dist / maxDist, 1) // 1 = same spot, 0 = 5km+

        const priceDiff = (hl.price_amount && l.price_amount)
          ? 1 - Math.min(Math.abs(hl.price_amount - l.price_amount) / hl.price_amount, 1)
          : 0

        const hlSize = hl.gross_area_sqm || hl.size_sqm
        const lSize = l.gross_area_sqm || l.size_sqm
        const sizeDiff = (hlSize && lSize)
          ? 1 - Math.min(Math.abs(hlSize - lSize) / hlSize, 1)
          : 0

        let roomScore = 0
        if (hl.rooms != null && l.rooms != null) {
          if (hl.rooms === l.rooms) roomScore = 1
          else if (Math.abs(hl.rooms - l.rooms) === 1) roomScore = 0.5
        }

        const score = distScore * 0.4 + priceDiff * 0.25 + sizeDiff * 0.2 + roomScore * 0.15
        return { ...l, _score: score, _dist: dist }
      })

    scored.sort((a, b) => b._score - a._score)
    return scored.slice(0, 10)
  }, [highlightedListing, listings])

  const toggleBtn = (
    <button
      onClick={() => setCollapsed(c => !c)}
      className="absolute top-1/2 -translate-y-1/2 left-full z-[1001] w-6 h-14 rounded-r-md bg-white border border-l-0 border-gray-300 shadow-md flex items-center justify-center text-gray-400 hover:text-gray-700 hover:bg-gray-50 cursor-pointer transition-colors"
      title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
    >
      <CollapseChevron collapsed={collapsed} />
    </button>
  )

  if (collapsed) {
    return (
      <div className="relative shrink-0 w-0">
        {toggleBtn}
      </div>
    )
  }

  if (selectedListing) {
    return (
      <div className="relative w-80 shrink-0">
        {toggleBtn}
        <div className="flex flex-col bg-white border-r border-gray-200 overflow-hidden h-full">
          <ListingDetail
            listing={selectedListing}
            onBack={() => onSelectListing(null)}
            parishStats={parishStats}
            ineStats={ineStats}
            reaction={reactionFor?.(selectedListing)}
            onSetReaction={onSetReaction}
            onClearReaction={onClearReaction}
            personaId={personaId}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="relative w-80 shrink-0 min-h-0">
      {toggleBtn}
      <div className="flex flex-col bg-white border-r border-gray-200 overflow-hidden h-full">

      {/* Tab toggle */}
      <SidebarTabs activeTab={sidebarTab} onChangeTab={onChangeTab} />

      {/* Top section — switches by tab */}
      {sidebarTab === 'listings' ? (
        <>
          {/* Collapsible filter section */}
          {!filtersCollapsed && (
            <div className="overflow-y-auto shrink-0" style={{ maxHeight: '50vh' }}>
              <FilterPanel
                filters={filters}
                setFilter={setFilter}
                reset={reset}
              />
            </div>
          )}

          {/* Filter collapse toggle */}
          <button
            onClick={() => setFiltersCollapsed(v => !v)}
            className="flex items-center justify-center gap-1 px-4 py-1.5 bg-gray-50 border-y border-gray-200 text-xs text-gray-500 hover:bg-gray-100 cursor-pointer shrink-0 transition-colors"
          >
            <span>{filtersCollapsed ? t.filters : ''}</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              {filtersCollapsed
                ? <polyline points="6 9 12 15 18 9" />
                : <polyline points="6 15 12 9 18 15" />}
            </svg>
            {filtersCollapsed && <span className="text-gray-400 ml-1">({t.filters})</span>}
          </button>
        </>
      ) : (
        <NeighbourhoodPanel
          showNeighborhoods={showNeighborhoods}
          onToggleNeighborhoods={onToggleNeighborhoods}
          visibleGroups={visibleGroups}
          onToggleGroup={onToggleGroup}
          neighborhoodGroups={neighborhoodGroups}
          hiddenParishes={hiddenParishes}
          onToggleParish={onToggleParish}
          neighborhoods={neighborhoods}
          typologies={neighbourhoodTypologies}
          parishStats={parishStats}
          onSelectNeighborhood={onSelectNeighborhood}
          showProjects={showProjects}
          onToggleProjects={onToggleProjects}
          visibleCategories={visibleCategories}
          onToggleCategory={onToggleCategory}
          projects={projects}
          showSoldTrends={showSoldTrends}
          onToggleSoldTrends={onToggleSoldTrends}
          soldDateRange={soldDateRange}
          onSoldDateRangeChange={onSoldDateRangeChange}
          soldTrendsData={soldTrendsData}
          showSecurity={showSecurity}
          onToggleSecurity={onToggleSecurity}
          securityPois={securityPois}
          selectedParishes={selectedParishes}
          onToggleSelectedParish={onToggleSelectedParish}
          onClearSelectedParishes={onClearSelectedParishes}
        />
      )}

      {/* Comparison cards */}
      {selectedParishes?.size > 0 && (
        <NeighbourhoodComparison
          selectedParishes={selectedParishes}
          parishStats={parishStats}
          onRemoveParish={onToggleSelectedParish}
          onClearAll={onClearSelectedParishes}
        />
      )}

      {/* Results header: count + sort — always visible */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-white shrink-0">
        <span className="text-sm font-semibold text-gray-700">
          {listings?.length != null ? t.listingsCount(listings.length) : ''}
        </span>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-gray-400">{t.sortBy}</label>
          <select
            value={filters.sort_by}
            onChange={e => setFilter('sort_by', e.target.value)}
            className="border border-gray-200 rounded px-1.5 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white"
          >
            <option value="default">{t.sortDefault}</option>
            {personaId && <option value="persona">{t.sortForYou}</option>}
            <option value="flip">Flip score</option>
            <option value="rent_score">Rent score</option>
            <option value="price_asc">{t.sortPriceAsc}</option>
            <option value="price_desc">{t.sortPriceDesc}</option>
            <option value="psm_gross_asc">{t.sortPsmGrossAsc}</option>
            <option value="psm_gross_desc">{t.sortPsmGrossDesc}</option>
            <option value="psm_living_asc">{t.sortPsmLivingAsc}</option>
            <option value="psm_living_desc">{t.sortPsmLivingDesc}</option>
            <option value="biggest">{t.sortBiggest}</option>
            <option value="smallest">{t.sortSmallest}</option>
          </select>
        </div>
      </div>

      {/* Selected neighborhood indicator */}
      {selectedNeighborhood && (
        <div className="flex items-center justify-between px-4 py-2 bg-blue-50 border-b border-blue-100 text-sm shrink-0">
          <span className="text-blue-700 font-medium truncate">{selectedNeighborhood}</span>
          <button
            onClick={onClearNeighborhood}
            className="text-blue-400 hover:text-blue-600 ml-2 shrink-0 cursor-pointer"
          >✕</button>
        </div>
      )}

      {/* Listing results — independent scroll */}
      <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
        {loading && (
          <div className="text-center text-gray-400 text-sm py-8">{t.loading}</div>
        )}
        {!loading && listings?.length === 0 && (
          <div className="text-center text-gray-400 text-sm py-8">{t.noListings}</div>
        )}

        {/* Pinned highlighted listing from map click */}
        {!loading && highlightedListing && (
          <>
            <div className="relative">
              <ListingCard
                listing={highlightedListing}
                onSelect={onSelectListing}
                highlighted
                reaction={reactionFor?.(highlightedListing)}
                onSetReaction={onSetReaction}
                onClearReaction={onClearReaction}
                preferences={preferences}
                personaId={personaId}
                personaWeights={personaWeights}
                scoreShow={scoreShow}
              />
              <button
                onClick={onClearHighlight}
                className="absolute top-1 right-1 w-5 h-5 flex items-center justify-center rounded-full bg-blue-100 text-blue-500 hover:bg-blue-200 hover:text-blue-700 text-xs cursor-pointer"
              >✕</button>
            </div>

            {/* Similar listings */}
            {similarListings.length > 0 && (
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <button
                  onClick={() => setShowSimilar(v => !v)}
                  className="w-full flex items-center justify-between px-3 py-1.5 bg-gray-50 text-xs text-gray-500 hover:bg-gray-100 cursor-pointer transition-colors"
                >
                  <span className="font-medium">{similarListings.length} similar nearby</span>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    {showSimilar
                      ? <polyline points="6 15 12 9 18 15" />
                      : <polyline points="6 9 12 15 18 9" />}
                  </svg>
                </button>
                {showSimilar && (
                  <div className="flex flex-col gap-1.5 p-2 bg-gray-50/50">
                    {similarListings.map(l => (
                      <div key={l.id} className="relative">
                        <ListingCard
                          listing={l}
                          onSelect={onSelectListing}
                          reaction={reactionFor?.(l)}
                          onSetReaction={onSetReaction}
                          onClearReaction={onClearReaction}
                          preferences={preferences}
                          personaId={personaId}
                personaWeights={personaWeights}
                          scoreShow={scoreShow}
                        />
                        <span className="absolute top-1 right-1 text-[10px] text-gray-400 bg-white/80 rounded px-1">
                          {l._dist < 1 ? `${Math.round(l._dist * 1000)}m` : `${l._dist.toFixed(1)}km`}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {!loading && listings?.slice(0, visibleCount).map(l => {
          if (highlightedListing?.id === l.id) return null
          if (similarListings.some(s => s.id === l.id)) return null
          return (
            <ListingCard
              key={l.id}
              listing={l}
              onSelect={onSelectListing}
              reaction={reactionFor?.(l)}
              onSetReaction={onSetReaction}
              onClearReaction={onClearReaction}
              preferences={preferences}
              personaId={personaId}
              personaWeights={personaWeights}
              scoreShow={scoreShow}
            />
          )
        })}
        {!loading && listings?.length > visibleCount && (
          <button
            onClick={() => setVisibleCount(v => v + PAGE_SIZE)}
            className="py-2 px-4 text-sm text-blue-600 hover:text-blue-800 hover:bg-blue-50 rounded-lg border border-blue-200 transition-colors cursor-pointer"
          >
            Show more ({listings.length - visibleCount} remaining)
          </button>
        )}
      </div>
      </div>
    </div>
  )
}
