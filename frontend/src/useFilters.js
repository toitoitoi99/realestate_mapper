import { useState, useCallback } from 'react'

const DEFAULT_FILTERS = {
  listing_type: 'sale',
  show_sold: 'active',
  min_price: '',
  max_price: '',
  min_sqm: '',
  max_sqm: '',
  rooms: '',
  neighborhood: '',
  sold_after: '',
  sold_before: '',
  sort_by: 'default',
  limit: 10000,
  min_price_per_sqm: '',
  max_price_per_sqm: '',
  bedrooms: '',
  bathrooms: '',
  floor: '',
  property_type: '',
  condition: '',
  parish: '',
  district: '',
  city: '',
  postal_code: '',
  min_score_pct: 0,
}

export function useFilters() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS)

  const setFilter = useCallback((key, value) => {
    setFilters(prev => ({ ...prev, [key]: value }))
  }, [])

  const reset = useCallback(() => setFilters(DEFAULT_FILTERS), [])

  return { filters, setFilter, reset }
}
