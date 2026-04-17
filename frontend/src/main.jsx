import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { LanguageProvider } from './LanguageContext.jsx'
import { ScoreBandsProvider } from './ScoreBandsContext.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <LanguageProvider>
      <ScoreBandsProvider>
        <App />
      </ScoreBandsProvider>
    </LanguageProvider>
  </StrictMode>,
)
