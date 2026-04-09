import { useState } from 'react'
import { useLanguage } from '../LanguageContext'

// Active filter chip labels
function getActiveChips(filters, t) {
  const chips = []
  if (filters.min_price) chips.push({ key: 'min_price', label: `≥ €${Number(filters.min_price).toLocaleString('pt-PT')}` })
  if (filters.max_price) chips.push({ key: 'max_price', label: `≤ €${Number(filters.max_price).toLocaleString('pt-PT')}` })
  if (filters.min_sqm) chips.push({ key: 'min_sqm', label: `≥ ${filters.min_sqm} m²` })
  if (filters.max_sqm) chips.push({ key: 'max_sqm', label: `≤ ${filters.max_sqm} m²` })
  if (filters.rooms) chips.push({ key: 'rooms', label: `T${filters.rooms}${filters.rooms === '5' ? '+' : ''}` })
  if (filters.show_sold) chips.push({ key: 'show_sold', label: t.showSold, value: false })
  if (filters.min_price_per_sqm) chips.push({ key: 'min_price_per_sqm', label: `≥ €${Number(filters.min_price_per_sqm).toLocaleString('pt-PT')}/m²` })
  if (filters.max_price_per_sqm) chips.push({ key: 'max_price_per_sqm', label: `≤ €${Number(filters.max_price_per_sqm).toLocaleString('pt-PT')}/m²` })
  if (filters.bedrooms) chips.push({ key: 'bedrooms', label: `${filters.bedrooms} ${t.bedrooms}` })
  if (filters.bathrooms) chips.push({ key: 'bathrooms', label: `${filters.bathrooms} ${t.bathrooms}` })
  if (filters.floor) chips.push({ key: 'floor', label: `${t.floor}: ${filters.floor}` })
  if (filters.property_type) chips.push({ key: 'property_type', label: filters.property_type })
  if (filters.condition) chips.push({ key: 'condition', label: filters.condition })
  if (filters.min_score_pct > 0) chips.push({ key: 'min_score_pct', label: `Top ${100 - filters.min_score_pct}%`, value: 0 })
  if (filters.parish) chips.push({ key: 'parish', label: filters.parish })
  if (filters.district) chips.push({ key: 'district', label: filters.district })
  if (filters.city) chips.push({ key: 'city', label: filters.city })
  if (filters.postal_code) chips.push({ key: 'postal_code', label: filters.postal_code })
  if (filters.grant_eligible) chips.push({ key: 'grant_eligible', label: 'Grant eligible', value: '' })
  return chips
}

