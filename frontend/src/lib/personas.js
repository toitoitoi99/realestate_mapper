// Four personas that act as soft lenses on top of the existing map/score data.
// Each persona implies a default listing view (sale vs rent), a set of filter
// defaults, and a weighted blend of existing scores. Used by:
//   - Landing page (tile copy + icons)
//   - Onboarding wizard (pre-fills preferences)
//   - Future: persona-aware ranking in the results view

export const PERSONAS = {
  rental_investor: {
    id: 'rental_investor',
    label: 'Rental investor',
    tagline: 'Buy to let — optimize for yield and demand.',
    description:
      'Find sale listings with strong rental yield, durable demand, and sensible acquisition prices relative to the neighborhood.',
    defaultView: 'sale',
    // Weights applied to existing scores (see scoring_engine.py).
    // Client-side ranking does not need to sum to 1; it just re-orders.
    scoreWeights: {
      yield_gross: 0.5,
      demand_durability: 0.2,
      market_discount: 0.2,
      amenity: 0.1,
    },
    icon: '📈',
  },
  flipper: {
    id: 'flipper',
    label: 'Flipper',
    tagline: 'Buy, renovate, sell — chase underpricing + renovation upside.',
    description:
      'Find sale listings trading below neighborhood comps where the renovation cost is justified by the expected resale margin.',
    defaultView: 'sale',
    scoreWeights: {
      market_discount: 0.5,
      expected_resale: 0.3,
      dev_momentum: 0.2,
    },
    icon: '🔨',
  },
  home_buyer: {
    id: 'home_buyer',
    label: 'Home buyer',
    tagline: 'A place to live — amenities, light, long-term hold.',
    description:
      'Find sale listings matched to lifestyle: neighborhood feel, light, outdoor space, and the style you actually want to live in.',
    defaultView: 'sale',
    scoreWeights: {
      amenity: 0.4,
      light: 0.2,
      outdoor_space: 0.2,
      demand_durability: 0.2,
    },
    icon: '🏡',
  },
  home_renter: {
    id: 'home_renter',
    label: 'Home renter',
    tagline: 'Renting — price, commute, and vibe.',
    description:
      'Find rental listings by price, location, commute, and interior style. Fast decisions with fewer commitments.',
    defaultView: 'rent',
    scoreWeights: {
      amenity: 0.4,
      transit: 0.3,
      light: 0.15,
      outdoor_space: 0.15,
    },
    icon: '🗝️',
  },
}

export const PERSONA_ORDER = [
  'rental_investor',
  'flipper',
  'home_buyer',
  'home_renter',
]

export function getPersona(id) {
  return PERSONAS[id] ?? null
}
