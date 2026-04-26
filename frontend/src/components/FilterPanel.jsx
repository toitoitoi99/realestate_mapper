import { useState } from 'react'
import { useLanguage } from '../LanguageContext'

function getActiveChips(filters, t) {
  const chips = []
  if (filters.min_price) chips.push({ key: 'min_price', label: `≥ €${Number(filters.min_price).toLocaleString('pt-PT')}` })
  if (filters.max_price) chips.push({ key: 'max_price', label: `≤ €${Number(filters.max_price).toLocaleString('pt-PT')}` })
  if (filters.min_sqm) chips.push({ key: 'min_sqm', label: `≥ ${filters.min_sqm} m²` })
  if (filters.max_sqm) chips.push({ key: 'max_sqm', label: `≤ ${filters.max_sqm} m²` })
  if (filters.show_sold !== 'both') chips.push({ key: 'show_sold', label: filters.show_sold === 'sold' ? t.soldOnly : t.activeOnly, value: 'both' })
  if (filters.min_price_per_sqm) chips.push({ key: 'min_price_per_sqm', label: `≥ €${Number(filters.min_price_per_sqm).toLocaleString('pt-PT')}/m²` })
  if (filters.max_price_per_sqm) chips.push({ key: 'max_price_per_sqm', label: `≤ €${Number(filters.max_price_per_sqm).toLocaleString('pt-PT')}/m²` })
  if (filters.bedrooms) chips.push({ key: 'bedrooms', label: `${filters.bedrooms === '0' ? 'Studio' : filters.bedrooms + ' bed'}` })
  if (filters.bathrooms) chips.push({ key: 'bathrooms', label: `${filters.bathrooms} ${t.bathrooms}` })
  if (filters.floor) chips.push({ key: 'floor', label: `${t.floor}: ${filters.floor}` })
  if (filters.property_type) chips.push({ key: 'property_type', label: filters.property_type })
  if (filters.condition) chips.push({ key: 'condition', label: filters.condition })
  if (filters.min_flip_score > 0) chips.push({ key: 'min_flip_score', label: `Flip ≥ ${filters.min_flip_score}`, value: 0 })
  if (filters.min_rent_score > 0) chips.push({ key: 'min_rent_score', label: `Rent ≥ ${filters.min_rent_score}`, value: 0 })
  if (filters.parish) chips.push({ key: 'parish', label: filters.parish })
  if (filters.district) chips.push({ key: 'district', label: filters.district })
  if (filters.city) chips.push({ key: 'city', label: filters.city })
  if (filters.postal_code) chips.push({ key: 'postal_code', label: filters.postal_code })
  if (filters.grant_eligible) chips.push({ key: 'grant_eligible', label: 'Grant eligible', value: '' })
  return chips
}

function countMoreFiltersActive(filters) {
  let n = 0
  if (filters.min_sqm || filters.max_sqm) n++
  if (filters.min_price_per_sqm || filters.max_price_per_sqm) n++
  if (filters.min_flip_score > 0) n++
  if (filters.min_rent_score > 0) n++
  if (filters.grant_eligible) n++
  if (filters.property_type) n++
  if (filters.condition) n++
  if (filters.floor) n++
  if (filters.bathrooms) n++
  if (filters.parish || filters.district || filters.city || filters.postal_code) n++
  return n
}

const BEDROOM_CHIPS = [
  { value: '0', label: 'Studio' },
  { value: '1', label: '1' },
  { value: '2', label: '2' },
  { value: '3', label: '3' },
  { value: '4', label: '4' },
  { value: '5', label: '5+' },
]

