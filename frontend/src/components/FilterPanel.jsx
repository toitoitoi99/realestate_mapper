import { useLanguage } from '../LanguageContext'

export default function FilterPanel({ filters, setFilter, reset, listingCount }) {
  const { t } = useLanguage()

  const input = (key, placeholder) => (
    <input
      type="number"
      placeholder={placeholder}
      value={filters[key]}
      onChange={e => setFilter(key, e.target.value)}
      className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
    />
  )

  return (
    <div className="flex flex-col gap-4 p-4 border-b border-gray-200">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-700">{t.filters}</span>
        <button onClick={reset} className="text-xs text-blue-500 hover:underline cursor-pointer">{t.reset}</button>
      </div>

      <div>
        <label className="text-xs text-gray-500 mb-1 block">{t.listingType}</label>
        <div className="flex rounded overflow-hidden border border-gray-200 text-sm">
          {[['sale', t.buy], ['rent', t.rent]].map(([type, label]) => (
            <button key={type} onClick={() => setFilter('listing_type', type)}
              className={`flex-1 py-1.5 ${filters.listing_type === type
                ? 'bg-blue-500 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="text-xs text-gray-500 mb-1 block">
          {filters.listing_type === 'rent' ? t.monthlyRent : t.price}
        </label>
        <div className="flex gap-2">
          {input('min_price', 'Min')}
          {input('max_price', 'Max')}
        </div>
      </div>

      <div>
        <label className="text-xs text-gray-500 mb-1 block">{t.size}</label>
        <div className="flex gap-2">
          {input('min_sqm', 'Min')}
          {input('max_sqm', 'Max')}
        </div>
      </div>

      <div>
        <label className="text-xs text-gray-500 mb-1 block">{t.rooms}</label>
        <select
          value={filters.rooms}
          onChange={e => setFilter('rooms', e.target.value)}
          className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
        >
          <option value="">{t.any}</option>
          {[0, 1, 2, 3, 4, 5].map(r => (
            <option key={r} value={r}>T{r}{r === 5 ? '+' : ''}</option>
          ))}
        </select>
      </div>

      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={filters.show_sold}
          onChange={e => setFilter('show_sold', e.target.checked)}
          className="rounded border-gray-300 text-amber-500 focus:ring-amber-400"
        />
        <span className="text-xs text-gray-600">{t.showSold}</span>
        <span className="w-3 h-3 rounded-full bg-amber-400 inline-block ml-auto" />
      </label>

      {filters.show_sold && (
        <div>
          <label className="text-xs text-gray-500 mb-1 block">{t.soldDateRange}</label>
          <div className="flex gap-2">
            <input
              type="date"
              value={filters.sold_after}
              onChange={e => setFilter('sold_after', e.target.value)}
              className="flex-1 border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-amber-400"
            />
            <input
              type="date"
              value={filters.sold_before}
              onChange={e => setFilter('sold_before', e.target.value)}
              className="flex-1 border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-amber-400"
            />
          </div>
        </div>
      )}

      <div>
        <label className="text-xs text-gray-500 mb-1 block">{t.sortBy}</label>
        <select
          value={filters.sort_by}
          onChange={e => setFilter('sort_by', e.target.value)}
          className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
        >
          <option value="default">{t.sortDefault}</option>
          <option value="rarity">{t.sortRarity}</option>
          <option value="price_asc">{t.sortPriceAsc}</option>
          <option value="price_desc">{t.sortPriceDesc}</option>
        </select>
      </div>

      <div className="text-xs text-gray-400 pt-1">
        {listingCount != null ? t.listingsCount(listingCount) : ''}
      </div>
    </div>
  )
}
