import React, { useState, useCallback, useEffect, useRef } from 'react'
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
 *   boardWidth    {number}  – Source board width in image-space pixels.
 *   boardHeight   {number}  – Source board height in image-space pixels.
 *   debugBounds   {boolean} – Whether to show debug-only window outlines.
 *   renderSprites {boolean} – Whether gameplay sprites should be animated.
 *   showScore     {boolean} – Whether to render the score display.
 */
export default function GameBoard({
  backgroundUrl,
  overlayUrl = '',
  windows = [],
  spriteUrls = [],
  boardWidth = 1280,
  boardHeight = 720,
  debugBounds = false,
  renderSprites = true,
  showScore = true,
  showDownloadButton = false,
  downloadFilename = 'monster-board.png',
}) {
  // Track which windows have been "whacked" (clicked while monster is visible)
  const [score, setScore] = useState(0)
  const [whackedIds, setWhackedIds] = useState(new Set())
  const boardRef = useRef(null)
  const [transform, setTransform] = useState({ scale: 1, offsetX: 0, offsetY: 0 })

  useEffect(() => {
    const node = boardRef.current
    if (!node) return undefined

    function computeTransform() {
      const rect = node.getBoundingClientRect()
      if (!rect.width || !rect.height || !boardWidth || !boardHeight) {
        setTransform({ scale: 1, offsetX: 0, offsetY: 0 })
        return
      }

      const scale = Math.min(rect.width / boardWidth, rect.height / boardHeight)
      const offsetX = (rect.width - boardWidth * scale) / 2
      const offsetY = (rect.height - boardHeight * scale) / 2
      setTransform({ scale, offsetX, offsetY })
    }

    computeTransform()

    const resizeObserver = new ResizeObserver(computeTransform)
    resizeObserver.observe(node)
    window.addEventListener('resize', computeTransform)

    return () => {
      resizeObserver.disconnect()
      window.removeEventListener('resize', computeTransform)
    }
  }, [boardWidth, boardHeight, backgroundUrl])

  useEffect(() => {
    setScore(0)
    setWhackedIds(new Set())
  }, [backgroundUrl, windows])

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
      {showScore && <div className="score-display">Score: {score}</div>}
      <div
        ref={boardRef}
        className="game-board"
        style={{
          aspectRatio: `${boardWidth} / ${boardHeight}`,
        }}
      >
        <img src={backgroundUrl} alt="Game background" className="game-board-image" />

        <div
          className="game-overlay"
          style={{
            width: boardWidth,
            height: boardHeight,
            transform: `translate(${transform.offsetX}px, ${transform.offsetY}px) scale(${transform.scale})`,
            transformOrigin: 'top left',
          }}
        >
          {windows.map((win, idx) => (
            <WindowNode
              key={win.id ?? idx}
              window={win}
              spriteUrl={spriteUrls[idx] ?? ''}
              isWhacked={whackedIds.has(win.id ?? idx)}
              onWhack={() => handleWhack(win.id ?? idx)}
              debugBounds={false}
              renderSprite={renderSprites}
            />
          ))}
        </div>

        {overlayUrl && <img src={overlayUrl} alt="Building overlay" className="game-board-overlay-image" />}

        {debugBounds && (
          <div
            className="debug-box-layer"
            style={{
              width: boardWidth,
              height: boardHeight,
              transform: `translate(${transform.offsetX}px, ${transform.offsetY}px) scale(${transform.scale})`,
              transformOrigin: 'top left',
            }}
          >
            {windows.map((win, idx) => (
              <div
                key={`debug-${win.id ?? idx}`}
                className="debug-box"
                style={{
                  left: win.x,
                  top: win.y,
                  width: win.width,
                  height: win.height,
                }}
              />
            ))}
          </div>
        )}
      </div>
      {showDownloadButton && backgroundUrl && (
        <div className="board-actions">
          <a className="download-btn" href={backgroundUrl} download={downloadFilename}>
            Download Image
          </a>
        </div>
      )}
    </div>
  )
}
