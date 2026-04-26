import { useLanguage } from '../LanguageContext'
import GrantBadge from './GrantBadge'
import { FlipRentBadges } from './FlipRentScorecard'
import ReactionButtons from './ReactionButtons'
import { matchBadges } from '../lib/matchBadges'
import { personaInsight } from '../lib/personaInsight'
import WhyThisRanked from './WhyThisRanked'

export default function ListingCard({ listing, onSelect, highlighted, reaction, onSetReaction, onClearReaction, preferences, personaId, personaWeights, scoreShow = 'both' }) {
  const { t } = useLanguage()
  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  const isDisliked = reaction?.reaction === 'dislike'
  const isLiked = reaction?.reaction === 'like'
  const insight = personaInsight(listing, personaId)

  // Status pill: sold listings get a muted indicator
  const isSold = listing.status === 'sold'

  return (
    <div
      onClick={() => onSelect(listing)}
      className={`block px-3 py-2 rounded-lg transition-all text-left cursor-pointer ${
        highlighted
          ? 'bg-primary-tint border-2 border-primary-border shadow-sm'
          : isLiked
            ? 'bg-green-50/40 border border-green-200 hover:border-green-300 hover:shadow-sm'
            : isDisliked
              ? 'border border-gray-100 opacity-60 hover:opacity-90'
              : 'border border-gray-100 hover:border-gray-300 hover:shadow-sm'
      }`}
    >
      {/* Row 1: Score badges (if any) + status pill */}
      <ScoreLine flip={listing.flip_score} rent={listing.rent_score} show={scoreShow} isSold={isSold} grant={listing.grant_eligible} />

      {/* Row 2: Price (prominent) + price/sqm */}
      <div className="flex items-baseline justify-between gap-2 mt-1">
        <span className="text-sm font-bold text-slate-800">
          €{fmt(listing.price_amount)}
        </span>
        {listing.price_per_sqm && (
          <span className="text-xs text-slate-400 tabular-nums shrink-0">
            €{fmt(listing.price_per_sqm)}/m²
          </span>
        )}
      </div>

      {/* Row 3: Size + rooms + neighborhood */}
      <div className="flex items-center gap-2 mt-0.5 text-xs text-slate-500">
        {listing.rooms != null && <span>T{listing.rooms}</span>}
        {listing.size_sqm && <span>{fmt(listing.size_sqm)} m²</span>}
        {listing.neighborhood && (
          <>
            <span className="text-slate-300">·</span>
            <span className="truncate">{listing.neighborhood}</span>
          </>
        )}
      </div>

      {/* Row 4: Title/address — only if it adds info beyond the above */}
      {listing.title && (
        <p className="text-xs text-slate-400 mt-0.5 line-clamp-1 leading-snug">
          {listing.title}
        </p>
      )}

      {/* Row 5: Persona insight (inline, no colored background) */}
      {insight && (
        <PersonaInsightBadge
          insight={insight}
          why={personaId ? <WhyThisRanked listing={listing} personaId={personaId} weights={personaWeights} /> : null}
        />
      )}

      {/* Row 6: Match badges (preference matches) */}
      <MatchBadges badges={matchBadges(listing, preferences)} />

      {/* Row 7: Reaction buttons — right-aligned, no extra margin unless needed */}
      {onSetReaction && (
        <div className="mt-1.5 flex items-center justify-between">
          <ReactionButtons
            reaction={reaction}
            onSet={(r, c) => onSetReaction(listing, r, c)}
            onClear={() => onClearReaction(listing)}
            variant="compact"
          />
          {reaction?.comment && (
            <span className="text-[10px] text-slate-400 italic truncate ml-2 flex-1 text-right" title={reaction.comment}>
              "{reaction.comment}"
            </span>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Top score line: flip/rent badges on the left, status + grant on the right.
 * Only renders if there's something to show.
 */
function ScoreLine({ flip, rent, show, isSold, grant }) {
  const hasBadges = (flip != null || rent != null)
  const hasStatus = isSold || grant
  if (!hasBadges && !hasStatus) return null

  return (
    <div className="flex items-center justify-between gap-2">
      <FlipRentBadges flip={flip} rent={rent} show={show} className="" />
      <div className="flex items-center gap-1 ml-auto">
        {grant && <GrantBadge eligible={grant} compact />}
        {isSold && (
          <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 uppercase tracking-wide">
            Sold
          </span>
        )}
      </div>
    </div>
  )
}

function PersonaInsightBadge({ insight, why }) {
  const colorCls = insight.tone === 'good'
    ? 'text-emerald-700'
    : insight.tone === 'bad'
      ? 'text-red-600'
      : 'text-slate-600'
  return (
    <div className="mt-1 flex items-center gap-1">
      <span className={`text-[10px] font-semibold uppercase tracking-wider ${colorCls}`}>
        {insight.label}
      </span>
      <span className={`text-xs font-medium ${colorCls}`}>{insight.value}</span>
      {why}
    </div>
  )
}

function MatchBadges({ badges }) {
  if (!badges?.length) return null
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {badges.map(b => (
        <span
          key={b.key}
          className="inline-flex items-center gap-0.5 text-[10px] font-medium px-1.5 py-0.5 rounded bg-slate-100 text-slate-600"
          title="Matches your preferences"
        >
          <svg width="8" height="8" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
          </svg>
          {b.label}
        </span>
      ))}
    </div>
  )
}
