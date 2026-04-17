import { createClient } from '@supabase/supabase-js'

// Vite exposes only env vars prefixed with VITE_
const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

// Throwing here would crash the whole app if env vars are missing during dev.
// Instead, export a flag and a stub client so the rest of the app can render
// (the landing page just shows "auth unavailable" if creds aren't set).
export const supabaseEnabled = Boolean(url && anonKey)

export const supabase = supabaseEnabled
  ? createClient(url, anonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null
