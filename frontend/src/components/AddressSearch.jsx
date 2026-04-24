import { useState, useRef, useEffect } from 'react'
import { useMap } from 'react-leaflet'
import { Marker, Popup } from 'react-leaflet'
import L from 'leaflet'
import { addressLookup } from '../api'

const PIN_ICON = L.divIcon({
  className: '',
  html: `<div style="
    width:24px;height:24px;border-radius:50% 50% 50% 0;
    background:#ef4444;border:2px solid #b91c1c;
    transform:rotate(-45deg);margin-top:-12px;margin-left:-12px;
  "></div>`,
  iconSize: [24, 24],
  iconAnchor: [12, 24],
  popupAnchor: [0, -28],
})

function FlyToResult({ result }) {
  const map = useMap()
  if (result) map.flyTo([result.lat, result.lon], 17, { duration: 1.2 })
  return null
}

export default function AddressSearch({ onSelectListing, onLookupResult }) {
  const map = useMap()
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [lookupState, setLookupState] = useState(null) // null | 'loading' | 'done' | 'error' | 'empty' | 'busy'
  const [lookupData, setLookupData] = useState(null)
  const [lookupError, setLookupError] = useState(null)
  const debounceRef = useRef(null)
  const containerRef = useRef(null)

  async function fetchSuggestions(val) {
    const base = `https://nominatim.openstreetmap.org/search?format=json&limit=8&addressdetails=1&countrycodes=pt&q=${encodeURIComponent(val)}`
    const headers = { 'Accept-Language': 'en' }
    let viewbox = null
    if (map) {
      const b = map.getBounds()
      // Nominatim viewbox order: west,north,east,south
      viewbox = `${b.getWest()},${b.getNorth()},${b.getEast()},${b.getSouth()}`
    }
    // First try: bias to visible map area (soft bias, bounded=0)
    const firstUrl = viewbox ? `${base}&viewbox=${viewbox}&bounded=0` : base
    let res = await fetch(firstUrl, { headers })
    let data = await res.json()
    // Fallback: broaden to all of Portugal if nothing came back
    if ((!data || data.length === 0) && viewbox) {
      res = await fetch(base, { headers })
      data = await res.json()
    }
    return data || []
  }

  // Close suggestions when clicking outside the search container
  useEffect(() => {
    function handleClickOutside(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setSuggestions([])
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  function handleChange(e) {
    const val = e.target.value
    setQuery(val)
    setLookupState(null)
    setLookupData(null)
    clearTimeout(debounceRef.current)
    if (val.trim().length < 3) { setSuggestions([]); return }

    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const data = await fetchSuggestions(val)
        setSuggestions(data)
      } catch { /* ignore */ }
      finally { setLoading(false) }
    }, 350)
  }

  function handleSelect(item) {
    setQuery(item.display_name.split(',').slice(0, 2).join(',').trim())
    setSuggestions([])
    setResult({ lat: parseFloat(item.lat), lon: parseFloat(item.lon), label: item.display_name })
    setLookupState(null)
    setLookupData(null)
  }

  function handleKeyDown(e) {
    if (e.key === 'Escape') { setSuggestions([]); setQuery(''); setResult(null); setLookupState(null); setLookupData(null) }
  }

  function handleClear() {
    setQuery('')
    setSuggestions([])
    setResult(null)
    setLookupState(null)
    setLookupData(null)
  }

  async function handleLookup() {
    if (!result) return
    setSuggestions([])
    setLookupState('loading')
    setLookupData(null)
    try {
      const data = await addressLookup(query, result.lat, result.lon)
      setLookupData(data)
      if (!data.listings || data.listings.length === 0) {
        setLookupState('empty')
      } else {
        setLookupState('done')
        if (onLookupResult) onLookupResult(data.listings)
        if (onSelectListing && data.listings[0]) {
          const first = data.listings[0]
          onSelectListing({ ...first, listing_type: first.listing_type || 'sale' })
        }
      }
    } catch (err) {
      if (err.message?.includes('429')) {
        setLookupState('busy')
      } else {
        setLookupState('error')
        setLookupError(err.message || 'Search failed')
      }
    }
  }

  return (
    <>
      {result && <FlyToResult result={result} />}
      {result && (
        <Marker position={[result.lat, result.lon]} icon={PIN_ICON}>
          <Popup>{result.label.split(',').slice(0, 3).join(',')}</Popup>
        </Marker>
      )}

      {/* Search box — positioned top-center of map */}
      <div
        ref={containerRef}
        className="absolute top-3 left-1/2 z-[1000]"
        style={{ transform: 'translateX(-50%)', width: 360 }}
      >
        <div className="relative">
          <input
            type="text"
            value={query}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            onBlur={() => setTimeout(() => setSuggestions([]), 150)}
            placeholder="Search address…"
            className="w-full rounded-lg shadow-md border border-gray-200 bg-white px-4 py-2 pr-8 text-sm text-gray-800 placeholder-gray-400 outline-none focus:ring-2 focus:ring-primary"
          />
          {(query || result) && (
            <button
              onClick={handleClear}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 text-lg leading-none"
            >×</button>
          )}
        </div>

        {suggestions.length > 0 && (
          <ul className="mt-1 bg-white rounded-lg shadow-lg border border-gray-100 text-sm overflow-hidden">
            {suggestions.map(item => (
              <li
                key={item.place_id}
                onClick={() => handleSelect(item)}
                className="px-4 py-2 cursor-pointer hover:bg-primary-tint text-gray-700 truncate border-b border-gray-50 last:border-0"
              >
                {item.display_name}
              </li>
            ))}
          </ul>
        )}

        {loading && (
          <div className="mt-1 bg-white rounded-lg shadow px-4 py-2 text-xs text-gray-400">Searching…</div>
        )}

        {/* Lookup button — appears after address is selected */}
        {result && lookupState === null && (
          <button
            onClick={handleLookup}
            className="mt-1 w-full bg-primary hover:bg-primary-hover text-white text-sm font-medium rounded-lg shadow px-4 py-2 transition-colors"
          >
            Find listings here
          </button>
        )}

        {/* Lookup states */}
        {lookupState === 'loading' && (
          <div className="mt-1 bg-white rounded-lg shadow px-4 py-2.5 text-sm text-gray-600 flex items-center gap-2">
            <svg className="animate-spin h-4 w-4 text-primary" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
            </svg>
            Searching… this may take 15-30 seconds
          </div>
        )}

        {lookupState === 'done' && lookupData && (
          <div className="mt-1 bg-green-50 border border-green-200 rounded-lg shadow px-4 py-2 text-sm text-green-800">
            Found {lookupData.listings.length} listing{lookupData.listings.length !== 1 ? 's' : ''}
            {lookupData.source === 'idealista' && ' (scraped from Idealista)'}
            {lookupData.source === 'database' && ' (already in database)'}
          </div>
        )}

        {lookupState === 'empty' && (
          <div className="mt-1 bg-yellow-50 border border-yellow-200 rounded-lg shadow px-4 py-2 text-sm text-yellow-800">
            No listings found near this address
          </div>
        )}

        {lookupState === 'busy' && (
          <div className="mt-1 bg-orange-50 border border-orange-200 rounded-lg shadow px-4 py-2 text-sm text-orange-800">
            Another lookup is in progress. Try again in a moment.
          </div>
        )}

        {lookupState === 'error' && (
          <div className="mt-1 bg-red-50 border border-red-200 rounded-lg shadow px-4 py-2 text-sm text-red-800">
            {lookupError || 'Search failed'}
          </div>
        )}
      </div>
    </>
  )
}
