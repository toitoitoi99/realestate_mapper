export const CATEGORIES = {
  new_construction: {
    label: 'New construction',
    color: '#dc2626',
    stroke: '#991b1b',
    defaultVisible: true,
  },
  government: {
    label: 'Government project',
    color: '#7c3aed',
    stroke: '#4c1d95',
    defaultVisible: true,
  },
  extension: {
    label: 'Extension / Reconstruction',
    color: '#ea580c',
    stroke: '#9a3412',
    defaultVisible: true,
  },
  demolition: {
    label: 'Demolition',
    color: '#374151',
    stroke: '#111827',
    defaultVisible: true,
  },
  planning: {
    label: 'Planning intent',
    color: '#2563eb',
    stroke: '#1e3a8a',
    defaultVisible: true,
  },
  conservation: {
    label: 'Conservation / Maintenance',
    color: '#0891b2',
    stroke: '#164e63',
    defaultVisible: true,
  },
  alteration: {
    label: 'Alteration',
    color: '#9ca3af',
    stroke: '#6b7280',
    defaultVisible: true,
  },
  unidentified: {
    label: 'Unidentified',
    color: '#e5e7eb',
    stroke: '#d1d5db',
    defaultVisible: false,
  },
}

export function getCategory(operation) {
  if (!operation) return 'unidentified'
  const op = operation.toLowerCase()

  if (op.includes('construção nova') || op === 'construção nova') return 'new_construction'
  if (op.startsWith('construção'))                                  return 'new_construction'
  if (op.includes('adm. pública') || op.includes('adm publica'))   return 'government'
  if (op.includes('ampliação') || op.includes('reconstrução'))      return 'extension'
  if (op.includes('demolição'))                                      return 'demolition'
  if (op.includes('informação prévia'))                              return 'planning'
  if (op.includes('conservação'))                                    return 'conservation'
  if (op.includes('não identificada') || op.includes('nao identificada')) return 'unidentified'
  return 'alteration'
}
