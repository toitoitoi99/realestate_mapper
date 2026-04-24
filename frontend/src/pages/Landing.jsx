import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { PERSONA_ORDER, PERSONAS } from '../lib/personas'
import SignInModal from '../components/SignInModal'

export default function Landing() {
  const navigate = useNavigate()
  const { user, signOut } = useAuth()
  const [signInOpen, setSignInOpen] = useState(false)
  const [pickedPersona, setPickedPersona] = useState(null)

  function pick(personaId) {
    setPickedPersona(personaId)
    if (user) {
      sessionStorage.setItem('pending_persona', personaId)
      navigate('/onboarding')
    } else {
      setSignInOpen(true)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white">
      {/* Top bar */}
      <header className="px-6 py-4 flex items-center justify-between max-w-6xl mx-auto">
        <div className="flex items-center gap-2">
          <span className="text-2xl">🏛️</span>
          <span className="font-semibold text-gray-900">Lisbon Real Estate</span>
        </div>
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/app')}
            className="text-sm text-gray-600 hover:text-gray-900"
          >
            Skip to map
          </button>
          {user ? (
            <>
              <span className="text-sm text-gray-500 hidden sm:inline">
                {user.email}
              </span>
              <button
                onClick={signOut}
                className="text-sm text-gray-600 hover:text-gray-900"
              >
                Sign out
              </button>
            </>
          ) : (
            <button
              onClick={() => setSignInOpen(true)}
              className="text-sm font-medium text-primary hover:text-primary"
            >
              Sign in
            </button>
          )}
        </div>
      </header>

      {/* Hero */}
      <section className="px-6 pt-12 pb-10 max-w-4xl mx-auto text-center">
        <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
          Find your place in Lisbon.
        </h1>
        <p className="mt-4 text-lg text-gray-600 max-w-2xl mx-auto">
          A focused real-estate explorer for Greater Lisbon, Porto, and the Algarve.
          Tell us what you&rsquo;re looking for &mdash; we&rsquo;ll tune the map to you.
        </p>
      </section>

      {/* Persona tiles */}
      <section className="px-6 pb-16 max-w-5xl mx-auto">
        <p className="text-center text-xs uppercase tracking-wider text-gray-400 mb-6">
          Pick what fits you
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {PERSONA_ORDER.map((id) => {
            const p = PERSONAS[id]
            const isPicked = pickedPersona === id
            return (
              <button
                key={id}
                onClick={() => pick(id)}
                className={`group text-left rounded-xl border bg-white p-5 transition-all
                  hover:border-primary hover:shadow-md hover:-translate-y-0.5
                  ${isPicked ? 'border-primary ring-2 ring-primary-border' : 'border-gray-200'}`}
              >
                <div className="text-3xl">{p.icon}</div>
                <div className="mt-3 font-semibold text-gray-900 group-hover:text-blue-700">
                  {p.label}
                </div>
                <div className="mt-1 text-sm text-gray-500 leading-snug">
                  {p.tagline}
                </div>
                <div className="mt-3 text-xs text-primary opacity-0 group-hover:opacity-100 transition-opacity">
                  Choose &rarr;
                </div>
              </button>
            )
          })}
        </div>

        <p className="mt-8 text-center text-xs text-gray-400">
          You can change this any time from settings.
        </p>
      </section>

      <SignInModal
        open={signInOpen}
        onClose={() => setSignInOpen(false)}
        persona={pickedPersona}
      />
    </div>
  )
}
