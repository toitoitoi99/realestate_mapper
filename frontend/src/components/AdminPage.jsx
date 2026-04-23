import { useState } from 'react'
import ScrapersTab from './admin/ScrapersTab'
import TuningTab from './admin/TuningTab'
import DataHealthTab from './admin/DataHealthTab'
import ConfigTab from './admin/ConfigTab'
import CalibrateTab from './admin/CalibrateTab'
import { useIsAdmin, setAdminMode, isAdminModeOn } from '../lib/admin'

const TABS = [
  { key: 'scrapers',   label: 'Scrapers' },
  { key: 'health',     label: 'Data Health' },
  { key: 'tuning',     label: 'Tuning' },
  { key: 'calibrate',  label: 'Calibrate' },
  { key: 'config',     label: 'Config' },
]

export default function AdminPage({ onBack, selectedScraper, onSelectScraper, scraping, scrapeStatus, onScrape, onViewListing }) {
  const [tab, setTab] = useState('scrapers')
  const isAdmin = useIsAdmin()
  const [, force] = useState(0)

  const toggleAdminMode = () => {
    setAdminMode(!isAdminModeOn())
    force(x => x + 1)
  }

  return (
    <div className="flex flex-col h-full bg-gray-50">
      <div className="flex items-center gap-4 px-4 py-2 bg-white border-b border-gray-200 shrink-0">
        <button
          onClick={onBack}
          className="text-xs text-gray-600 hover:text-gray-900 cursor-pointer"
        >
          ← Back to map
        </button>
        <span className="font-semibold text-gray-800">⚙️ Admin</span>
        <nav className="flex gap-1 ml-4">
          {TABS.map(ti => (
            <button
              key={ti.key}
              onClick={() => setTab(ti.key)}
              className={`px-3 py-1 text-xs rounded cursor-pointer ${
                tab === ti.key
                  ? 'bg-gray-800 text-white'
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              {ti.label}
            </button>
          ))}
        </nav>
        <label className="ml-auto flex items-center gap-2 text-xs text-gray-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={isAdmin}
            onChange={toggleAdminMode}
            className="cursor-pointer"
          />
          Admin mode (show per-persona rater on listings)
        </label>
      </div>

      <div className="flex-1 overflow-auto">
        {tab === 'scrapers' && (
          <ScrapersTab
            selectedScraper={selectedScraper}
            onSelectScraper={onSelectScraper}
            scraping={scraping}
            scrapeStatus={scrapeStatus}
            onScrape={onScrape}
          />
        )}
        {tab === 'health'     && <DataHealthTab />}
        {tab === 'tuning'     && <TuningTab onViewListing={onViewListing} />}
        {tab === 'calibrate'  && <CalibrateTab onViewListing={onViewListing} />}
        {tab === 'config'     && <ConfigTab />}
      </div>
    </div>
  )
}
