import { useLanguage } from '../LanguageContext'
import GrantBadge from './GrantBadge'
import { FlipRentBadges } from './FlipRentScorecard'
import SourceLogo from './SourceLogo'
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

  return (
    <div
      onClick={() => onSelect(listing)}
      className={`block p-3 rounded-lg transition-all text-left cursor-pointer ${
        highlighted
          ? 'bg-primary-tint border-2 border-primary-border shadow-sm'
          : isLiked
            ? 'bg-green-50/50 border border-green-200 hover:border-green-400 hover:shadow-sm'
            : isDisliked
              ? 'bg-gray-50 border border-gray-200 opacity-70 hover:opacity-100'
              : 'border border-gray-100 hover:border-primary-border hover:shadow-sm'
      }`}
    >
      <div className="flex justify-between items-start gap-2 mb-1">
        <span className="font-semibold text-gray-900 text-sm leading-tight line-clamp-2">
          {listing.title || `T${listing.rooms ?? '?'} ${t.inArea(listing.neighborhood || 'Lisboa')}`}
        </span>
        <span className="shrink-0 text-primary font-bold text-sm">
          €{fmt(listing.price_amount)}
        </span>
      </div>

      <div className="flex gap-3 text-xs text-gray-500 mt-1">
        {listing.rooms != null && <span>T{listing.rooms}</span>}
        {listing.size_sqm && <span>{fmt(listing.size_sqm)} m²</span>}
        {listing.price_per_sqm && <span>€{fmt(listing.price_per_sqm)}/m²</span>}
      </div>

      <div className="flex items-center justify-between mt-1">
        {listing.neighborhood && (
          <span className="text-xs text-gray-400 truncate">{listing.neighborhood}</span>
        )}
        <span className="ml-auto"><SourceLogo source={listing.source} /></span>
      </div>

      <FlipRentBadges flip={listing.flip_score} rent={listing.rent_score} show={scoreShow} />
      {insight && (
        <PersonaInsightBadge
          insight={insight}
          why={personaId ? <WhyThisRanked listing={listing} personaId={personaId} weights={personaWeights} /> : null}
        />
      )}
      <GrantBadge eligible={listing.grant_eligible} />
      <MatchBadges badges={matchBadges(listing, preferences)} />

      {onSetReaction && (
        <div className="mt-2 flex items-center justify-between">
          <ReactionButtons
            reaction={reaction}
            onSet={(r, c) => onSetReaction(listing, r, c)}
            onClear={() => onClearReaction(listing)}
            variant="compact"
          />
          {reaction?.comment && (
            <span className="text-[10px] text-gray-500 italic truncate ml-2 flex-1 text-right" title={reaction.comment}>
              “{reaction.comment}”
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function PersonaInsightBadge({ insight, why }) {
  const cls = insight.tone === 'good'
    ? 'bg-emerald-100 text-emerald-800 border-emerald-300'
    : insight.tone === 'bad'
      ? 'bg-red-100 text-red-800 border-red-300'
      : 'bg-primary-tint text-primary border-primary-border'
  return (
    <div className="mt-1.5 inline-flex items-center">
      <span className={`inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-0.5 rounded border ${cls}`}>
        <span className="uppercase tracking-wider text-[10px] opacity-75">{insight.label}</span>
        <span>{insight.value}</span>
      </span>
      {why}
    </div>
  )
}

function MatchBadges({ badges }) {
  if (!badges?.length) return null
  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {badges.map(b => (
        <span
          key={b.key}
          className="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200"
          title="Matches your preferences"
        >
          <svg width="9" height="9" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
          </svg>
          {b.label}
        </span>
      ))}
    </div>
  )
}
