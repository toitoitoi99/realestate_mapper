import { useState, useEffect } from 'react'

/**
 * Like / dislike toggle with optional comment editor.
 *
 * Variants:
 *  - "compact": just two icon buttons, no comment editor (used on listing cards)
 *  - "full":    icon buttons + comment textarea (used in listing detail panel)
 */
export default function ReactionButtons({
  reaction,                 // { reaction: 'like'|'dislike', comment } | null
  onSet,                    // (reaction, comment) => Promise
  onClear,                  // () => Promise
  variant = 'compact',
}) {
  const current = reaction?.reaction || null
  const [comment, setComment] = useState(reaction?.comment || '')
  const [editing, setEditing] = useState(false)

  useEffect(() => {
    setComment(reaction?.comment || '')
  }, [reaction?.comment])

  const handleClick = (e, target) => {
    e.stopPropagation()
    if (current === target) {
      onClear()
    } else {
      onSet(target, comment || null)
    }
  }

  const saveComment = (e) => {
    e?.stopPropagation()
    if (!current) return
    onSet(current, comment || null)
    setEditing(false)
  }

  const btnBase = 'flex items-center justify-center rounded-full transition-colors cursor-pointer'
  const sizeCls = variant === 'compact' ? 'w-6 h-6 text-xs' : 'w-9 h-9 text-base'

  return (
    <div className={variant === 'compact' ? 'flex items-center gap-1' : 'flex flex-col gap-2'}>
      <div className="flex items-center gap-1.5">
        <button
          onClick={(e) => handleClick(e, 'like')}
          title={current === 'like' ? 'Remove like' : 'Like'}
          className={`${btnBase} ${sizeCls} ${
            current === 'like'
              ? 'bg-green-100 text-green-700 ring-1 ring-green-300'
              : 'bg-gray-100 text-gray-400 hover:bg-green-50 hover:text-green-600'
          }`}
        >👍</button>
        <button
          onClick={(e) => handleClick(e, 'dislike')}
          title={current === 'dislike' ? 'Remove dislike' : 'Dislike (hides from map)'}
          className={`${btnBase} ${sizeCls} ${
            current === 'dislike'
              ? 'bg-red-100 text-red-700 ring-1 ring-red-300'
              : 'bg-gray-100 text-gray-400 hover:bg-red-50 hover:text-red-600'
          }`}
        >👎</button>
        {variant === 'full' && current && reaction?.comment && !editing && (
          <button
            onClick={(e) => { e.stopPropagation(); setEditing(true) }}
            className="text-xs text-blue-600 hover:text-blue-800 cursor-pointer ml-2"
          >Edit note</button>
        )}
        {variant === 'full' && current && !reaction?.comment && !editing && (
          <button
            onClick={(e) => { e.stopPropagation(); setEditing(true) }}
            className="text-xs text-blue-600 hover:text-blue-800 cursor-pointer ml-2"
          >Add note</button>
        )}
      </div>

      {variant === 'full' && current && (editing || reaction?.comment) && (
        <div onClick={(e) => e.stopPropagation()}>
          {editing ? (
            <>
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder={current === 'like' ? 'Why do you like it?' : 'Why don’t you like it?'}
                className="w-full text-xs border border-gray-300 rounded p-2 focus:outline-none focus:ring-1 focus:ring-blue-400"
                rows={2}
              />
              <div className="flex gap-2 mt-1">
                <button
                  onClick={saveComment}
                  className="text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700 cursor-pointer"
                >Save</button>
                <button
                  onClick={(e) => { e.stopPropagation(); setEditing(false); setComment(reaction?.comment || '') }}
                  className="text-xs text-gray-500 hover:text-gray-700 cursor-pointer"
                >Cancel</button>
              </div>
            </>
          ) : (
            <p className="text-xs text-gray-600 italic whitespace-pre-line bg-gray-50 border border-gray-200 rounded p-2">
              “{reaction.comment}”
            </p>
          )}
        </div>
      )}
    </div>
  )
}
