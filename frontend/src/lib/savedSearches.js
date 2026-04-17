// CRUD wrapper around the public.saved_searches table.
// All calls hit Supabase directly (no backend round-trip) — RLS guarantees
// users only ever see/modify their own rows.

import { supabase, supabaseEnabled } from './supabase'

export async function listSavedSearches() {
  if (!supabaseEnabled) return []
  const { data, error } = await supabase
    .from('saved_searches')
    .select('id, name, filters, area, updated_at')
    .order('updated_at', { ascending: false })
  if (error) {
    console.warn('listSavedSearches:', error)
    return []
  }
  return data || []
}

export async function createSavedSearch({ userId, name, filters, area }) {
  if (!supabaseEnabled) throw new Error('Supabase not configured')
  if (!name?.trim()) throw new Error('Name is required')
  const { data, error } = await supabase
    .from('saved_searches')
    .insert({
      user_id: userId,
      name: name.trim(),
      filters: filters || {},
      area: area || null,
    })
    .select()
    .single()
  if (error) throw error
  return data
}

export async function deleteSavedSearch(id) {
  if (!supabaseEnabled) throw new Error('Supabase not configured')
  const { error } = await supabase.from('saved_searches').delete().eq('id', id)
  if (error) throw error
}
