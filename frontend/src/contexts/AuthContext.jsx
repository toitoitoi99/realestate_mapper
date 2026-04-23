import { createContext, useContext, useEffect, useState } from 'react'
import { supabase, supabaseEnabled } from '../lib/supabase'

const AuthContext = createContext({
  user: null,
  session: null,
  profile: null,
  loading: true,
  enabled: false,
  signInWithOAuth: async () => {},
  signInWithEmail: async () => {},
  signOut: async () => {},
  refreshProfile: async () => {},
})

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null)
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!supabaseEnabled) {
      setLoading(false)
      return
    }

    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session ?? null)
      setLoading(false)
    })

    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s ?? null)
    })
    return () => sub.subscription.unsubscribe()
  }, [])

  // Load the user's profile row whenever the session changes.
  useEffect(() => {
    if (!session?.user) {
      setProfile(null)
      return
    }
    loadProfile(session.user.id).then(setProfile)
  }, [session?.user?.id])

  async function loadProfile(userId) {
    if (!supabaseEnabled) return null
    const { data, error } = await supabase
      .from('profiles')
      .select('*')
      .eq('id', userId)
      .maybeSingle()
    if (error && error.code !== 'PGRST116') {
      console.warn('profile load error:', error)
    }
    return data
  }

  async function signInWithOAuth(provider = 'google') {
    if (!supabaseEnabled) throw new Error('Supabase not configured')
    const { error } = await supabase.auth.signInWithOAuth({
      provider,
      options: { redirectTo: window.location.origin },
    })
    if (error) throw error
  }

  async function signInWithEmail(email) {
    if (!supabaseEnabled) throw new Error('Supabase not configured')
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.origin },
    })
    if (error) throw error
  }

  async function signOut() {
    if (!supabaseEnabled) return
    await supabase.auth.signOut()
  }

  async function refreshProfile() {
    if (!session?.user) return null
    const p = await loadProfile(session.user.id)
    setProfile(p)
    return p
  }

  return (
    <AuthContext.Provider
      value={{
        user: session?.user ?? null,
        session,
        profile,
        loading,
        enabled: supabaseEnabled,
        signInWithOAuth,
        signInWithEmail,
        signOut,
        refreshProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
