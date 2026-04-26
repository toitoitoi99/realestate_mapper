// Preference vocabulary + helpers shared between the onboarding wizard and
// the listings query layer. The shape stored on profiles.preferences is:
//
// {
//   budget:  { min: number|null, max: number|null },   // EUR
//   size:    { min: number|null, max: number|null },   // m² (living)
//   bedrooms_min: number|null,                          // 0..4 (4 means 4+)
//   style: string|null,                                 // one of STYLE_OPTIONS
//   outdoor_required: boolean,                          // require any outdoor space
//   max_renovation: string|null                         // turnkey|cosmetic|full_renovation
// }

export const EMPTY_PREFERENCES = {
  budget: { min: null, max: null },
  size: { min: null, max: null },
  bedrooms_min: null,
  property_type: null,   // 'apartment' | 'house' | null (either)
  style: null,
  outdoor_required: false,
  max_renovation: null,
  // When true, only show listings the photo-tagger has already labelled —
  // hides untagged ones rather than letting them through. Useful once tag
  // coverage is high (today only ~17% of sales have style tags, so off
  // by default).
  strict_tags: false,
}

export const STYLE_OPTIONS = [
  { id: 'modern',       label: 'Modern' },
  { id: 'minimalist',   label: 'Minimalist' },
  { id: 'scandinavian', label: 'Scandinavian' },
  { id: 'classic',      label: 'Classic' },
  { id: 'traditional',  label: 'Traditional' },
  { id: 'industrial',   label: 'Industrial' },
  { id: 'rustic',       label: 'Rustic' },
  { id: 'eclectic',     label: 'Eclectic' },
]

export const RENOVATION_OPTIONS = [
  { id: 'turnkey',         label: 'Turnkey only',  hint: 'Move-in ready, no work' },
  { id: 'cosmetic',        label: 'Cosmetic OK',   hint: 'Paint and small fixes are fine' },
  { id: 'full_renovation', label: 'Anything',      hint: 'Open to gut renovations' },
]

export const BEDROOM_OPTIONS = [
  { id: 0, label: 'Studio+' },
  { id: 1, label: '1+' },
  { id: 2, label: '2+' },
  { id: 3, label: '3+' },
  { id: 4, label: '4+' },
]

// Convert preferences to the URL params expected by the backend listings API.
// Untouched fields are omitted so the API doesn't apply them.
export function preferencesToFilters(prefs) {
  const out = {}
  if (!prefs) return out
  if (prefs.budget?.min != null) out.min_price = prefs.budget.min
  if (prefs.budget?.max != null) out.max_price = prefs.budget.max
  if (prefs.size?.min != null)   out.min_sqm = prefs.size.min
  if (prefs.size?.max != null)   out.max_sqm = prefs.size.max
  if (prefs.bedrooms_min != null) out.bedrooms_min = prefs.bedrooms_min
  if (prefs.style)                out.style_primary = prefs.style
  if (prefs.outdoor_required)     out.outdoor_required = true
  if (prefs.max_renovation)       out.max_renovation = prefs.max_renovation
  if (prefs.strict_tags)          out.strict_tags = true
  return out
}
