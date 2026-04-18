import { useEffect, useState } from 'react'
import { useAuth } from '../contexts/AuthContext'

const LS_KEY = 'admin_mode'

export function isAdminModeOn() {
  try { return localStorage.getItem(LS_KEY) === '1' } catch { return false }
}

export function setAdminMode(on) {
  try {
    if (on) localStorage.setItem(LS_KEY, '1')
    else localStorage.removeItem(LS_KEY)
  } catch {}
  window.dispatchEvent(new Event('admin-mode-change'))
}

export function useIsAdmin() {
  const { profile } = useAuth()
  const [localOn, setLocalOn] = useState(isAdminModeOn())
  useEffect(() => {
    const onChange = () => setLocalOn(isAdminModeOn())
    window.addEventListener('admin-mode-change', onChange)
    window.addEventListener('storage', onChange)
    return () => {
      window.removeEventListener('admin-mode-change', onChange)
      window.removeEventListener('storage', onChange)
    }
  }, [])
  return profile?.is_admin === true || localOn
}
