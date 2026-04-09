/**
 * GrantBadge — shows when a listing is in a low-density territory
 * eligible for Portuguese renovation grants (up to 60-80% fundo perdido).
 */

const PROGRAMS = [
  { name: 'Crescer com o Turismo', rate: '60–80%', use: 'Tourism rehabilitation' },
  { name: 'Base Territorial', rate: '50–60%', use: 'Construction/remodeling' },
]

export default function GrantBadge({ eligible, compact = false }) {
  if (!eligible) return null

  const tooltip = PROGRAMS.map(p => `${p.name}: ${p.rate} (${p.use})`).join('\n')

  if (compact) {
    return (
      <span
        title={`Grant eligible\n${tooltip}`}
        style={{
          background: '#059669', color: '#fff',
          fontSize: '10px', fontWeight: 700,
          padding: '1px 5px', borderRadius: '4px',
          letterSpacing: '0.04em', lineHeight: 1.4,
          display: 'inline-block', flexShrink: 0,
        }}
      >
        Grant 60%
      </span>
    )
  }

  return (
    <div style={{ marginTop: '4px' }}>
      <span
        title={tooltip}
        style={{
          background: '#059669', color: '#fff',
          fontSize: '10px', fontWeight: 700,
          padding: '2px 6px', borderRadius: '4px',
          letterSpacing: '0.04em', display: 'inline-block',
        }}
      >
        Grant Eligible · up to 80%
      </span>
      <div style={{ fontSize: '10px', color: '#9ca3af', marginTop: '2px' }}>
        Low-density territory · Fundo perdido
      </div>
    </div>
  )
}
