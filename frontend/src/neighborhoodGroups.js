// Dynamic neighbourhood grouping: groups parishes by municipality.
// Groups are derived at runtime from parishFeatures (GeoJSON data from /api/parishes).

// Color palette for municipalities (cycles if more than available)
const PALETTE = [
  '#f59e0b', '#3b82f6', '#8b5cf6', '#10b981', '#ef4444',
  '#06b6d4', '#f97316', '#ec4899', '#14b8a6', '#a855f7',
  '#84cc16', '#6366f1', '#d97706', '#0ea5e9', '#e11d48',
  '#22d3ee', '#facc15', '#4ade80',
]

/**
 * Build municipality-based groups from parish GeoJSON features.
 * @param {Array} features - GeoJSON Feature[] with properties.municipality
 * @returns {{ groups: Object, parishToGroup: Object }}
 */
export function buildGroups(features) {
  if (!features?.length) return { groups: {}, parishToGroup: {} }

  // Collect unique municipalities preserving insertion order
  const munMap = {}
  for (const f of features) {
    const mun = f.properties?.municipality
    if (!mun) continue
    if (!munMap[mun]) munMap[mun] = []
    munMap[mun].push(f.properties.name)
  }

  const groups = {}
  const parishToGroup = {}
  const munNames = Object.keys(munMap)

  munNames.forEach((mun, i) => {
    const key = mun  // use municipality name as group key
    const color = PALETTE[i % PALETTE.length]
    groups[key] = {
      label: mun,
      color,
      fillColor: color,
      neighborhoods: munMap[mun],
    }
    for (const parish of munMap[mun]) {
      parishToGroup[parish] = key
    }
  })

  return { groups, parishToGroup }
}
