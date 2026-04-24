import { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'

export default function SignInModal({ open, onClose, persona = null }) {
  const { signInWithOAuth, signInWithEmail, enabled } = useAuth()
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState(null)

  if (!open) return null

  async function handleEmail(e) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      // Stash the chosen persona so the post-auth redirect can pick it up.
      if (persona) sessionStorage.setItem('pending_persona', persona)
      await signInWithEmail(email)
      setSent(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function handleGoogle() {
    setError(null)
    setBusy(true)
    try {
      if (persona) sessionStorage.setItem('pending_persona', persona)
      await signInWithOAuth('google')
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-xl bg-white shadow-xl p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Sign in</h2>
            <p className="mt-1 text-sm text-gray-500">
              {persona
                ? 'We\u2019ll save your profile and bring you back here.'
                : 'Save your preferences and listings across devices.'}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="Close"
          >
            &times;
          </button>
        </div>

        {!enabled && (
          <div className="mt-4 rounded-md bg-amber-50 border border-amber-200 p-3 text-xs text-amber-800">
            Auth is not configured. Add <code>VITE_SUPABASE_URL</code> and{' '}
            <code>VITE_SUPABASE_ANON_KEY</code> to <code>frontend/.env.local</code> and
            restart the dev server.
          </div>
        )}

        {sent ? (
          <div className="mt-5 rounded-md bg-emerald-50 border border-emerald-200 p-4 text-sm text-emerald-800">
            Check your email for a sign-in link.
          </div>
        ) : (
          <>
            <button
              onClick={handleGoogle}
              disabled={busy || !enabled}
              className="mt-5 w-full inline-flex items-center justify-center gap-2 rounded-md border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <GoogleIcon />
              Continue with Google
            </button>

            <div className="my-5 flex items-center gap-3 text-xs text-gray-400">
              <div className="h-px flex-1 bg-gray-200" />
              or
              <div className="h-px flex-1 bg-gray-200" />
            </div>

            <form onSubmit={handleEmail} className="space-y-3">
              <input
                type="email"
                required
                placeholder="you@email.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                disabled={busy || !enabled}
              />
              <button
                type="submit"
                disabled={busy || !enabled || !email}
                className="w-full rounded-md bg-primary hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed px-4 py-2.5 text-sm font-medium text-white"
              >
                {busy ? 'Sending\u2026' : 'Email me a sign-in link'}
              </button>
            </form>

            {error && (
              <p className="mt-3 text-xs text-red-600">{error}</p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
      />
    </svg>
  )
}
