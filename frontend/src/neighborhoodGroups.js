// Lisboa's 24 official parishes (freguesias) after the 2012 administrative reorganisation.
// Parish boundaries are fetched at runtime from /api/parishes (CML ArcGIS data).
// Here we define the 5 cultural zone groups and their parish memberships.

export const GROUPS = {
  historic: {
    label: 'Historic & Central',
    color: '#f59e0b',
    fillColor: '#f59e0b',
    neighborhoods: ['Misericórdia', 'Santa Maria Maior', 'São Vicente', 'Santo António'],
  },
  riverside: {
    label: 'Riverside & Western',
    color: '#3b82f6',
    fillColor: '#3b82f6',
    neighborhoods: ['Belém', 'Ajuda', 'Alcântara'],
  },
  uptown: {
    label: 'Uptown & Modern',
    color: '#8b5cf6',
    fillColor: '#8b5cf6',
    neighborhoods: ['Parque das Nações', 'Avenidas Novas', 'Alvalade', 'Areeiro'],
  },
  residential: {
    label: 'Inner Residential',
    color: '#10b981',
    fillColor: '#10b981',
    neighborhoods: ['Campo de Ourique', 'Estrela', 'Campolide', 'Arroios', 'Penha de França', 'Beato'],
  },
  outer: {
    label: 'Outer Districts',
    color: '#6b7280',
    fillColor: '#6b7280',
    neighborhoods: ['Carnide', 'Lumiar', 'Santa Clara', 'Olivais', 'Benfica', 'São Domingos de Benfica', 'Marvila'],
  },
}

// Reverse lookup: parish name → group key
export const PARISH_TO_GROUP = {}
for (const [key, group] of Object.entries(GROUPS)) {
  for (const name of group.neighborhoods) {
    PARISH_TO_GROUP[name] = key
  }
}
