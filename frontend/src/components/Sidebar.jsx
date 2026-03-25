import { useLanguage } from '../LanguageContext'
import FilterPanel from './FilterPanel'
import ListingCard from './ListingCard'
import ListingDetail from './ListingDetail'

export default function Sidebar({
  filters, setFilter, reset,
  listings, loading,
  selectedNeighborhood, onClearNeighborhood,
  selectedListing, onSelectListing,
}) {
  const { t } = useLanguage()

  if (selectedListing) {
    return (
      <div className="w-80 shrink-0 flex flex-col bg-white border-r border-gray-200 overflow-hidden">
        <ListingDetail listing={selectedListing} onBack={() => onSelectListing(null)} />
      </div>
    )
  }

  return (
    <div className="w-80 shrink-0 flex flex-col bg-white border-r border-gray-200 overflow-hidden">
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

      <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
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
  )
}
