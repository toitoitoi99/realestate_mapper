import { useState } from 'react'
import MyListingsPage from './MyListingsPage'
import ComparePage from './ComparePage'
import Onboarding from '../pages/Onboarding'

const TABS = [
  { id: 'impressions', label: '⭐ My listings' },
  { id: 'compare',     label: '⚖️ Compare' },
  { id: 'profile',     label: '👤 Edit profile' },
]

export default function MyPage({ onBack, onViewListing, initialTab = 'impressions' }) {
  const [tab, setTab] = useState(initialTab)

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Shared header */}
      <div className="bg-white border-b border-gray-200 shrink-0">
        <div className="flex items-center gap-4 px-4 py-2">
          <button
            onClick={onBack}
            className="text-xs text-gray-600 hover:text-gray-900 cursor-pointer"
          >
            ← Back to map
          </button>
          <span className="font-semibold text-gray-800">My account</span>
        </div>

        {/* Tab bar */}
        <div className="flex border-t border-gray-100">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                tab === t.id
                  ? 'border-primary text-primary'
                  : 'border-transparent text-gray-500 hover:text-gray-800 hover:border-gray-300'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-auto">
        {tab === 'impressions' && (
          <MyListingsPage embedded onViewListing={onViewListing} />
        )}
        {tab === 'compare' && (
          <ComparePage embedded onViewListing={onViewListing} />
        )}
        {tab === 'profile' && (
          <div className="overflow-auto">
            <Onboarding onDone={onBack} />
          </div>
        )}
      </div>
    </div>
  )
}
