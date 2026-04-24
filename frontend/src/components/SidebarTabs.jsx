import { useLanguage } from '../LanguageContext'

export default function SidebarTabs({ activeTab, onChangeTab }) {
  const { t } = useLanguage()
  const tabs = [
    { key: 'listings', label: t.tabListings },
    { key: 'neighbourhoods', label: t.tabNeighbourhoods },
  ]

  return (
    <div className="flex border-b border-gray-200 shrink-0">
      {tabs.map(tab => (
        <button
          key={tab.key}
          onClick={() => onChangeTab(tab.key)}
          className={`flex-1 py-2 text-sm font-medium text-center cursor-pointer transition-colors ${
            activeTab === tab.key
              ? 'text-primary border-b-2 border-primary bg-white'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
