const SOURCE_META = {
  idealista:  { label: 'idealista',  color: '#1BC744' },
  imovirtual: { label: 'imovirtual', color: '#6B21A8' },
  era:        { label: 'ERA',        color: '#D32F2F' },
  casa_sapo:  { label: 'CASA SAPO',  color: '#FF6F00' },
  olx:        { label: 'OLX',        color: '#002F34' },
  remax:      { label: 'RE/MAX',     color: '#003DA5' },
}

export default function SourceLogo({ source, size = 'sm' }) {
  const meta = SOURCE_META[source] || { label: source, color: '#6B7280' }
  const h = size === 'sm' ? 'h-5' : 'h-6'

  return (
    <img
      src={`/logos/${source}.svg`}
      alt={meta.label}
      className={`${h} w-auto`}
      onError={(e) => {
        // Fallback to a colored text span if SVG fails
        const span = document.createElement('span')
        span.textContent = meta.label
        span.style.cssText = `background:${meta.color};color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;white-space:nowrap;`
        e.target.replaceWith(span)
      }}
    />
  )
}
