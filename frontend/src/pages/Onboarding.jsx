import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { getPersona, PERSONAS } from '../lib/personas'
import {
  EMPTY_PREFERENCES, STYLE_OPTIONS, RENOVATION_OPTIONS, BEDROOM_OPTIONS,
} from '../lib/preferences'
import { extractImageTags, extractPreferences, fetchPreferenceDeck } from '../api'
import { supabase } from '../lib/supabase'

// Single-page preference wizard. Persona at top, then chips/ranges for the
// rest. Saves both `persona` and `preferences` jsonb to the profile row.
export default function Onboarding({ onDone } = {}) {
  const { user, profile, loading, refreshProfile } = useAuth()
  const navigate = useNavigate()
  const done = () => { if (onDone) onDone(); else navigate('/app') }
  const [personaId, setPersonaId] = useState('')
  const [prefs, setPrefs] = useState(EMPTY_PREFERENCES)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  // Reference image state — preview, extracted tags, in-flight + error.
  const [refImageUrl, setRefImageUrl] = useState(null)
  const [refTags, setRefTags] = useState(null)        // {style_primary, color_palette, ...}
  const [refExtracting, setRefExtracting] = useState(false)
  const [refError, setRefError] = useState(null)
  const fileInputRef = useRef(null)

  // Chat state — natural-language input that merges into the wizard.
  const [chatInput, setChatInput] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatSummary, setChatSummary] = useState(null)
  const [chatError, setChatError] = useState(null)

  // Swipe deck state — loaded lazily once a persona is picked.
  const [deck, setDeck] = useState(null)               // array of items, or null until loaded
  const [deckIndex, setDeckIndex] = useState(0)
  const [deckLoading, setDeckLoading] = useState(false)
  const [deckError, setDeckError] = useState(null)
  const [swipeCounts, setSwipeCounts] = useState({ like: 0, dislike: 0, skip: 0 })
  const lastDeckPersonaRef = useRef(null)

  async function sendChat() {
    const msg = chatInput.trim()
    if (!msg) return
    setChatBusy(true); setChatError(null)
    try {
      const result = await extractPreferences(msg, { ...prefs, persona: personaId })
      // Merge only the keys the model returned. budget/size are objects;
      // shallow-merge those so partial updates (e.g. only max) preserve min.
      setPrefs(prev => {
        const next = { ...prev }
        if (result.budget) next.budget = { ...prev.budget, ...result.budget }
        if (result.size)   next.size   = { ...prev.size,   ...result.size }
        if (result.bedrooms_min != null)    next.bedrooms_min = result.bedrooms_min
        if (result.style)                   next.style = result.style
        if (result.outdoor_required != null) next.outdoor_required = result.outdoor_required
        if (result.max_renovation)          next.max_renovation = result.max_renovation
        return next
      })
      if (result.persona) setPersonaId(result.persona)
      setChatSummary(result.summary || 'Got it.')
      setChatInput('')
    } catch (e) {
      setChatError(e.message || 'Could not understand that')
    } finally {
      setChatBusy(false)
    }
  }

  // Hydrate from existing profile + any persona stashed during sign-in.
  useEffect(() => {
    const stashed = sessionStorage.getItem('pending_persona')
    setPersonaId(stashed || profile?.persona || '')
    if (profile?.preferences) {
      setPrefs({ ...EMPTY_PREFERENCES, ...profile.preferences })
    }
    if (profile?.reference_image_tags) {
      setRefTags(profile.reference_image_tags)
    }
  }, [profile])

  async function onPickImage(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setRefError(null)
    setRefExtracting(true)
    setRefTags(null)
    // Local preview while we wait for the API.
    const previewUrl = URL.createObjectURL(file)
    setRefImageUrl(previewUrl)
    try {
      const tags = await extractImageTags(file)
      setRefTags(tags)
      // Auto-fill preference chips from the extracted tags (only fields the
      // wizard exposes — user can still override).
      setPrefs(prev => ({
        ...prev,
        style: tags.style_primary || prev.style,
        outdoor_required: prev.outdoor_required || (tags.outdoor_type && tags.outdoor_type !== 'none'),
      }))
    } catch (err) {
      setRefError(err.message || 'Extraction failed')
    } finally {
      setRefExtracting(false)
    }
  }

  function clearRefImage() {
    setRefImageUrl(null)
    setRefTags(null)
    setRefError(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  useEffect(() => {
    if (!loading && !user) navigate('/')
  }, [loading, user, navigate])

  // Fetch a fresh deck whenever the persona changes (don't refetch on every
  // chip nudge — would be jarring mid-swipe).
  useEffect(() => {
    if (!personaId) return
    if (lastDeckPersonaRef.current === personaId) return
    lastDeckPersonaRef.current = personaId
    setDeckLoading(true); setDeckError(null)
    fetchPreferenceDeck({
      persona: personaId,
      n: 12,
      minPrice: prefs.budget?.min ?? undefined,
      maxPrice: prefs.budget?.max ?? undefined,
      minSqm:   prefs.size?.min ?? undefined,
      maxSqm:   prefs.size?.max ?? undefined,
    }).then(res => {
      setDeck(res.items || [])
      setDeckIndex(0)
      setSwipeCounts({ like: 0, dislike: 0, skip: 0 })
    }).catch(e => {
      setDeckError(e.message || 'Could not load deck')
    }).finally(() => setDeckLoading(false))
  }, [personaId])

  async function recordSwipe(item, action) {
    if (!item) return
    setSwipeCounts(prev => ({ ...prev, [action]: prev[action] + 1 }))
    setDeckIndex(i => i + 1)
    if (!user) return
    // Fire-and-forget; conflict on (user_id, listing_source, listing_id)
    // means this listing was already swiped — overwrite the action.
    try {
      await supabase.from('profile_swipes').upsert({
        user_id:        user.id,
        listing_id:     item.id,
        listing_source: item.source,
        listing_type:   item.listing_type,
        action,
        persona:        personaId || null,
        axis_bins:      item.axis_bins || {},
        factor_positives: item.factor_positives || {},
      }, { onConflict: 'user_id,listing_source,listing_id' })
    } catch (e) {
      console.warn('swipe save failed:', e)
    }
  }

  const persona = getPersona(personaId)

  function update(path, value) {
    setPrefs(prev => {
      const next = { ...prev }
      const parts = path.split('.')
      if (parts.length === 1) {
        next[parts[0]] = value
      } else {
        next[parts[0]] = { ...prev[parts[0]], [parts[1]]: value }
      }
      return next
    })
  }

  async function save() {
    if (!user || !personaId) return
    setSaving(true); setError(null)
    try {
      const { error: e } = await supabase.from('profiles').upsert({
        id: user.id,
        persona: personaId,
        preferences: prefs,
        reference_image_tags: refTags || null,
        updated_at: new Date().toISOString(),
      })
      if (e) throw e
      sessionStorage.removeItem('pending_persona')
      await refreshProfile()
      done()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="p-8 text-gray-500">Loading&hellip;</div>

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white py-10">
      <div className="max-w-2xl mx-auto px-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-900">Tune your map</h1>
          <button
            onClick={done}
            className="text-sm text-gray-500 hover:text-gray-800"
          >
            Skip
          </button>
        </div>
        <p className="mt-2 text-sm text-gray-500">
          Pick what matters. Anything left blank means &ldquo;no preference.&rdquo; You can change all of this later.
        </p>

        {/* Chat — fills the chips for you */}
        <Section
          title="Tell us what you're looking for"
          subtitle="Skip the chips — type naturally and we'll fill the form. e.g. &ldquo;modern 2-bed under 450k, balcony, willing to renovate&rdquo;">
          <div className="flex gap-2">
            <input
              type="text"
              value={chatInput}
              onChange={e => setChatInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !chatBusy) sendChat() }}
              placeholder="Describe your ideal place\u2026"
              disabled={chatBusy}
              className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent disabled:opacity-60"
            />
            <button
              onClick={sendChat}
              disabled={chatBusy || !chatInput.trim()}
              className="rounded-md bg-primary hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed px-4 py-2 text-sm font-medium text-white"
            >
              {chatBusy ? '\u2026' : 'Apply'}
            </button>
          </div>
          {chatSummary && (
            <div className="mt-3 text-xs rounded-md bg-emerald-50 border border-emerald-200 p-2 text-emerald-800">
              <span className="font-medium">Got it: </span>{chatSummary}
            </div>
          )}
          {chatError && (
            <div className="mt-3 text-xs text-red-600">{chatError}</div>
          )}
        </Section>

        {/* Persona */}
        <Section title="What are you doing?">
          <div className="grid grid-cols-2 gap-2">
            {Object.values(PERSONAS).map(p => (
              <button
                key={p.id}
                type="button"
                onClick={() => setPersonaId(p.id)}
                className={chipClass(personaId === p.id)}
              >
                <span className="mr-1">{p.icon}</span>
                {p.label}
              </button>
            ))}
          </div>
        </Section>

        {/* Budget */}
        <Section title={persona?.defaultView === 'rent' ? 'Monthly budget (€)' : 'Budget (€)'}>
          <RangeRow
            min={prefs.budget.min}
            max={prefs.budget.max}
            onMin={v => update('budget.min', v)}
            onMax={v => update('budget.max', v)}
            placeholderMin="No min"
            placeholderMax="No max"
            step={persona?.defaultView === 'rent' ? 50 : 10000}
          />
        </Section>

        {/* Size + bedrooms */}
        <Section title="Size & rooms">
          <div className="text-xs text-gray-500 mb-1">Living area (m²)</div>
          <RangeRow
            min={prefs.size.min}
            max={prefs.size.max}
            onMin={v => update('size.min', v)}
            onMax={v => update('size.max', v)}
            placeholderMin="No min"
            placeholderMax="No max"
            step={5}
          />
          <div className="text-xs text-gray-500 mt-3 mb-1">Bedrooms</div>
          <ChipGroup
            options={BEDROOM_OPTIONS}
            value={prefs.bedrooms_min}
            onChange={v => update('bedrooms_min', v)}
            allowClear
          />
        </Section>

        {/* Style */}
        <Section title="Interior style"
          subtitle="One choice — applies once we have photo tags for a listing.">
          <ChipGroup
            options={STYLE_OPTIONS}
            value={prefs.style}
            onChange={v => update('style', v)}
            allowClear
          />
        </Section>

        {/* Reference image */}
        <Section
          title="Show us a vibe (optional)"
          subtitle="Upload a photo of an interior you love — Pinterest, magazine, anything. We'll auto-fill style chips below.">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={onPickImage}
            className="hidden"
            id="ref-image-input"
          />
          {!refImageUrl ? (
            <label
              htmlFor="ref-image-input"
              className="block w-full rounded-lg border-2 border-dashed border-gray-300 hover:border-primary-border hover:bg-primary-tint/30 cursor-pointer p-6 text-center"
            >
              <div className="text-3xl">🖼️</div>
              <div className="mt-2 text-sm font-medium text-gray-700">Click to upload</div>
              <div className="mt-0.5 text-xs text-gray-400">JPG, PNG, or WebP &middot; up to 6 MB</div>
            </label>
          ) : (
            <div className="flex gap-4">
              <img
                src={refImageUrl}
                alt="Reference"
                className="w-32 h-32 object-cover rounded-lg border border-gray-200 shrink-0"
              />
              <div className="flex-1 min-w-0">
                {refExtracting && (
                  <div className="text-sm text-gray-500">Extracting style&hellip;</div>
                )}
                {refError && (
                  <div className="text-sm text-red-600">{refError}</div>
                )}
                {refTags && !refExtracting && (
                  <div>
                    {refTags.summary && (
                      <div className="text-sm italic text-gray-600 mb-2">
                        &ldquo;{refTags.summary}&rdquo;
                      </div>
                    )}
                    <div className="flex flex-wrap gap-1.5">
                      {refTags.style_primary && <Tag>{refTags.style_primary}</Tag>}
                      {refTags.color_palette && <Tag>{refTags.color_palette} palette</Tag>}
                      {refTags.light_level && <Tag>{refTags.light_level} light</Tag>}
                      {refTags.outdoor_type && refTags.outdoor_type !== 'none' && (
                        <Tag>{refTags.outdoor_type}</Tag>
                      )}
                      {refTags.floor_material && refTags.floor_material !== 'unknown' && (
                        <Tag>{refTags.floor_material} floors</Tag>
                      )}
                      {(refTags.standout_features || []).map(f => (
                        <Tag key={f}>{f.replace('_', ' ')}</Tag>
                      ))}
                    </div>
                  </div>
                )}
                <button
                  onClick={clearRefImage}
                  className="mt-3 text-xs text-gray-500 hover:text-gray-800"
                >
                  Replace image
                </button>
              </div>
            </div>
          )}
        </Section>

        {/* Lifestyle */}
        <Section title="Lifestyle">
          <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-700">
            <input
              type="checkbox"
              checked={prefs.outdoor_required}
              onChange={e => update('outdoor_required', e.target.checked)}
              className="h-4 w-4"
            />
            Must have outdoor space (balcony, terrace, garden, or rooftop)
          </label>

          <div className="text-xs text-gray-500 mt-4 mb-1">
            Renovation tolerance{persona?.id === 'flipper' && ' — flippers usually pick "Anything"'}
          </div>
          <ChipGroup
            options={RENOVATION_OPTIONS}
            value={prefs.max_renovation}
            onChange={v => update('max_renovation', v)}
            allowClear
            withHints
          />

          <div className="mt-5 pt-4 border-t border-gray-100">
            <label className="flex items-start gap-2 cursor-pointer text-sm text-gray-700">
              <input
                type="checkbox"
                checked={prefs.strict_tags}
                onChange={e => update('strict_tags', e.target.checked)}
                className="h-4 w-4 mt-0.5"
              />
              <span>
                Strict tag match
                <span className="block text-xs text-gray-500 mt-0.5 leading-snug">
                  Hide listings we haven&rsquo;t photo-tagged yet (we&rsquo;re tagging them in batches; coverage grows over time). Off by default so you don&rsquo;t miss matches.
                </span>
              </span>
            </label>
          </div>
        </Section>

        {/* Swipe deck — only after a persona is picked, since axes depend on it */}
        {personaId && (
          <Section
            title="Quick swipes"
            subtitle={
              personaId === 'home_renter'
                ? 'Rental listings — tap like or skip to help us tune your results.'
                : personaId === 'flipper'
                ? 'Sale listings with renovation upside — like what catches your eye.'
                : personaId === 'rental_investor'
                ? 'Sale listings to buy and rent out — like what looks promising.'
                : 'Sale listings — tap like or skip to help us tune your results.'
            }>
            <SwipeDeck
              loading={deckLoading}
              error={deckError}
              deck={deck}
              index={deckIndex}
              counts={swipeCounts}
              persona={personaId}
              onAction={recordSwipe}
            />
          </Section>
        )}

        {error && <p className="mt-6 text-sm text-red-600">{error}</p>}

        <div className="mt-8 flex items-center gap-3">
          <button
            onClick={save}
            disabled={!personaId || saving}
            className="rounded-md bg-primary hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed px-5 py-2.5 text-sm font-semibold text-white"
          >
            {saving ? 'Saving\u2026' : 'Save and continue'}
          </button>
          <button
            onClick={() => setPrefs(EMPTY_PREFERENCES)}
            className="text-sm text-gray-500 hover:text-gray-800"
          >
            Reset preferences
          </button>
        </div>
      </div>
    </div>
  )
}

