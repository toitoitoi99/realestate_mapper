import { useState, useCallback } from 'react'

const DEFAULT_FILTERS = {
  listing_type: 'sale',
  show_sold: 'both',
  min_price: '',
  max_price: '',
  min_sqm: '',
  max_sqm: '',
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
  min_flip_score: 0,
  min_rent_score: 0,
  grant_eligible: '',
}

export function useFilters() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS)

  const setFilter = useCallback((key, value) => {
    setFilters(prev => ({ ...prev, [key]: value }))
  }, [])

  const reset = useCallback(() => setFilters(DEFAULT_FILTERS), [])

  // Replace the whole filter object — used by saved searches to swap an
  // entire stored filter set in one go. Unspecified keys fall back to defaults.
  const replaceAll = useCallback((next) => {
    setFilters({ ...DEFAULT_FILTERS, ...(next || {}) })
  }, [])

  return { filters, setFilter, reset, replaceAll }
}
