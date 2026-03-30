import { useState } from 'react'
import { useLanguage } from '../LanguageContext'
import FilterPanel from './FilterPanel'
import ListingCard from './ListingCard'
import ListingDetail from './ListingDetail'

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
}) {
  const { t } = useLanguage()
  const [collapsed, setCollapsed] = useState(false)

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
      <div className="relative shrink-0 w-0 border-r border-gray-200">
        {toggleBtn}
      </div>
    )
  }

  if (selectedListing) {
    return (
      <div className="relative w-80 shrink-0 flex flex-col bg-white border-r border-gray-200 overflow-hidden">
        {toggleBtn}
        <ListingDetail listing={selectedListing} onBack={() => onSelectListing(null)} />
      </div>
    )
  }

  return (
    <div className="relative w-80 shrink-0 flex flex-col bg-white border-r border-gray-200 min-h-0 overflow-hidden">
      {toggleBtn}
      <div className="flex-1 overflow-y-auto">
      <FilterPanel
        filters={filters}
        setFilter={setFilter}
        reset={reset}
        listingCount={listings?.length}
      />

      {selectedNeighborhood && (
        <div className="flex items-center justify-between px-4 py-2 bg-blue-50 border-b border-blue-100 text-sm">
          <span className="text-blue-700 font-medium truncate">{selectedNeighborhood}</span>
          <button
            onClick={onClearNeighborhood}
            className="text-blue-400 hover:text-blue-600 ml-2 shrink-0 cursor-pointer"
          >✕</button>
        </div>
      )}

      <div className="p-3 flex flex-col gap-2">
        {loading && (
          <div className="text-center text-gray-400 text-sm py-8">{t.loading}</div>
        )}
        {!loading && listings?.length === 0 && (
          <div className="text-center text-gray-400 text-sm py-8">{t.noListings}</div>
        )}
        {!loading && listings?.map(l => (
          <ListingCard key={l.id} listing={l} onSelect={onSelectListing} />
        ))}
      </div>
      </div>
    </div>
  )
}
