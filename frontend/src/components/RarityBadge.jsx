/**
 * RarityBadge — shows a colour-coded rarity tier for a listing.
 *
 * Tiers (calibrated to observed score range 23–70):
 *   < 35  → nothing (common, no badge)
 *   35–50 → Notable  (slate)
 *   50–60 → Rare     (amber)
 *   ≥ 60  → Unique   (violet)
 *
 * When `showDetail` is true, a tooltip breakdown of the contributing factors is shown.
 */

const FACTOR_LABELS = {
  price_dev:      'Unusual price/m²',
  typology:       'Rare room count',
  size_dev:       'Unusual size',
  condition:      'Condition vs area',
  scarcity:       'Low supply',
  prop_type:      'Rare property type',
  vs_sold:        'Below sold average',
  new_build_prox: 'Near new construction',
}

function getTier(score) {
  if (score == null) return null
  if (score >= 60) return { label: 'Unique',   bg: '#7c3aed', text: '#fff' }
  if (score >= 50) return { label: 'Rare',     bg: '#d97706', text: '#fff' }
  if (score >= 35) return { label: 'Notable',  bg: '#475569', text: '#fff' }
  return null
}

// Top 3 contributing factors, sorted by weighted contribution
function topFactors(factors) {
  if (!factors) return []
  const WEIGHTS = {
    price_dev: 0.25, typology: 0.15, size_dev: 0.10,
    condition: 0.20, scarcity: 0.10, prop_type: 0.05,
    vs_sold: 0.10,   new_build_prox: 0.05,
  }
  return Object.entries(factors)
    .map(([k, v]) => ({ key: k, contribution: v * (WEIGHTS[k] ?? 0) }))
    .filter(x => x.contribution > 0.02)
    .sort((a, b) => b.contribution - a.contribution)
    .slice(0, 3)
}

export default function RarityBadge({ score, factors, compact = false }) {
  const tier = getTier(score)
  if (!tier) return null

  const parsedFactors = typeof factors === 'string' ? (() => { try { return JSON.parse(factors) } catch { return null } })() : factors
  const top = topFactors(parsedFactors)

  if (compact) {
    return (
      <span
        title={`Rarity ${score?.toFixed(0)}/100\n${top.map(f => FACTOR_LABELS[f.key]).join(' · ')}`}
        style={{
          background: tier.bg, color: tier.text,
          fontSize: '10px', fontWeight: 700,
          padding: '1px 5px', borderRadius: '4px',
          letterSpacing: '0.04em', lineHeight: 1.4,
          display: 'inline-block', flexShrink: 0,
        }}
      >
        {tier.label}
      </span>
    )
  }

  return (
    <div style={{ marginTop: '4px' }}>
      <span
        style={{
          background: tier.bg, color: tier.text,
          fontSize: '10px', fontWeight: 700,
          padding: '2px 6px', borderRadius: '4px',
          letterSpacing: '0.04em', display: 'inline-block',
        }}
      >
        {tier.label} · {score?.toFixed(0)}/100
      </span>
      {top.length > 0 && (
        <div style={{ fontSize: '10px', color: '#9ca3af', marginTop: '2px' }}>
          {top.map(f => FACTOR_LABELS[f.key]).join(' · ')}
        </div>
      )}
    </div>
  )
}
