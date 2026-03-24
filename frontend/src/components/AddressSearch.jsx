import { useState, useRef } from 'react'
import { useMap } from 'react-leaflet'
import { Marker, Popup } from 'react-leaflet'
import L from 'leaflet'

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

export default function AddressSearch() {
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const debounceRef = useRef(null)

  function handleChange(e) {
    const val = e.target.value
    setQuery(val)
    clearTimeout(debounceRef.current)
    if (val.trim().length < 3) { setSuggestions([]); return }

    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(val + ', Lisboa, Portugal')}&format=json&limit=5&addressdetails=1`
        const res = await fetch(url, { headers: { 'Accept-Language': 'en' } })
        const data = await res.json()
        setSuggestions(data)
      } catch { /* ignore */ }
      finally { setLoading(false) }
    }, 350)
  }

  function handleSelect(item) {
    setQuery(item.display_name.split(',').slice(0, 2).join(',').trim())
    setSuggestions([])
    setResult({ lat: parseFloat(item.lat), lon: parseFloat(item.lon), label: item.display_name })
  }

  function handleKeyDown(e) {
    if (e.key === 'Escape') { setSuggestions([]); setQuery(''); setResult(null) }
  }

  function handleClear() {
    setQuery('')
    setSuggestions([])
    setResult(null)
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
        className="absolute top-3 left-1/2 z-[1000]"
        style={{ transform: 'translateX(-50%)', width: 360 }}
      >
        <div className="relative">
          <input
            type="text"
            value={query}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder="Search address in Lisbon…"
            className="w-full rounded-lg shadow-md border border-gray-200 bg-white px-4 py-2 pr-8 text-sm text-gray-800 placeholder-gray-400 outline-none focus:ring-2 focus:ring-blue-400"
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
                className="px-4 py-2 cursor-pointer hover:bg-blue-50 text-gray-700 truncate border-b border-gray-50 last:border-0"
              >
                {item.display_name}
              </li>
            ))}
          </ul>
        )}

        {loading && (
          <div className="mt-1 bg-white rounded-lg shadow px-4 py-2 text-xs text-gray-400">Searching…</div>
        )}
      </div>
    </>
  )
}
