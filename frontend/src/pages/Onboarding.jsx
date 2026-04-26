import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { getPersona, PERSONAS, PERSONA_ORDER } from '../lib/personas'
import {
  EMPTY_PREFERENCES, STYLE_OPTIONS, RENOVATION_OPTIONS, BEDROOM_OPTIONS,
} from '../lib/preferences'
import { extractImageTags, extractPreferences, fetchPreferenceDeck } from '../api'
import { supabase } from '../lib/supabase'

const TOTAL_STEPS = 5

// Friendly renovation labels for onboarding (maps to the same IDs)
const RENO_ONBOARDING_OPTIONS = [
  { id: 'turnkey',         label: 'Ready to move in' },
  { id: 'cosmetic',        label: 'OK with some work' },
  { id: 'full_renovation', label: 'Happy to renovate' },
]

const PROPERTY_TYPE_OPTIONS = [
  { id: 'apartment', label: 'Apartment' },
  { id: 'house',     label: 'House' },
  { id: null,        label: 'Either' },
]

// Revised persona copy (friendlier for first-impression onboarding)
const PERSONA_ONBOARDING = {
  home_buyer: {
    headline: 'I want to buy a home to live in',
    description: 'Looking for the right place — light, neighborhood feel, and a style you\'ll actually want to wake up in.',
  },
  home_renter: {
    headline: 'I\'m looking for a place to rent',
    description: 'Rental listings ranked by price, commute, and vibe. Move fast with fewer commitments.',
  },
  rental_investor: {
    headline: 'I\'m buying to rent out',
    description: 'Find sale listings with strong rental yield and durable tenant demand.',
  },
  flipper: {
    headline: 'I buy, renovate, and sell',
    description: 'Listings priced below neighborhood comps with renovation upside baked in.',
  },
}