function Section({ title, subtitle, children }) {
  return (
    <section className="mt-6 rounded-lg border border-gray-200 bg-white p-5">
      <div className="text-sm font-semibold text-gray-900">{title}</div>
      {subtitle && <div className="mt-0.5 text-xs text-gray-500">{subtitle}</div>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function chipClass(active) {
  return `inline-flex items-center justify-center px-3 py-1.5 rounded-full text-sm border transition-colors ${
    active
      ? 'bg-primary text-white border-primary'
      : 'bg-white text-gray-700 border-gray-300 hover:border-primary hover:text-primary'
  }`
}

function ChipGroup({ options, value, onChange, allowClear, withHints }) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map(opt => (
        <button
          key={String(opt.id)}
          type="button"
          onClick={() => onChange(opt.id === value && allowClear ? null : opt.id)}
          title={withHints ? opt.hint : undefined}
          className={chipClass(value === opt.id)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

function Tag({ children }) {
  return (
    <span className="inline-block px-2 py-0.5 rounded-full text-[11px] bg-primary-tint text-primary">
      {children}
    </span>
  )
}

function PhotoCarousel({ urls, alt }) {
  const [photoIndex, setPhotoIndex] = useState(0)
  const photos = urls && urls.length > 0 ? urls : null

  if (!photos) {
    return (
      <div className="w-full h-56 bg-gray-100 grid place-items-center text-gray-400 text-sm">
        No photo
      </div>
    )
  }

  const prev = (e) => {
    e.stopPropagation()
    setPhotoIndex(i => (i - 1 + photos.length) % photos.length)
  }
  const next = (e) => {
    e.stopPropagation()
    setPhotoIndex(i => (i + 1) % photos.length)
  }

  return (
    <div className="relative w-full h-56 bg-gray-100 overflow-hidden">
      <img
        key={photoIndex}
        src={photos[photoIndex]}
        alt={alt}
        className="w-full h-full object-cover"
        loading="lazy"
      />
      {photos.length > 1 && (
        <>
          <button
            type="button"
            onClick={prev}
            className="absolute left-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/40 hover:bg-black/60 text-white flex items-center justify-center text-sm leading-none"
            aria-label="Previous photo"
          >
            ‹
          </button>
          <button
            type="button"
            onClick={next}
            className="absolute right-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/40 hover:bg-black/60 text-white flex items-center justify-center text-sm leading-none"
            aria-label="Next photo"
          >
            ›
          </button>
          <div className="absolute bottom-2 left-1/2 -translate-x-1/2 flex gap-1">
            {photos.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={(e) => { e.stopPropagation(); setPhotoIndex(i) }}
                className={`w-1.5 h-1.5 rounded-full transition-colors ${i === photoIndex ? 'bg-white' : 'bg-white/50'}`}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function SwipeDeck({ loading, error, deck, index, counts, persona, onAction }) {
  if (loading) {
    return <div className="text-sm text-gray-500">Loading listings\u2026</div>
  }
  if (error) {
    return <div className="text-sm text-red-600">{error}</div>
  }
  if (!deck || deck.length === 0) {
    return <div className="text-sm text-gray-500">No matching listings to swipe on yet.</div>
  }
  if (index >= deck.length) {
    const total = counts.like + counts.dislike + counts.skip
    return (
      <div className="text-center py-6">
        <div className="text-3xl">🎉</div>
        <div className="mt-2 text-sm font-medium text-gray-900">All done</div>
        <div className="mt-1 text-xs text-gray-500">
          {counts.like} liked &middot; {counts.dislike} passed &middot; {counts.skip} skipped &nbsp;({total} total)
        </div>
      </div>
    )
  }

  const item = deck[index]
  const fmt = (n) => (n == null ? '—' : new Intl.NumberFormat('en-US').format(Math.round(n)))
  const priceLabel = item.listing_type === 'rent'
    ? `€${fmt(item.price_amount)}/mo`
    : `€${fmt(item.price_amount)}`
  const photos = item.image_urls && item.image_urls.length > 0
    ? item.image_urls
    : (item.image_url ? [item.image_url] : [])

  return (
    <div>
      <div className="rounded-lg overflow-hidden border border-gray-200 bg-white">
        <PhotoCarousel key={index} urls={photos} alt={item.neighborhood || 'Listing'} />
        <div className="p-4">
          <div className="flex items-baseline justify-between">
            <div className="text-lg font-semibold text-gray-900">{priceLabel}</div>
            <div className="text-xs text-gray-500">{item.parish || item.neighborhood || ''}</div>
          </div>
          <div className="mt-1 text-xs text-gray-600">
            {item.size_sqm ? `${Math.round(item.size_sqm)} m²` : ''}
            {item.bedrooms != null ? ` \u00b7 ${item.bedrooms}-bed` : ''}
            {item.style_primary ? ` \u00b7 ${item.style_primary}` : ''}
            {item.outdoor_type && item.outdoor_type !== 'none' ? ` \u00b7 ${item.outdoor_type}` : ''}
          </div>
          {persona === 'flipper' && item.renovation_class && item.renovation_class !== 'turnkey' && (
            <div className="mt-2 inline-block rounded px-1.5 py-0.5 text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200">
              {item.renovation_class === 'full_renovation' ? '🔨 Needs full reno' : '🖌️ Cosmetic work'}
            </div>
          )}
          {persona === 'rental_investor' && item.price_per_sqm && (
            <div className="mt-2 inline-block rounded px-1.5 py-0.5 text-xs font-medium bg-green-50 text-green-700 border border-green-200">
              €{Math.round(item.price_per_sqm)}/m²
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => onAction(item, 'dislike')}
          className="flex-1 rounded-md border border-gray-300 bg-white hover:bg-red-50 hover:border-red-400 hover:text-red-700 px-3 py-2 text-sm font-medium text-gray-700"
        >
          ✕ Pass
        </button>
        <button
          type="button"
          onClick={() => onAction(item, 'skip')}
          className="rounded-md border border-gray-300 bg-white hover:bg-gray-50 px-3 py-2 text-sm text-gray-500"
        >
          Skip
        </button>
        <button
          type="button"
          onClick={() => onAction(item, 'like')}
          className="flex-1 rounded-md border border-primary-border bg-primary-tint hover:bg-primary-tint hover:border-primary px-3 py-2 text-sm font-medium text-primary"
        >
          ♥ Like
        </button>
      </div>

      <div className="mt-3 flex items-center justify-between text-xs text-gray-500">
        <span>{index + 1} of {deck.length}</span>
        <span>{counts.like} liked &middot; {counts.dislike} passed</span>
      </div>
    </div>
  )
}

function RangeRow({ min, max, onMin, onMax, placeholderMin, placeholderMax, step = 1 }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <input
        type="number"
        value={min ?? ''}
        onChange={e => onMin(e.target.value === '' ? null : Number(e.target.value))}
        placeholder={placeholderMin}
        step={step}
        min={0}
        className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
      />
      <input
        type="number"
        value={max ?? ''}
        onChange={e => onMax(e.target.value === '' ? null : Number(e.target.value))}
        placeholder={placeholderMax}
        step={step}
        min={0}
        className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
      />
    </div>
  )
}
