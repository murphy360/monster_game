import React, { useState, useCallback } from 'react'
import WindowNode from './WindowNode.jsx'

/**
 * GameBoard renders the background image and overlays WindowNode components
 * at the positions defined by the ``windows`` array.
 *
 * Props:
 *   backgroundUrl {string}  – URL or data-URI for the background image.
 *   windows       {Array}   – Array of window config objects:
 *                             { id, x, y, width, height }
 *   spriteUrls    {Array}   – Parallel array of sprite image URLs/data-URIs.
 */
export default function GameBoard({ backgroundUrl, windows = [], spriteUrls = [] }) {
  // Track which windows have been "whacked" (clicked while monster is visible)
  const [score, setScore] = useState(0)
  const [whackedIds, setWhackedIds] = useState(new Set())

  const handleWhack = useCallback((windowId) => {
    setWhackedIds((prev) => {
      if (prev.has(windowId)) return prev
      const next = new Set(prev)
      next.add(windowId)
      return next
    })
    setScore((s) => s + 1)
  }, [])

  return (
    <div className="game-board-wrapper">
      <div className="score-display">Score: {score}</div>
      <div
        className="game-board"
        style={{
          backgroundImage: `url(${backgroundUrl})`,
          backgroundSize: 'cover',
          backgroundPosition: 'center',
        }}
      >
        {windows.map((win, idx) => (
          <WindowNode
            key={win.id ?? idx}
            window={win}
            spriteUrl={spriteUrls[idx] ?? ''}
            isWhacked={whackedIds.has(win.id ?? idx)}
            onWhack={() => handleWhack(win.id ?? idx)}
          />
        ))}
      </div>
    </div>
  )
}
