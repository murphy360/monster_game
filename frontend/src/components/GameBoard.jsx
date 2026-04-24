import React, { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import WindowNode from './WindowNode.jsx'

const ROUND_DURATION_SECONDS = 60
const VISIBLE_DURATION_MS = 2000
const SPAWN_DELAY_MIN_MS = 160
const SPAWN_DELAY_MAX_MS = 1800
const MISS_MARKER_MS = 500

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
  monstersMeta = [],
  boardWidth = 1280,
  boardHeight = 720,
  debugBounds = false,
  renderSprites = true,
  showScore = true,
  showDownloadButton = false,
  downloadFilename = 'monster-board.png',
}) {
  // Track score and per-sprite smash counts for the current round.
  const [score, setScore] = useState(0)
  const [missedCount, setMissedCount] = useState(0)
  const [averageResponseMs, setAverageResponseMs] = useState(VISIBLE_DURATION_MS)
  const [smashCounts, setSmashCounts] = useState({})
  const [activeSpawns, setActiveSpawns] = useState({})
  const [missMarkers, setMissMarkers] = useState({})
  const [secondsLeft, setSecondsLeft] = useState(ROUND_DURATION_SECONDS)
  const boardRef = useRef(null)
  const spawnTimeoutRef = useRef(null)
  const scheduleNextSpawnRef = useRef(() => {})
  const hideTimeoutsRef = useRef({})
  const missMarkerTimeoutsRef = useRef({})
  const activeSpawnsRef = useRef({})
  const lastResolvedSpawnRef = useRef({ windowId: '', spriteUrl: '' })
  const performanceRef = useRef({ totalResponseMs: 0, resolvedCount: 0 })
  const [transform, setTransform] = useState({ scale: 1, offsetX: 0, offsetY: 0 })
  const roundOver = showScore && secondsLeft <= 0
  const namedSprites = useMemo(() => spriteUrls.reduce((acc, spriteUrl, idx) => {
    const name = monstersMeta[idx]?.name?.trim()
    if (!spriteUrl || !name) return acc
    if (acc.some((entry) => entry.spriteUrl === spriteUrl)) return acc
    acc.push({ spriteUrl, name })
    return acc
  }, []), [monstersMeta, spriteUrls])
  const uniqueSpriteUrls = useMemo(
    () => namedSprites.map((entry) => entry.spriteUrl),
    [namedSprites],
  )
  const spriteStats = useMemo(() => namedSprites.map((entry) => ({
    spriteUrl: entry.spriteUrl,
    name: entry.name,
    smashCount: smashCounts[entry.spriteUrl] ?? 0,
  })), [namedSprites, smashCounts])

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
    setMissedCount(0)
    setAverageResponseMs(VISIBLE_DURATION_MS)
    performanceRef.current = { totalResponseMs: 0, resolvedCount: 0 }
    lastResolvedSpawnRef.current = { windowId: '', spriteUrl: '' }
    setSmashCounts({})
    setMissMarkers({})
    Object.values(missMarkerTimeoutsRef.current).forEach((timeoutId) => clearTimeout(timeoutId))
    missMarkerTimeoutsRef.current = {}
    activeSpawnsRef.current = {}
    setActiveSpawns({})
    setSecondsLeft(ROUND_DURATION_SECONDS)
  }, [backgroundUrl, windows])

  const recordResolution = useCallback((elapsedMs, wasMiss) => {
    const safeElapsed = Math.max(0, Math.round(elapsedMs))
    const nextTotal = performanceRef.current.totalResponseMs + safeElapsed
    const nextResolved = performanceRef.current.resolvedCount + 1

    performanceRef.current = {
      totalResponseMs: nextTotal,
      resolvedCount: nextResolved,
    }

    setAverageResponseMs(Math.round(nextTotal / nextResolved))
    if (wasMiss) setMissedCount((prev) => prev + 1)
  }, [])

  const showMissMarker = useCallback((windowId) => {
    setMissMarkers((prev) => ({ ...prev, [windowId]: true }))

    if (missMarkerTimeoutsRef.current[windowId]) {
      clearTimeout(missMarkerTimeoutsRef.current[windowId])
    }

    missMarkerTimeoutsRef.current[windowId] = setTimeout(() => {
      setMissMarkers((prev) => {
        if (!prev[windowId]) return prev
        const next = { ...prev }
        delete next[windowId]
        return next
      })
      delete missMarkerTimeoutsRef.current[windowId]
    }, MISS_MARKER_MS)
  }, [])

  useEffect(() => {
    const hideTimeouts = hideTimeoutsRef.current
    Object.values(hideTimeouts).forEach((timeoutId) => clearTimeout(timeoutId))
    hideTimeoutsRef.current = {}

    if (spawnTimeoutRef.current) {
      clearTimeout(spawnTimeoutRef.current)
      spawnTimeoutRef.current = null
    }

    if (!renderSprites || roundOver || uniqueSpriteUrls.length === 0 || windows.length === 0) {
      activeSpawnsRef.current = {}
      scheduleNextSpawnRef.current = () => {}
      setActiveSpawns({})
      return undefined
    }

    const getRandomDelay = () => {
      const avg = performanceRef.current.resolvedCount > 0
        ? performanceRef.current.totalResponseMs / performanceRef.current.resolvedCount
        : VISIBLE_DURATION_MS

      const normalized = Math.max(
        0,
        Math.min(1, (avg - 220) / Math.max(1, VISIBLE_DURATION_MS - 220)),
      )
      const targetDelay =
        SPAWN_DELAY_MIN_MS + normalized * (SPAWN_DELAY_MAX_MS - SPAWN_DELAY_MIN_MS)

      const jitter = 0.72 + Math.random() * 0.56
      const randomizedDelay = Math.round(targetDelay * jitter)

      return Math.max(
        SPAWN_DELAY_MIN_MS,
        Math.min(SPAWN_DELAY_MAX_MS, randomizedDelay),
      )
    }

    const spawnOne = () => {
      setActiveSpawns((prev) => {
        const liveActive = activeSpawnsRef.current
        const occupiedWindowIds = new Set(Object.keys(liveActive))
        const visibleSprites = new Set(Object.values(liveActive).map((entry) => entry.spriteUrl))

        const availableWindows = windows
          .map((win, idx) => ({
            win,
            windowId: String(win.id ?? idx),
          }))
          .filter(({ windowId }) => !occupiedWindowIds.has(windowId))

        const availableSprites = uniqueSpriteUrls.filter((spriteUrl) => !visibleSprites.has(spriteUrl))

        const preferredWindows = availableWindows.filter(
          ({ windowId }) => windowId !== lastResolvedSpawnRef.current.windowId,
        )
        const preferredSprites = availableSprites.filter(
          (spriteUrl) => spriteUrl !== lastResolvedSpawnRef.current.spriteUrl,
        )

        const spawnableWindows = preferredWindows.length > 0 ? preferredWindows : availableWindows
        const spawnableSprites = preferredSprites.length > 0 ? preferredSprites : availableSprites

        if (spawnableWindows.length === 0 || spawnableSprites.length === 0) {
          return prev
        }

        const chosenWindowEntry = spawnableWindows[Math.floor(Math.random() * spawnableWindows.length)]
        const chosenWindowId = chosenWindowEntry.windowId
        const chosenSprite = spawnableSprites[Math.floor(Math.random() * spawnableSprites.length)]

        // Safety guard against racey rapid reassignments in the same window.
        if (liveActive[chosenWindowId] || hideTimeoutsRef.current[chosenWindowId]) {
          return prev
        }

        const spawnedAt = Date.now()
        const next = {
          ...liveActive,
          [chosenWindowId]: {
            spriteUrl: chosenSprite,
            spawnedAt,
          },
        }
        activeSpawnsRef.current = next

        hideTimeoutsRef.current[chosenWindowId] = setTimeout(() => {
          const activeAtWindow = activeSpawnsRef.current[chosenWindowId]
          if (activeAtWindow && activeAtWindow.spawnedAt === spawnedAt) {
            recordResolution(Date.now() - activeAtWindow.spawnedAt, true)
            showMissMarker(chosenWindowId)
            lastResolvedSpawnRef.current = {
              windowId: chosenWindowId,
              spriteUrl: activeAtWindow.spriteUrl,
            }
          }

          setActiveSpawns((current) => {
            if (!current[chosenWindowId]) return current
            const updated = { ...current }
            delete updated[chosenWindowId]
            activeSpawnsRef.current = updated
            if (Object.keys(updated).length === 0) {
              scheduleNextSpawnRef.current(120)
            }
            return updated
          })
          delete hideTimeoutsRef.current[chosenWindowId]
        }, VISIBLE_DURATION_MS)

        return next
      })

      scheduleNextSpawnRef.current(getRandomDelay())
    }

    scheduleNextSpawnRef.current = (delay = getRandomDelay()) => {
      if (spawnTimeoutRef.current) {
        clearTimeout(spawnTimeoutRef.current)
      }
      spawnTimeoutRef.current = setTimeout(spawnOne, delay)
    }

    scheduleNextSpawnRef.current(120)

    return () => {
      if (spawnTimeoutRef.current) {
        clearTimeout(spawnTimeoutRef.current)
        spawnTimeoutRef.current = null
      }
      scheduleNextSpawnRef.current = () => {}
      Object.values(hideTimeoutsRef.current).forEach((timeoutId) => clearTimeout(timeoutId))
      hideTimeoutsRef.current = {}
      Object.values(missMarkerTimeoutsRef.current).forEach((timeoutId) => clearTimeout(timeoutId))
      missMarkerTimeoutsRef.current = {}
    }
  }, [recordResolution, renderSprites, roundOver, showMissMarker, uniqueSpriteUrls, windows])

  useEffect(() => {
    if (!showScore) return undefined
    if (roundOver) return undefined

    const tickId = setInterval(() => {
      setSecondsLeft((prev) => Math.max(prev - 1, 0))
    }, 1000)

    return () => clearInterval(tickId)
  }, [showScore, roundOver])

  const handleWhack = useCallback((windowId, spriteUrl) => {
    if (roundOver) return
    if (!spriteUrl) return

    const normalizedWindowId = String(windowId)
    const activeAtWindow = activeSpawnsRef.current[normalizedWindowId]
    if (!activeAtWindow || activeAtWindow.spriteUrl !== spriteUrl) return

    recordResolution(Date.now() - activeAtWindow.spawnedAt, false)
    lastResolvedSpawnRef.current = {
      windowId: normalizedWindowId,
      spriteUrl,
    }

    const hideTimeout = hideTimeoutsRef.current[normalizedWindowId]
    if (hideTimeout) {
      clearTimeout(hideTimeout)
      delete hideTimeoutsRef.current[normalizedWindowId]
    }

    setActiveSpawns((prev) => {
      if (!prev[normalizedWindowId]) return prev
      const next = { ...prev }
      delete next[normalizedWindowId]
      activeSpawnsRef.current = next
      if (Object.keys(next).length === 0) {
        scheduleNextSpawnRef.current(120)
      }
      return next
    })

    setScore((s) => s + 1)
    setSmashCounts((prev) => ({
      ...prev,
      [spriteUrl]: (prev[spriteUrl] ?? 0) + 1,
    }))
  }, [recordResolution, roundOver])

  return (
    <div className="game-board-wrapper">
      {showScore && (
        <div className="score-bar">
          <div className="score-display">Score: {score}</div>
          <div className="missed-display">Missed: {missedCount}</div>
          <div className="avg-display">Avg React: {(averageResponseMs / 1000).toFixed(2)}s</div>
          <div className={`timer-display${roundOver ? ' done' : ''}`}>
            Time: {secondsLeft}s
          </div>
        </div>
      )}
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
            (() => {
              const windowId = String(win.id ?? idx)
              return (
            <WindowNode
              key={windowId}
              window={win}
              windowId={windowId}
              activeSpriteUrl={activeSpawns[windowId]?.spriteUrl ?? ''}
              showMissMarker={Boolean(missMarkers[windowId])}
              onWhack={handleWhack}
              debugBounds={false}
              renderSprite={renderSprites && !roundOver}
            />
              )
            })()
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

        {roundOver && (
          <div className="round-over-overlay" role="status" aria-live="polite">
            <p className="round-over-title">Round Over</p>
            <p className="round-over-score">Final Score: {score}</p>
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
      {showScore && spriteStats.length > 0 && (
        <div className="sprite-stats-panel" aria-label="Sprite smash counts">
          {spriteStats.map((stat, idx) => (
            <div key={`${stat.spriteUrl}-${idx}`} className="sprite-stat-card">
              <img src={stat.spriteUrl} alt={`Monster ${idx + 1}`} className="sprite-stat-image" />
              <div className="sprite-stat-meta">
                <span className="sprite-stat-name">{stat.name ?? `Monster ${idx + 1}`}</span>
                <span className="sprite-stat-count">Smashed: {stat.smashCount}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
