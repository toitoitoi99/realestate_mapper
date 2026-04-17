import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './index.css'
import App from './App.jsx'
import { LanguageProvider } from './LanguageContext.jsx'
import { ScoreBandsProvider } from './ScoreBandsContext.jsx'
import { AuthProvider, useAuth } from './contexts/AuthContext.jsx'
import Landing from './pages/Landing.jsx'
import Onboarding from './pages/Onboarding.jsx'

// Decide what to show at "/": signed-in users with a persona go straight
// to /app; everyone else sees the landing page.
function RootRoute() {
  const { user, profile, loading } = useAuth()
  if (loading) return <div className="min-h-screen grid place-items-center text-gray-500">Loading&hellip;</div>
  if (user && profile?.persona) return <Navigate to="/app" replace />
  return <Landing />
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <LanguageProvider>
      <ScoreBandsProvider>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<RootRoute />} />
              <Route path="/onboarding" element={<Onboarding />} />
              <Route path="/app" element={<App />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </ScoreBandsProvider>
    </LanguageProvider>
  </StrictMode>,
)
