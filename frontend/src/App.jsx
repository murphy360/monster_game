import React, { useState, useEffect, useCallback } from 'react'
import GameBoard from './components/GameBoard.jsx'
import ReviewPage from './components/ReviewPage.jsx'
import TestPage from './components/TestPage.jsx'

const FALLBACK_BACKGROUND = 'https://placehold.co/1280x720/1a1a2e/ffffff?text=Monster+Game'
const DEFAULT_BOARD_WIDTH = 1280
const DEFAULT_BOARD_HEIGHT = 720

function getSafeDimension(value, fallback) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

export default function App() {
  const pathname = typeof window !== 'undefined' ? window.location.pathname : '/'
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
  const [savedLevels, setSavedLevels] = useState([])
  const [showLevelsList, setShowLevelsList] = useState(false)

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

  async function handleGenerateLevel() {
    setLoading(true)
    setError(null)
    setLevelData(null)
    setSpriteUrls([])
    setMonstersMeta([])
    setLoadingPhase('Preparing monsters…')
    try {
      const res = await fetch('/generate-level', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme, generate_images: true }),
      })
      if (!res.ok) throw new Error(`Server error: ${res.status}`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let pendingEvent = null

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            pendingEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            const payload = JSON.parse(line.slice(6))
            if (pendingEvent === 'sprite_count') {
              const nextMonsters = payload.monsters ?? []
              setMonstersMeta(nextMonsters)
              setSpriteUrls(new Array(payload.count ?? 0).fill(''))
              setLoadingPhase(`Summoning ${payload.count} monsters…`)
            } else if (pendingEvent === 'sprite') {
              setSpriteUrls((prev) => {
                const next = [...prev]
                if (payload.url) {
                  next[payload.index] = payload.url
                }
                return next
              })
            } else if (pendingEvent === 'layout') {
              if (payload.generation_warning) {
                setError(`Generation warning: ${payload.generation_warning}`)
              }

              setLevelData(payload)
              setLoading(false)
              setLoadingPhase('')
            } else if (pendingEvent === 'done') {
              fetchSavedLevels()
            }
            pendingEvent = null
          }
        }
      }
    } catch (err) {
      setError(err.message)
      setLoadingPhase('')
      setLoading(false)
    }
  }

  async function handleLoadLevel(id) {
    setLoading(true)
    setError(null)
    setShowLevelsList(false)
    try {
      const res = await fetch(`/levels/${id}`)
      if (!res.ok) throw new Error(`Server error: ${res.status}`)
      const data = await res.json()
      setLevelData(data)
      const nextMonsters = data.monsters_meta ?? []
      setMonstersMeta(nextMonsters)
      setSpriteUrls((data.sprite_urls ?? []).filter(Boolean))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

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
                {lvl.theme} &mdash; {new Date(lvl.created_at).toLocaleString()}
              </span>
            </button>
          ))}
        </div>
      ) : levelData ? (
        <GameBoard
          backgroundUrl={levelData.background_url || FALLBACK_BACKGROUND}
          overlayUrl={levelData.overlay_url || ''}
          windows={levelData.windows}
          spriteUrls={spriteUrls}
          monstersMeta={monstersMeta}
          boardWidth={getSafeDimension(levelData.board_width, DEFAULT_BOARD_WIDTH)}
          boardHeight={getSafeDimension(levelData.board_height, DEFAULT_BOARD_HEIGHT)}
          debugBounds={showDebugBounds}
          showDownloadButton={showDebugBounds}
          downloadFilename={`${theme || 'monster-level'}.png`}
        />
      ) : loading && spriteUrls.length > 0 ? (
        <div className="sprite-preview-screen">
          <p className="loading-title">{loadingPhase}</p>
          <p className="loading-phase">Building the background scene at the same time…</p>
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
              <div className="loading-pulse-bar"><div className="loading-pulse-fill" /></div>
            </div>
          ) : (
            <p>Enter a theme and click <strong>Generate Level</strong> to start.</p>
          )}
        </div>
      )}
    </div>
  )
}