export default function FilterPanel({ filters, setFilter, reset }) {
  const { t } = useLanguage()
  const [moreOpen, setMoreOpen] = useState(false)

  const chips = getActiveChips(filters, t)
  const moreCount = countMoreFiltersActive(filters)

  const removeChip = (chip) => {
    setFilter(chip.key, chip.value !== undefined ? chip.value : '')
  }

  const numInput = (key, placeholder) => (
    <input
      type="number"
      placeholder={placeholder}
      value={filters[key]}
      onChange={e => setFilter(key, e.target.value)}
      className="w-full border border-gray-200 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
    />
  )

  const textInput = (key, placeholder) => (
    <input
      type="text"
      placeholder={placeholder}
      value={filters[key]}
      onChange={e => setFilter(key, e.target.value)}
      className="w-full border border-gray-200 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
    />
  )

  const selectInput = (key, label, options) => (
    <div>
      <label className="text-xs text-gray-500 mb-1 block">{label}</label>
      <select
        value={filters[key]}
        onChange={e => setFilter(key, e.target.value)}
        className="w-full border border-gray-200 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
      >
        <option value="">{t.any}</option>
        {options.map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </select>
    </div>
  )

  return (
    <div className="flex flex-col gap-2.5 p-3">

      {/* Active chips + reset */}
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1 items-center">
          {chips.map(chip => (
            <span
              key={chip.key}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-primary-tint text-primary text-xs border border-primary-border"
            >
              {chip.label}
              <button onClick={() => removeChip(chip)} className="leading-none hover:text-primary-dark cursor-pointer">&times;</button>
            </span>
          ))}
          <button onClick={reset} className="text-xs text-gray-400 hover:text-gray-600 ml-auto cursor-pointer">Reset</button>
        </div>
      )}

      {/* Listing type */}
      <div className="flex rounded overflow-hidden border border-gray-200 text-sm">
        {[['sale', t.buy], ['rent', t.rent], ['all', t.all]].map(([type, label]) => (
          <button key={type} onClick={() => setFilter('listing_type', type)}
            className={`flex-1 py-1.5 text-sm font-medium transition-colors cursor-pointer ${
              filters.listing_type === type ? 'bg-primary text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
            }`}>
            {label}
          </button>
        ))}
      </div>

      {/* Bedrooms (promoted from Advanced) */}
      <div>
        <label className="text-xs text-gray-500 mb-1 block">{t.bedrooms}</label>
        <div className="flex gap-1">
          {BEDROOM_CHIPS.map(({ value, label }) => (
            <button
              key={value}
              onClick={() => setFilter('bedrooms', filters.bedrooms === value ? '' : value)}
              className={`flex-1 py-1 text-xs rounded border transition-colors cursor-pointer ${
                filters.bedrooms === value
                  ? 'bg-primary text-white border-primary'
                  : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Price — compact single row */}
      <div>
        <label className="text-xs text-gray-500 mb-1 block">
          {filters.listing_type === 'rent' ? t.monthlyRent : t.price} (€)
        </label>
        <div className="flex gap-1.5">
          {numInput('min_price', 'Min')}
          {numInput('max_price', 'Max')}
        </div>
      </div>

      {/* Status */}
      <div className="flex rounded-lg border border-gray-200 overflow-hidden">
        {[
          { value: 'active', label: t.activeOnly },
          { value: 'sold',   label: t.soldOnly },
          { value: 'both',   label: t.bothStatus },
        ].map(opt => (
          <button
            key={opt.value}
            onClick={() => setFilter('show_sold', opt.value)}
            className={`flex-1 text-xs py-1.5 font-medium transition-colors cursor-pointer ${
              filters.show_sold === opt.value
                ? opt.value === 'sold'
                  ? 'bg-amber-500 text-white'
                  : opt.value === 'both'
                  ? 'bg-gray-700 text-white'
                  : 'bg-primary text-white'
                : 'bg-white text-gray-500 hover:bg-gray-50'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Sold date range — only when relevant */}
      {(filters.show_sold === 'sold' || filters.show_sold === 'both') && (
        <div className="flex gap-1.5">
          <input type="date" value={filters.sold_after}
            onChange={e => setFilter('sold_after', e.target.value)}
            className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
          />
          <input type="date" value={filters.sold_before}
            onChange={e => setFilter('sold_before', e.target.value)}
            className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
          />
        </div>
      )}

      {/* More filters toggle */}
      <button
        onClick={() => setMoreOpen(v => !v)}
        className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700 cursor-pointer select-none w-full"
      >
        <span className="flex-1 text-left font-medium">{t.advancedFilters}</span>
        {moreCount > 0 && (
          <span className="bg-primary text-white text-[10px] rounded-full px-1.5 py-0.5 leading-none">{moreCount}</span>
        )}
        <span className="text-[10px]">{moreOpen ? '▲' : '▼'}</span>
      </button>

      {/* More filters — collapsible */}
      {moreOpen && (
        <div className="flex flex-col gap-2.5 border-t border-gray-100 pt-2.5">

          {/* Size */}
          <div>
            <label className="text-xs text-gray-500 mb-1 block">{t.size} (m²)</label>
            <div className="flex gap-1.5">
              {numInput('min_sqm', 'Min')}
              {numInput('max_sqm', 'Max')}
            </div>
          </div>

          {/* Flip score */}
          <div>
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>Flip score</span>
              <span className="font-medium text-primary">{!filters.min_flip_score ? 'All' : `≥ ${filters.min_flip_score}`}</span>
            </div>
            <input type="range" min="0" max="100" step="5"
              value={filters.min_flip_score || 0}
              onChange={e => setFilter('min_flip_score', Number(e.target.value))}
              className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-primary"
            />
          </div>

          {/* Rent score */}
          <div>
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>Rent score</span>
              <span className="font-medium text-emerald-600">{!filters.min_rent_score ? 'All' : `≥ ${filters.min_rent_score}`}</span>
            </div>
            <input type="range" min="0" max="100" step="5"
              value={filters.min_rent_score || 0}
              onChange={e => setFilter('min_rent_score', Number(e.target.value))}
              className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-emerald-500"
            />
          </div>

          {/* Grant eligible */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox"
              checked={!!filters.grant_eligible}
              onChange={e => setFilter('grant_eligible', e.target.checked || '')}
              className="rounded border-gray-300 text-emerald-500 focus:ring-emerald-400"
            />
            <span className="text-xs text-gray-600">Grant eligible only</span>
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block ml-auto" />
          </label>

          {/* Price/sqm */}
          <div>
            <label className="text-xs text-gray-500 mb-1 block">{t.pricePerSqm} (€/m²)</label>
            <div className="flex gap-1.5">
              {numInput('min_price_per_sqm', 'Min')}
              {numInput('max_price_per_sqm', 'Max')}
            </div>
          </div>

          {/* Property type + condition */}
          <div className="grid grid-cols-2 gap-2">
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

          {/* Bathrooms + Floor */}
          <div className="grid grid-cols-2 gap-2">
            {selectInput('bathrooms', t.bathrooms, [
              ['1', '1'], ['2', '2'], ['3', '3'], ['4', '4+'],
            ])}
            <div>
              <label className="text-xs text-gray-500 mb-1 block">{t.floor}</label>
              {textInput('floor', 'e.g. 3, RC')}
            </div>
          </div>

          {/* Location */}
          <div className="grid grid-cols-2 gap-2">
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

      {/* Reset — shown in header area when no chips are visible */}
      {chips.length === 0 && (
        <button onClick={reset} className="text-xs text-gray-400 hover:text-gray-600 text-right cursor-pointer">Reset</button>
      )}

    </div>
  )
}