export default function FilterPanel({ filters, setFilter, reset }) {
  const { t } = useLanguage()
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const chips = getActiveChips(filters, t)

  const removeChip = (chip) => {
    const val = chip.value !== undefined ? chip.value : ''
    setFilter(chip.key, val)
  }

  const input = (key, placeholder) => (
    <input
      type="number"
      placeholder={placeholder}
      value={filters[key]}
      onChange={e => setFilter(key, e.target.value)}
      className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
    />
  )

  const textInput = (key, placeholder) => (
    <input
      type="text"
      placeholder={placeholder}
      value={filters[key]}
      onChange={e => setFilter(key, e.target.value)}
      className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
    />
  )

  const selectInput = (key, label, options) => (
    <div>
      <label className="text-xs text-gray-500 mb-1 block">{label}</label>
      <select
        value={filters[key]}
        onChange={e => setFilter(key, e.target.value)}
        className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
      >
        <option value="">{t.any}</option>
        {options.map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </select>
    </div>
  )

  return (
    <div className="flex flex-col gap-3 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-700">{t.filters}</span>
        <button onClick={reset} className="text-xs text-blue-500 hover:underline cursor-pointer">{t.reset}</button>
      </div>

      {/* Active filter chips */}
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map(chip => (
            <span
              key={chip.key}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 text-xs border border-blue-200"
            >
              {chip.label}
              <button
                onClick={() => removeChip(chip)}
                className="text-blue-400 hover:text-blue-600 cursor-pointer leading-none"
              >&times;</button>
            </span>
          ))}
        </div>
      )}

      {/* Section: Search type */}
      <div>
        <label className="text-xs text-gray-500 mb-1 block">{t.listingType}</label>
        <div className="flex rounded overflow-hidden border border-gray-200 text-sm">
          {[['sale', t.buy], ['rent', t.rent], ['all', t.all]].map(([type, label]) => (
            <button key={type} onClick={() => setFilter('listing_type', type)}
              className={`flex-1 py-1.5 ${filters.listing_type === type
                ? 'bg-blue-500 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Section: Price & Size */}
      <div className="flex flex-col gap-3 bg-gray-50 rounded-lg p-3 -mx-1">
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
      </div>

      {/* Section: Property basics */}
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

      {/* Show sold toggle */}
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

      {/* Grant eligible toggle */}
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={!!filters.grant_eligible}
          onChange={e => setFilter('grant_eligible', e.target.checked || '')}
          className="rounded border-gray-300 text-emerald-500 focus:ring-emerald-400"
        />
        <span className="text-xs text-gray-600">Grant eligible only</span>
        <span className="w-3 h-3 rounded-full bg-emerald-500 inline-block ml-auto" />
      </label>

      {/* Deal Score percentile slider */}
      {filters.listing_type !== 'rent' && (
        <div className="bg-gray-50 rounded-lg p-3 -mx-1">
          <label className="text-xs text-gray-500 mb-2 block">{t.dealScore}</label>
          <input
            type="range"
            min="0"
            max="90"
            step="5"
            value={filters.min_score_pct}
            onChange={e => setFilter('min_score_pct', Number(e.target.value))}
            className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-blue-500"
          />
          <div className="flex justify-between text-[10px] text-gray-400 mt-1">
            <span>{t.dealScoreAll}</span>
            <span className="font-medium text-blue-600">
              {filters.min_score_pct === 0 ? t.dealScoreAll : t.dealScoreTop(100 - filters.min_score_pct)}
            </span>
          </div>
        </div>
      )}

      {/* Advanced filters — collapsible */}
      <div
        className="flex items-center justify-between cursor-pointer select-none"
        onClick={() => setAdvancedOpen(v => !v)}
      >
        <span className="text-xs font-medium text-gray-600">{t.advancedFilters}</span>
        <span className="text-gray-400 text-[10px] ml-2">{advancedOpen ? '▲' : '▼'}</span>
      </div>

      {advancedOpen && (
        <div className="flex flex-col gap-3 bg-gray-50 rounded-lg p-3 -mx-1">
          {/* Price per sqm */}
          <div>
            <label className="text-xs text-gray-500 mb-1 block">{t.pricePerSqm}</label>
            <div className="flex gap-2">
              {input('min_price_per_sqm', 'Min')}
              {input('max_price_per_sqm', 'Max')}
            </div>
          </div>

          {/* Property details — 2-column grid */}
          <div className="grid grid-cols-2 gap-3">
            {selectInput('bedrooms', t.bedrooms, [
              ['1', '1'], ['2', '2'], ['3', '3'], ['4', '4'], ['5', '5+'],
            ])}

            {selectInput('bathrooms', t.bathrooms, [
              ['1', '1'], ['2', '2'], ['3', '3'], ['4', '4+'],
            ])}

            {selectInput('property_type', t.propertyType, [
              ['apartment', 'Apartment'],
              ['house', 'House'],
              ['studio', 'Studio'],
            ])}

            {selectInput('condition', t.condition, [
              ['new', 'New'],
              ['used', 'Used'],
              ['renovated', 'Renovated'],
            ])}
          </div>

          <div>
            <label className="text-xs text-gray-500 mb-1 block">{t.floor}</label>
            {textInput('floor', 'e.g. 3, RC')}
          </div>

          {/* Location — 2-column grid */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-gray-500 mb-1 block">{t.parish}</label>
              {textInput('parish', t.parish)}
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">{t.district}</label>
              {textInput('district', t.district)}
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">{t.city}</label>
              {textInput('city', t.city)}
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">{t.postalCode}</label>
              {textInput('postal_code', t.postalCode)}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
