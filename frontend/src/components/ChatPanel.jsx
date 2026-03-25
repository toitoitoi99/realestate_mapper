import { useState, useRef, useEffect } from 'react'
import { sendChatMessage } from '../api'
import { useLanguage } from '../LanguageContext'

function ChatListingCard({ listing }) {
  const fmt = (n) => n != null ? Math.round(n).toLocaleString('pt-PT') : '—'

  return (
    <a
      href={listing.url}
      target="_blank"
      rel="noopener noreferrer"
      className="block p-2 bg-white rounded border border-gray-100 hover:border-blue-300 transition-colors text-xs"
    >
      <div className="flex justify-between items-start gap-1">
        <span className="font-medium text-gray-800 leading-tight line-clamp-1 flex-1">
          {listing.title || `T${listing.rooms ?? '?'} ${listing.neighborhood || ''}`}
        </span>
        <span className="shrink-0 font-bold text-blue-700">€{fmt(listing.price_amount)}</span>
      </div>
      <div className="flex gap-2 text-gray-400 mt-0.5">
        {listing.rooms != null && <span>T{listing.rooms}</span>}
        {listing.size_sqm && <span>{fmt(listing.size_sqm)} m²</span>}
        {listing.price_per_sqm && <span>€{fmt(listing.price_per_sqm)}/m²</span>}
        {listing.neighborhood && <span className="truncate">{listing.neighborhood}</span>}
      </div>
    </a>
  )
}

export default function ChatPanel({ onHighlightListings }) {
  const { t } = useLanguage()
  const [isOpen, setIsOpen] = useState(false)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (isOpen) inputRef.current?.focus()
  }, [isOpen])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading) return

    const userMsg = { role: 'user', content: text }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      // Build history for context (only text content for API)
      const history = messages
        .filter(m => typeof m.content === 'string')
        .map(m => ({ role: m.role, content: m.content }))

      const data = await sendChatMessage(text, history)

      if (data.error) {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: data.error,
          isError: true,
        }])
      } else {
        const assistantMsg = {
          role: 'assistant',
          content: data.response,
          listings: data.listings || [],
          filtersApplied: data.filters_applied || {},
          count: data.count || 0,
        }
        setMessages(prev => [...prev, assistantMsg])

        // Highlight found listings on the map
        if (data.listings?.length > 0 && onHighlightListings) {
          onHighlightListings(data.listings)
        }
      }
    } catch (err) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Failed to connect to the search service. Make sure the backend is running.',
        isError: true,
      }])
    } finally {
      setLoading(false)
    }
  }

  if (!isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="absolute bottom-6 left-3 z-[1000] bg-blue-600 hover:bg-blue-700 text-white rounded-full w-12 h-12 flex items-center justify-center shadow-lg transition-colors cursor-pointer"
        title={t.smartSearch ?? 'Smart Search'}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </button>
    )
  }

  return (
    <div className="absolute bottom-6 left-3 z-[1000] w-96 bg-white rounded-lg shadow-xl flex flex-col overflow-hidden border border-gray-200"
         style={{ maxHeight: '70vh' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-blue-600 text-white">
        <div className="flex items-center gap-2">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" />
            <path d="M21 21l-4.35-4.35" />
          </svg>
          <span className="font-medium text-sm">{t.smartSearch ?? 'Smart Search'}</span>
        </div>
        <button
          onClick={() => setIsOpen(false)}
          className="text-white/70 hover:text-white transition-colors cursor-pointer"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-[200px]" style={{ maxHeight: '50vh' }}>
        {messages.length === 0 && (
          <div className="text-center text-gray-400 text-xs py-8 space-y-2">
            <p className="font-medium text-gray-500">{t.chatWelcome ?? 'Ask me anything about Lisbon properties'}</p>
            <p>{t.chatExamples ?? 'Try:'}</p>
            <div className="space-y-1">
              {[
                '"T2 in Estrela under 400k"',
                '"Cheap apartments for rent in Alfama"',
                '"Large family home near Parque das Nações"',
              ].map((ex, i) => (
                <button
                  key={i}
                  onClick={() => { setInput(ex.replace(/"/g, '')); inputRef.current?.focus() }}
                  className="block w-full text-left text-blue-500 hover:text-blue-700 hover:bg-blue-50 rounded px-2 py-1 transition-colors cursor-pointer"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
              msg.role === 'user'
                ? 'bg-blue-600 text-white'
                : msg.isError
                  ? 'bg-red-50 text-red-700 border border-red-200'
                  : 'bg-gray-100 text-gray-800'
            }`}>
              {/* Message text */}
              <div className="whitespace-pre-wrap">{msg.content}</div>

              {/* Listing results */}
              {msg.listings?.length > 0 && (
                <div className="mt-2 space-y-1.5">
                  <div className="text-xs text-gray-500 font-medium">
                    {msg.count} {msg.count === 1 ? 'result' : 'results'}
                    {msg.count > msg.listings.length && ` (showing ${msg.listings.length})`}
                  </div>
                  {msg.listings.slice(0, 8).map(l => (
                    <ChatListingCard key={l.id} listing={l} />
                  ))}
                  {msg.listings.length > 8 && (
                    <div className="text-xs text-gray-400 text-center">
                      +{msg.listings.length - 8} more
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-100 rounded-lg px-4 py-2 text-sm text-gray-500">
              <span className="inline-flex gap-1">
                <span className="animate-bounce" style={{ animationDelay: '0ms' }}>.</span>
                <span className="animate-bounce" style={{ animationDelay: '150ms' }}>.</span>
                <span className="animate-bounce" style={{ animationDelay: '300ms' }}>.</span>
              </span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 p-2 flex gap-2">
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleSend() }}
          placeholder={t.chatPlaceholder ?? 'Describe what you\'re looking for...'}
          className="flex-1 text-sm px-3 py-2 rounded-lg border border-gray-200 focus:border-blue-400 focus:outline-none"
          disabled={loading}
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          className="px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors cursor-pointer"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
    </div>
  )
}
