import { createContext, useContext, useState } from 'react'
import { translations, PT_TERMS } from './translations'

const LanguageContext = createContext(null)

export function LanguageProvider({ children }) {
  const [lang, setLang] = useState('en')
  const toggle = () => setLang(l => l === 'en' ? 'pt' : 'en')
  const t = translations[lang]
  const translateTerm = (str) => {
    if (!str || lang === 'pt') return str
    return PT_TERMS[str] ?? str
  }
  return (
    <LanguageContext.Provider value={{ lang, toggle, t, translateTerm }}>
      {children}
    </LanguageContext.Provider>
  )
}

export function useLanguage() {
  return useContext(LanguageContext)
}