export default function Onboarding({ onDone } = {}) {
  const { user, profile, loading, refreshProfile } = useAuth()
  const navigate = useNavigate()
  const done = () => { if (onDone) onDone(); else navigate('/app') }

  // Step navigation
  const [step, setStep] = useState(1)

  // Core preference state — all preserved from original
  const [personaId, setPersonaId] = useState('')
  const [prefs, setPrefs] = useState(EMPTY_PREFERENCES)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  // Reference image state
  const [refImageUrl, setRefImageUrl] = useState(null)
  const [refTags, setRefTags] = useState(null)
  const [refExtracting, setRefExtracting] = useState(false)
  const [refError, setRefError] = useState(null)
  const fileInputRef = useRef(null)

  // Chat state — kept in state for extractPreferences, but not rendered in the new flow
  const [chatInput] = useState('')

  // Swipe deck state
  const [deck, setDeck] = useState(null)
  const [deckIndex, setDeckIndex] = useState(0)
  const [deckLoading, setDeckLoading] = useState(false)
  const [deckError, setDeckError] = useState(null)
  const [swipeCounts, setSwipeCounts] = useState({ like: 0, dislike: 0, skip: 0 })
  const lastDeckPersonaRef = useRef(null)

  // ── localStorage draft persistence ──────────────────────────────────────────
  // Restore on mount (runs once, before profile hydration)
  useEffect(() => {
    const raw = localStorage.getItem('onboarding_draft')
    if (!raw) return
    try {
      const draft = JSON.parse(raw)
      if (draft.personaId) setPersonaId(draft.personaId)
      if (draft.prefs)     setPrefs({ ...EMPTY_PREFERENCES, ...draft.prefs })
      if (draft.refTags)   setRefTags(draft.refTags)
      if (draft.step && draft.step > 1) setStep(draft.step)
    } catch { /* ignore parse errors */ }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Save on every relevant state change
  useEffect(() => {
    const draft = { step, personaId, prefs, refTags }
    localStorage.setItem('onboarding_draft', JSON.stringify(draft))
  }, [step, personaId, prefs, refTags])

  // ── Profile hydration ───────────────────────────────────────────────────────
  // Hydrate from existing profile + any persona stashed during sign-in.
  // Runs after profile loads; only applies if no localStorage draft was found.
  useEffect(() => {
    const stashed = sessionStorage.getItem('pending_persona')
    if (stashed || profile?.persona) {
      setPersonaId(prev => prev || stashed || profile?.persona || '')
    }
    if (profile?.preferences) {
      setPrefs(prev => {
        // Only hydrate if prefs are still empty (draft takes priority)
        const isEmpty = JSON.stringify(prev) === JSON.stringify(EMPTY_PREFERENCES)
        return isEmpty ? { ...EMPTY_PREFERENCES, ...profile.preferences } : prev
      })
    }
    if (profile?.reference_image_tags) {
      setRefTags(prev => prev || profile.reference_image_tags)
    }
  }, [profile])

  // ── Auth guard ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!loading && !user) navigate('/')
  }, [loading, user, navigate])

  // ── Deck loading ────────────────────────────────────────────────────────────
  // Pre-fetch deck as soon as persona is selected (step 1 completion), not at step 5
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
  }, [personaId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Swipe recording ─────────────────────────────────────────────────────────
  async function recordSwipe(item, action) {
    if (!item) return
    setSwipeCounts(prev => ({ ...prev, [action]: prev[action] + 1 }))
    setDeckIndex(i => i + 1)
    if (!user) return
    try {
      await supabase.from('profile_swipes').upsert({
        user_id:          user.id,
        listing_id:       item.id,
        listing_source:   item.source,
        listing_type:     item.listing_type,
        action,
        persona:          personaId || null,
        axis_bins:        item.axis_bins || {},
        factor_positives: item.factor_positives || {},
      }, { onConflict: 'user_id,listing_source,listing_id' })
    } catch (e) {
      console.warn('swipe save failed:', e)
    }
  }

  // ── Image handling ──────────────────────────────────────────────────────────
  async function onPickImage(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setRefError(null)
    setRefExtracting(true)
    setRefTags(null)
    const previewUrl = URL.createObjectURL(file)
    setRefImageUrl(previewUrl)
    try {
      const tags = await extractImageTags(file)
      setRefTags(tags)
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

  // ── Preference updater ──────────────────────────────────────────────────────
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

  // ── Save ────────────────────────────────────────────────────────────────────
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
      localStorage.removeItem('onboarding_draft')
      await refreshProfile()
      done()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const persona = getPersona(personaId)

  // ── Step navigation helpers ─────────────────────────────────────────────────
  function goNext() { setStep(s => Math.min(s + 1, 6)) }
  function goBack() { setStep(s => Math.max(s - 1, 1)) }

  // canAdvance per step
  const canAdvance = step === 1 ? !!personaId : true

  // ── Loading state ───────────────────────────────────────────────────────────
  if (loading) return <div className="p-8 text-gray-500">Loading&hellip;</div>

  // ── Step renderers ──────────────────────────────────────────────────────────

  function renderStep1() {
    return (
      <div>
        <h2 className="text-2xl font-bold text-gray-900">What brings you here?</h2>
        <p className="mt-2 text-sm text-gray-500">This shapes which listings and scores we show you.</p>
        <div className="mt-6 grid grid-cols-1 sm:grid-cols-2 gap-3">
          {PERSONA_ORDER.map(pid => {
            const p = PERSONAS[pid]
            const copy = PERSONA_ONBOARDING[pid]
            const isActive = personaId === pid
            return (
              <button
                key={pid}
                type="button"
                onClick={() => setPersonaId(pid)}
                className={`text-left rounded-xl border-2 p-5 transition-all ${
                  isActive
                    ? 'border-primary bg-primary-tint/40'
                    : 'border-gray-200 bg-white hover:border-primary/50 hover:bg-gray-50'
                }`}
              >
                <div className="text-4xl mb-3">{p.icon}</div>
                <div className={`text-sm font-semibold ${isActive ? 'text-primary' : 'text-gray-900'}`}>
                  {copy.headline}
                </div>
                <div className="mt-1 text-xs text-gray-500 leading-relaxed">
                  {copy.description}
                </div>
              </button>
            )
          })}
        </div>
      </div>
    )
  }

  function renderStep2() {
    const isRenter = personaId === 'home_renter'
    return (
      <div>
        <h2 className="text-2xl font-bold text-gray-900">What are you looking for?</h2>
        <p className="mt-2 text-sm text-gray-500">
          Let&rsquo;s narrow down the budget and property type.
        </p>

        {!isRenter && (
          <div className="mt-6">
            <div className="text-sm font-medium text-gray-700 mb-2">Property type</div>
            <div className="flex flex-wrap gap-2">
              {PROPERTY_TYPE_OPTIONS.map(opt => (
                <button
                  key={String(opt.id)}
                  type="button"
                  onClick={() => update('property_type', opt.id)}
                  className={chipClass(prefs.property_type === opt.id)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="mt-6">
          <div className="text-sm font-medium text-gray-700 mb-2">
            {isRenter ? 'Monthly rent (€)' : 'Budget (€)'}
          </div>
          <RangeRow
            min={prefs.budget.min}
            max={prefs.budget.max}
            onMin={v => update('budget.min', v)}
            onMax={v => update('budget.max', v)}
            placeholderMin="No min"
            placeholderMax="No max"
            step={isRenter ? 50 : 10000}
          />
          <div className="mt-2 text-xs text-gray-400">
            {isRenter
              ? 'Median rent in Lisboa is ~€1,400/mo for a 2-bed.'
              : 'Median sale price in Lisboa municipality is ~€4,500/m².'}
          </div>
        </div>
      </div>
    )
  }

  function renderStep3() {
    const isFlipper = personaId === 'flipper'
    return (
      <div>
        <h2 className="text-2xl font-bold text-gray-900">What does your ideal place look like?</h2>
        <p className="mt-2 text-sm text-gray-500">Skip anything you&rsquo;re not fussy about.</p>

        <div className="mt-6">
          <div className="text-sm font-medium text-gray-700 mb-2">Living area (m²)</div>
          <RangeRow
            min={prefs.size.min}
            max={prefs.size.max}
            onMin={v => update('size.min', v)}
            onMax={v => update('size.max', v)}
            placeholderMin="No min"
            placeholderMax="No max"
            step={5}
          />
        </div>

        <div className="mt-6">
          <div className="text-sm font-medium text-gray-700 mb-2">Bedrooms</div>
          <ChipGroup
            options={BEDROOM_OPTIONS}
            value={prefs.bedrooms_min}
            onChange={v => update('bedrooms_min', v)}
            allowClear
          />
        </div>

        <div className="mt-6">
          <div className="text-sm font-medium text-gray-700 mb-3">Preferences</div>

          <div className="mb-3">
            <div className="text-xs text-gray-500 mb-1.5">Interior style</div>
            <ChipGroup
              options={STYLE_OPTIONS}
              value={prefs.style}
              onChange={v => update('style', v)}
              allowClear
            />
          </div>

          <div className="mb-3">
            <button
              type="button"
              onClick={() => update('outdoor_required', !prefs.outdoor_required)}
              className={`inline-flex items-center gap-2 rounded-full border px-4 py-1.5 text-sm transition-colors ${
                prefs.outdoor_required
                  ? 'border-primary bg-primary-tint/40 text-primary font-medium'
                  : 'border-gray-300 bg-white text-gray-700 hover:border-primary/50'
              }`}
            >
              <span>{prefs.outdoor_required ? '✓' : '○'}</span>
              Outdoor space required
            </button>
          </div>

          <div>
            <div className="text-xs text-gray-500 mb-1.5">
              Renovation tolerance
              {isFlipper && (
                <span className="ml-2 text-amber-600">— pre-selected for you</span>
              )}
            </div>
            <ChipGroup
              options={RENO_ONBOARDING_OPTIONS}
              value={prefs.max_renovation}
              onChange={v => update('max_renovation', v)}
              allowClear
            />
          </div>
        </div>
      </div>
    )
  }

  function renderStep4() {
    return (
      <div>
        <h2 className="text-2xl font-bold text-gray-900">Got a vibe in mind?</h2>
        <p className="mt-2 text-sm text-gray-500">
          Upload a photo of an interior you love — Pinterest, magazine, anything.
          We&rsquo;ll auto-fill your style chips.
        </p>

        <div className="mt-6">
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
              className="block w-full rounded-xl border-2 border-dashed border-gray-300 hover:border-primary/50 hover:bg-primary-tint/20 cursor-pointer p-10 text-center transition-colors"
            >
              <div className="text-4xl">🖼️</div>
              <div className="mt-3 text-sm font-medium text-gray-700">Click to upload</div>
              <div className="mt-1 text-xs text-gray-400">JPG, PNG, or WebP &middot; up to 6 MB</div>
            </label>
          ) : (
            <div className="flex gap-4">
              <img
                src={refImageUrl}
                alt="Reference"
                className="w-36 h-36 object-cover rounded-xl border border-gray-200 shrink-0"
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
                    <div className="text-xs text-emerald-700 font-medium mb-2">
                      We&rsquo;ve updated your style preference.
                    </div>
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
        </div>
      </div>
    )
  }

  function renderStep5() {
    const swipeSubtitle =
      personaId === 'home_renter'
        ? 'Rental listings — tap like or skip to help us tune your results.'
        : personaId === 'flipper'
        ? 'Sale listings with renovation upside — like what catches your eye.'
        : personaId === 'rental_investor'
        ? 'Sale listings to buy and rent out — like what looks promising.'
        : 'Sale listings — tap like or skip to help us tune your results.'

    return (
      <div>
        <h2 className="text-2xl font-bold text-gray-900">Like what you see?</h2>
        <p className="mt-2 text-sm text-gray-500">{swipeSubtitle}</p>
        <div className="mt-6">
          <SwipeDeck
            loading={deckLoading}
            error={deckError}
            deck={deck}
            index={deckIndex}
            counts={swipeCounts}
            persona={personaId}
            onAction={recordSwipe}
          />
        </div>
      </div>
    )
  }

  function renderStep6() {
    const budgetLabel = (() => {
      const { min, max } = prefs.budget
      if (!min && !max) return 'No preference'
      if (min && max)   return `€${min.toLocaleString()} – €${max.toLocaleString()}`
      if (min)          return `From €${min.toLocaleString()}`
      return `Up to €${max.toLocaleString()}`
    })()

    const styleLabel = prefs.style
      ? STYLE_OPTIONS.find(o => o.id === prefs.style)?.label || prefs.style
      : 'Any'

    const personaCopy = personaId ? PERSONA_ONBOARDING[personaId]?.headline : ''
    const total = swipeCounts.like + swipeCounts.dislike + swipeCounts.skip

    return (
      <div>
        <div className="text-4xl mb-4">🎉</div>
        <h2 className="text-2xl font-bold text-gray-900">You&rsquo;re all set.</h2>
        <p className="mt-2 text-sm text-gray-500">Here&rsquo;s what we learned:</p>

        <div className="mt-6 rounded-xl border border-gray-200 bg-white divide-y divide-gray-100">
          <div className="flex items-baseline justify-between px-5 py-3">
            <span className="text-xs text-gray-500 font-medium">Your goal</span>
            <span className="text-sm text-gray-900">{personaCopy}</span>
          </div>
          <div className="flex items-baseline justify-between px-5 py-3">
            <span className="text-xs text-gray-500 font-medium">Budget</span>
            <span className="text-sm text-gray-900">{budgetLabel}</span>
          </div>
          <div className="flex items-baseline justify-between px-5 py-3">
            <span className="text-xs text-gray-500 font-medium">Style</span>
            <span className="text-sm text-gray-900">{styleLabel}</span>
          </div>
          {total > 0 && (
            <div className="flex items-baseline justify-between px-5 py-3">
              <span className="text-xs text-gray-500 font-medium">Swipes</span>
              <span className="text-sm text-gray-900">
                {swipeCounts.like} liked &middot; {swipeCounts.dislike} passed
              </span>
            </div>
          )}
        </div>

        {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

        <div className="mt-8 flex flex-col gap-3">
          <button
            onClick={save}
            disabled={saving}
            className="w-full rounded-lg bg-primary hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed px-5 py-3 text-sm font-semibold text-white"
          >
            {saving ? 'Saving\u2026' : 'Open the map →'}
          </button>
          <button
            onClick={() => setStep(3)}
            className="w-full rounded-lg border border-gray-300 bg-white hover:bg-gray-50 px-5 py-3 text-sm font-medium text-gray-700"
          >
            Edit preferences
          </button>
        </div>
      </div>
    )
  }

  // ── Navigation bar ──────────────────────────────────────────────────────────
  function renderNav() {
    if (step === 6) return null // Step 6 has its own CTAs

    const isStep4 = step === 4
    const isStep5 = step === 5
    const isSkippable = isStep4 || isStep5

    return (
      <div className="mt-8 flex items-center justify-between">
        <div className="w-24">
          {step > 1 && (
            <button
              onClick={goBack}
              className="text-sm text-gray-500 hover:text-gray-800"
            >
              ← Back
            </button>
          )}
        </div>

        <div className="text-xs text-gray-400">
          Step {step} of {TOTAL_STEPS}
        </div>

        <div className="w-24 flex items-center justify-end gap-3">
          {isSkippable && (
            <button
              onClick={goNext}
              className="text-sm text-gray-500 hover:text-gray-800"
            >
              Skip
            </button>
          )}
          {isStep4 && (
            <button
              onClick={goNext}
              className="rounded-lg bg-primary hover:bg-primary-hover px-4 py-2 text-sm font-semibold text-white"
            >
              Next →
            </button>
          )}
          {isStep5 && (
            <button
              onClick={goNext}
              className="rounded-lg bg-primary hover:bg-primary-hover px-4 py-2 text-sm font-semibold text-white"
            >
              Finish
            </button>
          )}
          {!isSkippable && (
            <button
              onClick={goNext}
              disabled={!canAdvance}
              className="rounded-lg bg-primary hover:bg-primary-hover disabled:opacity-40 disabled:cursor-not-allowed px-4 py-2 text-sm font-semibold text-white"
            >
              Next →
            </button>
          )}
        </div>
      </div>
    )
  }

  // ── Main render ─────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white">
      {/* Progress bar */}
      {step < 6 && (
        <div className="w-full bg-gray-100 h-1">
          <div
            className="bg-primary h-1 transition-all duration-300"
            style={{ width: `${(step / TOTAL_STEPS) * 100}%` }}
          />
        </div>
      )}

      {/* Progress dots */}
      {step < 6 && (
        <div className="flex items-center justify-center gap-2 pt-5 pb-1">
          {Array.from({ length: TOTAL_STEPS }, (_, i) => (
            <div
              key={i}
              className={`rounded-full transition-all duration-300 ${
                i < step
                  ? 'bg-primary w-5 h-1.5'
                  : i === step - 1
                  ? 'bg-primary w-5 h-1.5'
                  : 'bg-gray-300 w-3 h-1.5'
              }`}
            />
          ))}
        </div>
      )}

      <div className="max-w-2xl mx-auto px-6 py-8">
        {step === 1 && renderStep1()}
        {step === 2 && renderStep2()}
        {step === 3 && renderStep3()}
        {step === 4 && renderStep4()}
        {step === 5 && renderStep5()}
        {step === 6 && renderStep6()}

        {renderNav()}
      </div>
    </div>
  )
}

// ── Sub-components (unchanged from original) ────────────────────────────────

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
