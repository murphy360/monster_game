import React, { useEffect, useMemo, useState } from 'react'

const FALLBACK_DIMENSION = 1280

function safeDimension(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : FALLBACK_DIMENSION
}

function WindowOverlay({ windows, boardWidth, boardHeight }) {
  if (!Array.isArray(windows) || windows.length === 0) {
    return null
  }

  return (
    <div className="review-window-overlay">
      {windows.map((win, idx) => (
        <div
          key={`review-win-${win.id ?? idx}`}
          className="review-window-box"
          style={{
            left: `${(Number(win.x || 0) / boardWidth) * 100}%`,
            top: `${(Number(win.y || 0) / boardHeight) * 100}%`,
            width: `${(Number(win.width || 0) / boardWidth) * 100}%`,
            height: `${(Number(win.height || 0) / boardHeight) * 100}%`,
          }}
          title={`#${idx + 1} (${win.x}, ${win.y}, ${win.width}, ${win.height})`}
        />
      ))}
    </div>
  )
}

function ReviewImageCard({ title, imageUrl, windows, boardWidth, boardHeight }) {
  return (
    <section className="review-image-card">
      <h3>{title}</h3>
      {imageUrl ? (
        <div className="review-image-frame" style={{ aspectRatio: `${boardWidth} / ${boardHeight}` }}>
          <img src={imageUrl} alt={title} className="review-image" />
          <WindowOverlay windows={windows} boardWidth={boardWidth} boardHeight={boardHeight} />
        </div>
      ) : (
        <p className="review-empty">Not available for this level.</p>
      )}
    </section>
  )
}

export default function ReviewPage() {
  const [levels, setLevels] = useState([])
  const [selectedLevelId, setSelectedLevelId] = useState('')
  const [selectedLevel, setSelectedLevel] = useState(null)
  const [loadingLevels, setLoadingLevels] = useState(false)
  const [loadingLevelData, setLoadingLevelData] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function fetchLevels() {
      setLoadingLevels(true)
      setError('')
      try {
        const response = await fetch('/levels')
        if (!response.ok) {
          throw new Error(`Could not load saved levels (${response.status})`)
        }
        const data = await response.json()
        if (cancelled) return

        const nextLevels = Array.isArray(data) ? data : []
        setLevels(nextLevels)
        if (nextLevels.length > 0) {
          setSelectedLevelId(nextLevels[0].id)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message)
        }
      } finally {
        if (!cancelled) {
          setLoadingLevels(false)
        }
      }
    }

    fetchLevels()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    if (!selectedLevelId) {
      setSelectedLevel(null)
      return () => {
        cancelled = true
      }
    }

    async function fetchLevel() {
      setLoadingLevelData(true)
      setError('')
      try {
        const response = await fetch(`/levels/${selectedLevelId}`)
        if (!response.ok) {
          throw new Error(`Could not load level ${selectedLevelId} (${response.status})`)
        }
        const data = await response.json()
        if (!cancelled) {
          setSelectedLevel(data)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message)
        }
      } finally {
        if (!cancelled) {
          setLoadingLevelData(false)
        }
      }
    }

    fetchLevel()

    return () => {
      cancelled = true
    }
  }, [selectedLevelId])

  const boardWidth = useMemo(
    () => safeDimension(selectedLevel?.board_width),
    [selectedLevel?.board_width],
  )
  const boardHeight = useMemo(
    () => safeDimension(selectedLevel?.board_height),
    [selectedLevel?.board_height],
  )

  const windows = Array.isArray(selectedLevel?.windows) ? selectedLevel.windows : []
  const spriteUrls = Array.isArray(selectedLevel?.sprite_urls) ? selectedLevel.sprite_urls : []
  const monstersMeta = Array.isArray(selectedLevel?.monsters_meta) ? selectedLevel.monsters_meta : []
  const maxSlots = Math.max(windows.length, spriteUrls.length, monstersMeta.length)

  return (
    <div className="review-page">
      <aside className="review-sidebar">
        <h2>Saved Levels</h2>
        {loadingLevels && <p className="review-empty">Loading levels...</p>}
        {!loadingLevels && levels.length === 0 && <p className="review-empty">No saved levels yet.</p>}
        <div className="review-level-list">
          {levels.map((level) => (
            <button
              key={level.id}
              type="button"
              className={`review-level-item ${selectedLevelId === level.id ? 'active' : ''}`}
              onClick={() => setSelectedLevelId(level.id)}
            >
              <span className="review-level-title">{level.title || 'Untitled'}</span>
              <span className="review-level-meta">
                {level.theme || 'unknown theme'} | {level.created_at ? new Date(level.created_at).toLocaleString() : 'unknown date'}
              </span>
            </button>
          ))}
        </div>
      </aside>

      <main className="review-content">
        {error && <p className="error">Error: {error}</p>}
        {loadingLevelData && <p className="review-empty">Loading level details...</p>}

        {!loadingLevelData && selectedLevel && (
          <>
            <section className="review-summary">
              <h2>{selectedLevel.title || 'Untitled Level'}</h2>
              <p>Theme: {selectedLevel.theme || 'unknown'}</p>
              <p>Level ID: {selectedLevel.id || selectedLevelId}</p>
              <p>Window key color: {selectedLevel.window_key_color || 'not provided'}</p>
              <p>
                Board: {boardWidth}x{boardHeight} | Windows: {windows.length} | Sprites: {spriteUrls.filter(Boolean).length}/{spriteUrls.length}
              </p>
            </section>

            <section className="review-image-grid">
              <ReviewImageCard
                title="Original Background"
                imageUrl={selectedLevel.original_background_url || ''}
                windows={windows}
                boardWidth={boardWidth}
                boardHeight={boardHeight}
              />
              <ReviewImageCard
                title="Processed Background"
                imageUrl={selectedLevel.background_url || ''}
                windows={windows}
                boardWidth={boardWidth}
                boardHeight={boardHeight}
              />
              <ReviewImageCard
                title="Overlay"
                imageUrl={selectedLevel.overlay_url || ''}
                windows={windows}
                boardWidth={boardWidth}
                boardHeight={boardHeight}
              />
            </section>

            <section className="review-sprites">
              <h3>Sprite Selection</h3>
              {maxSlots === 0 && <p className="review-empty">No sprites or windows available.</p>}
              {maxSlots > 0 && (
                <div className="review-sprite-grid">
                  {Array.from({ length: maxSlots }, (_, index) => {
                    const windowData = windows[index]
                    const spriteUrl = spriteUrls[index] || ''
                    const monster = monstersMeta[index]
                    const name = monster?.name || `Monster ${index + 1}`
                    const flavor = monster?.flavor || ''

                    return (
                      <article key={`review-slot-${index}`} className="review-sprite-card">
                        <div className="review-sprite-preview">
                          {spriteUrl ? <img src={spriteUrl} alt={name} /> : <span className="review-empty">No sprite</span>}
                        </div>
                        <p className="review-sprite-name">{name}</p>
                        {flavor && <p className="review-sprite-flavor">{flavor}</p>}
                        <p className="review-sprite-window">
                          {windowData
                            ? `Window ${index + 1}: (${windowData.x}, ${windowData.y}) ${windowData.width}x${windowData.height}`
                            : 'No mapped window'}
                        </p>
                      </article>
                    )
                  })}
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  )
}
