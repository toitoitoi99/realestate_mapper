// Full per-persona breakdown shown on the listing detail view. Reads
// flip_factors.bundle (already fetched by /api/listings/:id) and shows
// the math behind the small KPI badge that appears on the listing card.
//
// Returns null when no persona is set or the listing has no usable data.

const MORTGAGE_RATE = 0.035          // indicative annual rate
const MORTGAGE_TERM_YEARS = 30
const MORTGAGE_LTV = 0.80
const MAINTENANCE_PCT_OF_RENT = 0.10  // simple monthly reserve
const FLIP_TXN_COST_PCT = 0.05        // IMT + legal + agent rough share

function readBundle(listing) {
  const raw = listing?.flip_factors
  if (!raw) return null
  try {
    return JSON.parse(raw).bundle || null
  } catch {
    return null
  }
}

function fmtEur(n) {
  if (n == null || !Number.isFinite(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  return `${sign}€${Math.round(abs).toLocaleString('pt-PT')}`
}

function fmtEurShort(n) {
  if (n == null || !Number.isFinite(n)) return '—'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}€${(abs / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000)     return `${sign}€${Math.round(abs / 1_000)}k`
  return `${sign}€${Math.round(abs)}`
}

function monthlyMortgage(principal, annualRate, years) {
  if (!principal) return null
  const r = annualRate / 12
  const n = years * 12
  return principal * r * Math.pow(1 + r, n) / (Math.pow(1 + r, n) - 1)
}

// --- Persona views ----------------------------------------------------------

function InvestorView({ listing, bundle }) {
  const price = listing.price_amount
  const size = listing.size_sqm
  const rentPerSqm = bundle?.expected_rent_eur_sqm
  if (!price || !size || !rentPerSqm) return <NotEnoughData />

  const monthlyRent = rentPerSqm * size
  const annualRent = monthlyRent * 12
  const yieldPct = (annualRent / price) * 100
  const principal = price * MORTGAGE_LTV
  const mortgage = monthlyMortgage(principal, MORTGAGE_RATE, MORTGAGE_TERM_YEARS)
  const cashFlow = monthlyRent - (mortgage || 0) - monthlyRent * MAINTENANCE_PCT_OF_RENT

  return (
    <Grid>
      <Row label="Asking price" value={fmtEur(price)} />
      <Row label="Expected rent" value={`${fmtEur(monthlyRent)}/mo`} />
      <Divider />
      <Row label="Gross yield" value={`${yieldPct.toFixed(2)}%`} highlight={yieldPct >= 5 ? 'good' : yieldPct >= 3.5 ? 'ok' : 'bad'} />
      <Row label="Annual rent" value={fmtEur(annualRent)} muted />
      <Divider />
      <SectionHeading>Indicative monthly cash flow</SectionHeading>
      <Row label={`Mortgage (${Math.round(MORTGAGE_LTV * 100)}% LTV, ${(MORTGAGE_RATE * 100).toFixed(1)}%, ${MORTGAGE_TERM_YEARS}y)`} value={fmtEur(-mortgage)} />
      <Row label="Maintenance reserve" value={fmtEur(-monthlyRent * MAINTENANCE_PCT_OF_RENT)} muted />
      <Row label="Net cash flow" value={`${fmtEur(cashFlow)}/mo`} highlight={cashFlow > 0 ? 'good' : 'bad'} bold />
      <Note>Excludes condo fees, vacancy, taxes (IMI), and rental income tax.</Note>
    </Grid>
  )
}

function FlipperView({ listing, bundle }) {
  const price = listing.price_amount
  const size = listing.size_sqm
  const reno = listing.reno_cost_estimate || 0
  const resalePerSqm = bundle?.expected_resale_eur_sqm
  if (!price || !size || !resalePerSqm) return <NotEnoughData />

  const txnCost = price * FLIP_TXN_COST_PCT
  const totalIn = price + reno + txnCost
  const resale = resalePerSqm * size
  const margin = resale - totalIn
  const roi = (margin / totalIn) * 100

  return (
    <Grid>
      <Row label="Asking price" value={fmtEur(price)} />
      <Row label="Model reno cost" value={reno ? fmtEur(reno) : '—'} />
      <Row label={`Transaction costs (~${Math.round(FLIP_TXN_COST_PCT * 100)}%)`} value={fmtEur(txnCost)} muted />
      <Row label="Total invested" value={fmtEur(totalIn)} bold />
      <Divider />
      <Row label="Expected resale" value={fmtEur(resale)} />
      <Row label="Resale €/m²" value={`${fmtEur(resalePerSqm)}/m²`} muted />
      <Divider />
      <Row label="Gross margin" value={fmtEurShort(margin)} highlight={margin >= 50_000 ? 'good' : margin >= 0 ? 'ok' : 'bad'} bold />
      <Row label="ROI on cash invested" value={`${roi.toFixed(1)}%`} highlight={roi >= 25 ? 'good' : roi >= 10 ? 'ok' : 'bad'} />
      <Note>Resale figure assumes a renovated unit at neighborhood comp price. Excludes interest, IMI, agent fees.</Note>
    </Grid>
  )
}

function HomeBuyerView({ listing, bundle }) {
  const positives = bundle?.positives || {}
  const buckets = [
    { label: 'Amenities & livability', value: positives.amenity },
    { label: 'Natural light',          value: positives.light },
    { label: 'Outdoor space',          value: positives.outdoor_space },
    { label: 'Demand durability',      value: positives.demand_durability },
    { label: 'Layout flexibility',     value: positives.layout_openness },
  ].filter(b => b.value != null)
  if (buckets.length === 0) return <NotEnoughData />

  const avg = buckets.reduce((a, b) => a + b.value, 0) / buckets.length

  return (
    <Grid>
      <Row label="Lifestyle fit (avg)" value={`${Math.round(avg)} / 100`} highlight={avg >= 60 ? 'good' : avg >= 45 ? 'ok' : 'bad'} bold />
      <Divider />
      {buckets.map(b => <ScoreRow key={b.label} label={b.label} score={b.value} />)}
      <Note>Scores are on a 0–100 scale; higher means more of that signal in the listing/area.</Note>
    </Grid>
  )
}

function HomeRenterView({ listing, bundle }) {
  const price = listing.price_amount
  const size = listing.size_sqm
  if (!price) return <NotEnoughData />

  const perSqm = size ? price / size : null
  const annual = price * 12
  const positives = bundle?.positives || {}
  const lifestyle = ['amenity', 'transit', 'light', 'outdoor_space']
    .map(k => positives[k]).filter(v => v != null)
  const lifestyleAvg = lifestyle.length
    ? lifestyle.reduce((a, b) => a + b, 0) / lifestyle.length : null

  return (
    <Grid>
      <Row label="Monthly rent" value={fmtEur(price)} bold />
      {perSqm && <Row label="Cost per m²" value={`${fmtEur(perSqm)}/m²`} />}
      <Row label="Annualised cost" value={fmtEur(annual)} muted />
      <Divider />
      {lifestyleAvg != null && (
        <Row
          label="Lifestyle fit"
          value={`${Math.round(lifestyleAvg)} / 100`}
          highlight={lifestyleAvg >= 60 ? 'good' : lifestyleAvg >= 45 ? 'ok' : 'bad'}
        />
      )}
      <Note>Excludes utilities, condo fees, and any additional landlord deposits.</Note>
    </Grid>
  )
}

const PERSONA_VIEWS = {
  rental_investor: InvestorView,
  flipper:         FlipperView,
  home_buyer:      HomeBuyerView,
  home_renter:     HomeRenterView,
}

const PERSONA_TITLE = {
  rental_investor: 'Rental investor view',
  flipper:         'Flipper view',
  home_buyer:      'Home buyer view',
  home_renter:     'Home renter view',
}

export default function PersonaInsightCard({ listing, personaId }) {
  if (!personaId || !listing) return null
  const View = PERSONA_VIEWS[personaId]
  if (!View) return null
  const bundle = readBundle(listing)

  return (
    <div className="rounded-lg border border-primary-border bg-primary-tint/30 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-gray-900 text-sm">{PERSONA_TITLE[personaId]}</h3>
        <span className="text-[10px] uppercase tracking-wider text-primary">Persona</span>
      </div>
      <View listing={listing} bundle={bundle} />
    </div>
  )
}

// --- Layout primitives ------------------------------------------------------

function Grid({ children }) {
  return <div className="space-y-1">{children}</div>
}

function Row({ label, value, highlight, muted, bold }) {
  const valueClass = [
    bold ? 'font-bold text-gray-900' : 'font-medium text-gray-800',
    muted ? 'text-gray-500 font-normal' : '',
    highlight === 'good' ? 'text-emerald-700' : '',
    highlight === 'bad'  ? 'text-red-600' : '',
    highlight === 'ok'   ? 'text-amber-700' : '',
  ].join(' ')
  return (
    <div className="flex justify-between items-baseline text-sm">
      <span className={muted ? 'text-gray-500 text-xs' : 'text-gray-600 text-xs'}>{label}</span>
      <span className={valueClass}>{value}</span>
    </div>
  )
}

function Divider() {
  return <div className="border-t border-primary-border my-1.5" />
}

function SectionHeading({ children }) {
  return <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold mt-2 mb-1">{children}</div>
}

function Note({ children }) {
  return <p className="text-[10px] text-gray-500 mt-2 italic leading-snug">{children}</p>
}

function ScoreRow({ label, score }) {
  const accent = score >= 60 ? 'bg-emerald-500' : score >= 45 ? 'bg-amber-400' : 'bg-red-400'
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-600">
        <span>{label}</span>
        <span className="font-medium text-gray-900">{Math.round(score)}</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded overflow-hidden mt-0.5 mb-1">
        <div className={`h-full ${accent}`} style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
      </div>
    </div>
  )
}

function NotEnoughData() {
  return (
    <p className="text-xs text-gray-500 italic">
      Not enough data for this listing yet — comparables or rent estimate missing.
    </p>
  )
}
