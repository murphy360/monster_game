import React, { useState, useEffect, useCallback, useRef } from 'react'
import GameBoard from './components/GameBoard.jsx'
import ReviewPage from './components/ReviewPage.jsx'
import TestPage from './components/TestPage.jsx'

const FALLBACK_BACKGROUND = 'https://placehold.co/1280x720/1a1a2e/ffffff?text=Monster+Game'
const DEFAULT_BOARD_WIDTH = 1280
const DEFAULT_BOARD_HEIGHT = 720
const REVIEW_DRAFT_KEY = 'monsterGame.reviewDraft'
const IMAGE_GENERATION_TIMEOUT_SECONDS = (() => {
  const parsed = Number(import.meta.env.VITE_IMAGE_GENERATION_TIMEOUT_SECONDS)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 150
})()

function getSafeDimension(value, fallback) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function formatSeconds(totalSeconds) {
  const safe = Math.max(0, Math.floor(totalSeconds || 0))
  const minutes = Math.floor(safe / 60)
  const seconds = safe % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export default function App() {
  const pathname = typeof window !== 'undefined' ? window.location.pathname : '/'
  const queryLevelId = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search).get('levelId') || ''
    : ''
  const isTestPage = pathname === '/test'
  const isReviewPage = pathname === '/review'
  const [levelData, setLevelData] = useState(null)
  const [spriteUrls, setSpriteUrls] = useState([])
  const [monstersMeta, setMonstersMeta] = useState([]) // [{name, flavor}]
  const [loading, setLoading] = useState(false)
  const [loadingPhase, setLoadingPhase] = useState('')
  const [error, setError] = useState(null)
  const [theme, setTheme] = useState('haunted house')
  const [showDebugBounds] = useState(import.meta.env.VITE_DEBUG_BOUNDS === 'true')
  const [makingSausage, setMakingSausage] = useState(false)
  const [savedLevels, setSavedLevels] = useState([])
  const [showLevelsList, setShowLevelsList] = useState(false)
  const [backgroundAttempt, setBackgroundAttempt] = useState(0)
  const [backgroundMaxAttempts, setBackgroundMaxAttempts] = useState(2)
  const [backgroundStatus, setBackgroundStatus] = useState('')
  const [manualSelectionMode, setManualSelectionMode] = useState(false)
  const [makingSausageBackground, setMakingSausageBackground] = useState(null)
  const [makingSausageBackgroundAttempts, setMakingSausageBackgroundAttempts] = useState([])
  const [selectedPreviewColor, setSelectedPreviewColor] = useState('')
  const [selectedPreviewPoint, setSelectedPreviewPoint] = useState(null)
  const [generationCountdown, setGenerationCountdown] = useState(IMAGE_GENERATION_TIMEOUT_SECONDS)
  const [generationTimedOut, setGenerationTimedOut] = useState(false)
  const queryLevelLoadedRef = useRef('')

  const updateReviewDraft = useCallback((patch) => {
    try {
      const existingRaw = localStorage.getItem(REVIEW_DRAFT_KEY)
      const existing = existingRaw ? JSON.parse(existingRaw) : {}
      const next = {
        id: '__draft__',
        title: `${theme || 'Untitled'} (In Progress)`,
        theme,
        created_at: new Date().toISOString(),
        board_width: DEFAULT_BOARD_WIDTH,
        board_height: DEFAULT_BOARD_HEIGHT,
        ...existing,
        ...patch,
      }
      localStorage.setItem(REVIEW_DRAFT_KEY, JSON.stringify(next))
    } catch (_) {
      // Non-blocking: review draft persistence should never break generation.
    }
  }, [theme])

  const fetchSavedLevels = useCallback(async () => {
    try {
      const res = await fetch('/levels')
      if (res.ok) setSavedLevels(await res.json())
    } catch (_) {}
  }, [])

  useEffect(() => {
    if (!isTestPage && !isReviewPage) fetchSavedLevels()
  }, [isTestPage, isReviewPage, fetchSavedLevels])

  // When all sprites have arrived but the background is still processing,
  // update the status so the user knows what's left.
  useEffect(() => {
    if (
      loading &&
      spriteUrls.length > 0 &&
      spriteUrls.every((url) => url !== '')
    ) {
      setLoadingPhase('All monsters ready — finishing the background scene…')
    }
  }, [loading, spriteUrls])

  useEffect(() => {
    if (!loading) return undefined

    const timer = window.setInterval(() => {
      setGenerationCountdown((prev) => {
        const next = Math.max(0, prev - 1)
        if (next === 0) {
          setGenerationTimedOut(true)
        }
        return next
      })
    }, 1000)

    return () => {
      window.clearInterval(timer)
    }
  }, [loading])

  async function handleGenerateLevel() {
    setLoading(true)
    setError(null)
    setLevelData(null)
    setSpriteUrls([])
    setMonstersMeta([])
    setLoadingPhase('Preparing monsters…')
    setBackgroundAttempt(0)
    setBackgroundMaxAttempts(2)
    setBackgroundStatus('')
    setMakingSausageBackground(null)
    setMakingSausageBackgroundAttempts([])
    setSelectedPreviewColor('')
    setSelectedPreviewPoint(null)
    setGenerationCountdown(IMAGE_GENERATION_TIMEOUT_SECONDS)
    setGenerationTimedOut(false)
    updateReviewDraft({
      sprite_urls: [],
      monsters_meta: [],
      windows: [],
      background_url: '',
      original_background_url: '',
      overlay_url: '',
      window_key_color: '',
      color_decision: null,
      generation_phase: 'starting',
    })
    try {
      console.log('🎮 Starting level generation request...')
      const requestPayload = { theme, generate_images: true, making_sausage: makingSausage }
      console.log('🚀 Request payload:', requestPayload)
      const res = await fetch('/generate-level', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestPayload),
      })
      console.log('Response received:', res.status, res.statusText)
      if (!res.ok) {
        const text = await res.text()
        console.error('Error response body:', text)
        throw new Error(`Server error: ${res.status} - ${text}`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let pendingEvent = null
      let eventCount = 0

      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          console.log(`✅ Stream ended. Total events received: ${eventCount}`)
          console.table({
            'Requests': 'making_sausage=' + makingSausage,
            'Events': eventCount,
          })
          break
        }
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            pendingEvent = line.slice(7).trim()
            console.log(`📬 Event: ${pendingEvent}`)
          } else if (line.startsWith('data: ')) {
            const payload = JSON.parse(line.slice(6))
            eventCount++
            console.log(`📦 [#${eventCount}] ${pendingEvent}:`, payload)
            if (pendingEvent === 'sprite_count') {
              const nextMonsters = payload.monsters ?? []
              setMonstersMeta(nextMonsters)
              setSpriteUrls(new Array(payload.count ?? 0).fill(''))
              setLoadingPhase(`Summoning ${payload.count} monsters…`)
              updateReviewDraft({
                monsters_meta: nextMonsters,
                sprite_urls: new Array(payload.count ?? 0).fill(''),
                generation_phase: 'sprites',
              })
            } else if (pendingEvent === 'sprite') {
              setSpriteUrls((prev) => {
                const next = [...prev]
                if (payload.url) {
                  next[payload.index] = payload.url
                }
                updateReviewDraft({
                  sprite_urls: next,
                  generation_phase: 'sprites',
                })
                return next
              })
            } else if (pendingEvent === 'background_attempt') {
              setBackgroundAttempt(payload.attempt ?? 0)
              if (payload.max_attempts) {
                setBackgroundMaxAttempts(payload.max_attempts)
              }
              setBackgroundStatus(payload.status ?? '')
              updateReviewDraft({
                background_attempt: payload.attempt ?? 0,
                background_max_attempts: payload.max_attempts ?? backgroundMaxAttempts,
                background_status: payload.status ?? '',
                generation_phase: 'background',
              })
            } else if (pendingEvent === 'background_image') {
              // Show background immediately when Making Sausage is enabled
              console.log('🖼️ Background image event received:', payload)
              if (payload.url) {
                console.log('🎨 Setting Making Sausage background:', payload.url.substring(0, 100))
                setMakingSausageBackground(payload.url)
                setLoadingPhase('Background image received — detecting windows and validating mask color…')
                setSelectedPreviewColor('')
                setSelectedPreviewPoint(null)
                setMakingSausageBackgroundAttempts((prev) => {
                  const attempt = payload.attempt ?? prev.length + 1
                  const next = prev.filter((item) => item.attempt !== attempt)
                  next.push({
                    attempt,
                    url: payload.url,
                    status: payload.status ?? '',
                    keyColor: payload.window_key_color ?? '',
                  })
                  return next.sort((a, b) => a.attempt - b.attempt)
                })
                updateReviewDraft({
                  original_background_url: payload.url,
                  background_url: payload.url,
                  window_key_color: payload.window_key_color ?? '',
                  color_decision: payload.color_decision ?? null,
                  generation_phase: 'background',
                })
              }
            } else if (pendingEvent === 'layout') {
              if (payload.generation_warning) {
                setError(`Generation warning: ${payload.generation_warning}`)
              }

              updateReviewDraft({
                ...payload,
                generation_phase: payload.manual_selection_required ? 'manual-selection' : 'layout',
              })

              if (payload.manual_selection_required) {
                setLevelData(payload)
                setManualSelectionMode(true)
                setGenerationTimedOut(false)
              } else {
                setLevelData(payload)
                setLoading(false)
                setLoadingPhase('')
                setBackgroundAttempt(0)
                setBackgroundMaxAttempts(2)
                setBackgroundStatus('')
                setGenerationTimedOut(false)
              }
            } else if (pendingEvent === 'done') {
              fetchSavedLevels()
            }
            pendingEvent = null
          }
        }
      }
    } catch (err) {
      console.error('❌ Generation error:', err)
      setError(err.message)
      setLoadingPhase('')
      setLoading(false)
      setGenerationTimedOut(false)
    }
  }

  function rgbToHex(r, g, b) {
    const toHex = (value) => Math.max(0, Math.min(255, value)).toString(16).padStart(2, '0')
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`.toUpperCase()
  }

  function handlePreviewImageClick(event) {
    const img = event.currentTarget
    if (!img || !img.naturalWidth || !img.naturalHeight) return

    const rect = img.getBoundingClientRect()
    if (!rect.width || !rect.height) return

    const clickX = event.clientX - rect.left
    const clickY = event.clientY - rect.top
    const pixelX = Math.max(0, Math.min(img.naturalWidth - 1, Math.round((clickX / rect.width) * (img.naturalWidth - 1))))
    const pixelY = Math.max(0, Math.min(img.naturalHeight - 1, Math.round((clickY / rect.height) * (img.naturalHeight - 1))))

    const canvas = document.createElement('canvas')
    canvas.width = img.naturalWidth
    canvas.height = img.naturalHeight
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return
    ctx.drawImage(img, 0, 0)
    const data = ctx.getImageData(pixelX, pixelY, 1, 1).data
    const hex = rgbToHex(data[0], data[1], data[2])

    setSelectedPreviewColor(hex)
    setSelectedPreviewPoint({ x: pixelX, y: pixelY })
  }

  async function handleLoadLevel(id) {
    setLoading(true)
    setError(null)
    setShowLevelsList(false)
    try {
      const res = await fetch(`/levels/${id}`)
      if (!res.ok) throw new Error(`Server error: ${res.status}`)
      const data = await res.json()

      // Gameplay safety: if a loaded level still points at the original image,
      // regenerate the processed background from original + saved key color.
      let normalized = data
      const hasOriginal = Boolean(data?.original_background_url)
      const hasKeyColor = Boolean(data?.window_key_color)
      const needsProcessedFallback = hasOriginal && hasKeyColor && (
        !data?.background_url || data.background_url === data.original_background_url
      )

      if (needsProcessedFallback) {
        try {
          const processedRes = await fetch('/serve-assets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              image_url: data.original_background_url,
              window_key_color: data.window_key_color,
              character_descriptions: [],
            }),
          })
          if (processedRes.ok) {
            const processed = await processedRes.json()
            normalized = {
              ...data,
              cropped_background_url: processed?.cropped_background_url || data.cropped_background_url,
              background_url: processed?.processed_background_url || data.background_url,
              board_width: Number.isFinite(Number(processed?.board_width)) && Number(processed.board_width) > 0
                ? Number(processed.board_width)
                : data.board_width,
              board_height: Number.isFinite(Number(processed?.board_height)) && Number(processed.board_height) > 0
                ? Number(processed.board_height)
                : data.board_height,
              // Keep saved windows if present; otherwise use newly outlined windows.
              windows: Array.isArray(data?.windows) && data.windows.length > 0
                ? data.windows
                : (Array.isArray(processed?.windows) ? processed.windows : data.windows),
            }
          }
        } catch (_) {
          // Non-blocking: continue with whatever the saved payload contains.
        }
      }

      setLevelData(normalized)
      const nextMonsters = normalized.monsters_meta ?? []
      setMonstersMeta(nextMonsters)
      setSpriteUrls(normalized.sprite_urls ?? [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (isTestPage || isReviewPage) return
    if (!queryLevelId) return
    if (queryLevelLoadedRef.current === queryLevelId) return

    queryLevelLoadedRef.current = queryLevelId
    handleLoadLevel(queryLevelId)
  }, [isTestPage, isReviewPage, queryLevelId])

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-title-row">
          <h1>🧟 Cathy's Monster Masher</h1>
          <nav className="page-nav">
            <a href="/" className={!isTestPage && !isReviewPage ? 'active' : ''}>Game</a>
            <a href="/test" className={isTestPage ? 'active' : ''}>Test</a>
            <a href="/review" className={isReviewPage ? 'active' : ''}>Review</a>
          </nav>
        </div>
        <div className="controls">
          {!isTestPage && !isReviewPage && (
            <>
              <input
                type="text"
                value={theme}
                onChange={(e) => setTheme(e.target.value)}
                placeholder="Enter level theme…"
                className="theme-input"
              />
              <button onClick={handleGenerateLevel} disabled={loading} className="generate-btn">
                {loading ? 'Generating…' : 'Generate Level'}
              </button>
              <label className="making-sausage-toggle">
                <input
                  type="checkbox"
                  checked={makingSausage}
                  onChange={(e) => setMakingSausage(e.target.checked)}
                  disabled={loading}
                />
                <span>Making Sausage</span>
              </label>
              {savedLevels.length > 0 && (
                <button
                  className="generate-btn secondary-btn"
                  onClick={() => setShowLevelsList((v) => !v)}
                  disabled={loading}
                >
                  {showLevelsList ? 'Hide Levels' : `Saved Levels (${savedLevels.length})`}
                </button>
              )}
            </>
          )}
        </div>
        {error && <p className="error">Error: {error}</p>}
      </header>

      {isTestPage ? (
        <TestPage debugBounds={showDebugBounds} />
      ) : isReviewPage ? (
        <ReviewPage />
      ) : showLevelsList ? (
        <div className="levels-list">
          <h2>Saved Levels</h2>
          {savedLevels.map((lvl) => (
            <button
              key={lvl.id}
              className="level-card"
              onClick={() => handleLoadLevel(lvl.id)}
              disabled={loading}
            >
              <span className="level-card-title">{lvl.title}</span>
              <span className="level-card-meta">
                {lvl.theme} &mdash; v{lvl.version ?? 1}
                {typeof lvl.versions_count === 'number' && lvl.versions_count > 1 ? ` (${lvl.versions_count} versions)` : ''}
                {' | '}updated {new Date(lvl.updated_at || lvl.created_at).toLocaleString()}
              </span>
            </button>
          ))}
        </div>
      ) : levelData ? (
        manualSelectionMode ? (
          <div className="manual-selection-screen">
            <div className="manual-selection-panel">
              <h2>Window Detection Failed</h2>
              <p>
                Automatic window detection couldn't find exactly where the windows are. 
                Using fallback windows based on the level configuration.
              </p>
              <div className="manual-selection-preview" style={{ aspectRatio: `${levelData.board_width} / ${levelData.board_height}` }}>
                <img src={levelData.background_url || FALLBACK_BACKGROUND} alt="Background" />
              </div>
              <div className="manual-selection-controls">
                <button
                  className="generate-btn"
                  onClick={() => {
                    setManualSelectionMode(false)
                    setLoading(false)
                  }}
                >
                  Continue with These Windows
                </button>
                <p className="manual-selection-info">
                  {levelData.windows?.length || 0} windows detected: {levelData.windows?.map((w, i) => i + 1).join(', ') || 'None'}
                </p>
              </div>
            </div>
          </div>
        ) : (
          <GameBoard
            backgroundUrl={levelData.background_url || FALLBACK_BACKGROUND}
            overlayUrl={showDebugBounds ? (levelData.overlay_url || '') : ''}
            windows={levelData.windows}
            spriteUrls={spriteUrls}
            monstersMeta={monstersMeta}
            boardWidth={getSafeDimension(levelData.board_width, DEFAULT_BOARD_WIDTH)}
            boardHeight={getSafeDimension(levelData.board_height, DEFAULT_BOARD_HEIGHT)}
            debugBounds={showDebugBounds}
            showDownloadButton={showDebugBounds}
            downloadFilename={`${theme || 'monster-level'}.png`}
          />
        )
      ) : loading && spriteUrls.length > 0 ? (
        <div className="sprite-preview-screen">
          <p className="loading-title">{loadingPhase}</p>
          <p className="loading-phase">Building the background scene at the same time…</p>
          <div className={`loading-timeout${generationTimedOut ? ' timeout-reached' : ''}`}>
            <span>Image generation timeout:</span>
            <strong>{formatSeconds(generationCountdown)}</strong>
          </div>
          {makingSausage && makingSausageBackground && (
            <div className="making-sausage-background-preview">
              <h3>
                Generated Background (Attempt {backgroundAttempt || makingSausageBackgroundAttempts.length}
                {backgroundMaxAttempts ? ` of ${backgroundMaxAttempts}` : ''})
              </h3>
              {(() => {
                const activeAttempt = makingSausageBackgroundAttempts.find((entry) => entry.url === makingSausageBackground)
                const activeColor = activeAttempt?.keyColor || ''
                return (
                  <div className="making-sausage-color-stack">
                    {activeColor && (
                      <div className="making-sausage-color-row">
                        <span>Matching Key Color:</span>
                        <span className="making-sausage-color-chip">
                          <span
                            className="making-sausage-color-swatch"
                            style={{ backgroundColor: activeColor }}
                            aria-hidden="true"
                          />
                          {activeColor}
                        </span>
                      </div>
                    )}
                    <div className="making-sausage-color-row">
                      <span>Selected Color:</span>
                      {selectedPreviewColor ? (
                        <span className="making-sausage-color-chip">
                          <span
                            className="making-sausage-color-swatch"
                            style={{ backgroundColor: selectedPreviewColor }}
                            aria-hidden="true"
                          />
                          {selectedPreviewColor}
                          {selectedPreviewPoint && (
                            <span className="making-sausage-point">@ ({selectedPreviewPoint.x}, {selectedPreviewPoint.y})</span>
                          )}
                        </span>
                      ) : (
                        <span className="making-sausage-color-empty">Click image to sample</span>
                      )}
                    </div>
                  </div>
                )
              })()}
              <img
                src={makingSausageBackground}
                alt="Generated background"
                onClick={handlePreviewImageClick}
              />
              {makingSausageBackgroundAttempts.length > 1 && (
                <div className="making-sausage-attempt-strip">
                  {makingSausageBackgroundAttempts.map((entry) => (
                    <div
                      key={entry.attempt}
                      className={`making-sausage-attempt-chip${entry.url === makingSausageBackground ? ' active' : ''}`}
                    >
                      <span>Attempt {entry.attempt}</span>
                      {entry.keyColor && <span className="making-sausage-attempt-color">{entry.keyColor}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="sprite-preview-grid">
            {spriteUrls.map((url, idx) => {
              const meta = monstersMeta[idx]
              return (
                <div key={idx} className={`sprite-preview-card${url ? ' loaded' : ''}`}>
                  <div className="sprite-preview-image">
                    {url
                      ? <img src={url} alt={meta?.name ?? `Monster ${idx + 1}`} />
                      : <span className="sprite-preview-pending" />
                    }
                  </div>
                  {meta && (
                    <div className="sprite-preview-info">
                      <span className="sprite-preview-name">{meta.name}</span>
                      {meta.flavor && <span className="sprite-preview-flavor">{meta.flavor}</span>}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ) : (
        <div className="placeholder">
          {loading ? (
            <div className="loading-placeholder">
              <p className="loading-title">Generating your level…</p>
              <p className="loading-phase">{loadingPhase}</p>
              <div className={`loading-timeout${generationTimedOut ? ' timeout-reached' : ''}`}>
                <span>Image generation timeout:</span>
                <strong>{formatSeconds(generationCountdown)}</strong>
              </div>
              <div className="loading-pulse-bar"><div className="loading-pulse-fill" /></div>
              {spriteUrls.length > 0 && spriteUrls.every((url) => url !== '') && (
                <div className="loading-background-status">
                  <div className="loading-spinner" />
                  <div className="loading-background-info">
                    <span>Processing background and window detection…</span>
                    {backgroundAttempt > 0 && (
                      <span className="loading-background-attempt">
                        Attempt {backgroundAttempt} of {backgroundMaxAttempts}
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p>Enter a theme and click <strong>Generate Level</strong> to start.</p>
          )}
        </div>
      )}
    </div>
  )
}
