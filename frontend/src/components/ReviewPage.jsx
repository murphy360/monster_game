import React, { useEffect, useMemo, useState } from 'react'

const REVIEW_DRAFT_KEY = 'monsterGame.reviewDraft'
const REVIEW_DRAFT_ID = '__draft__'

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

function ReviewImageCard({
  title,
  imageUrl,
  windows,
  boardWidth,
  boardHeight,
  showWindows = false,
  onImageClick,
}) {
  return (
    <section className="review-image-card">
      <h3>{title}</h3>
      {imageUrl ? (
        <div className="review-image-frame" style={{ aspectRatio: `${boardWidth} / ${boardHeight}` }}>
          <img
            src={imageUrl}
            alt={title}
            className={`review-image${onImageClick ? ' clickable' : ''}`}
            onClick={onImageClick}
          />
          {showWindows && (
            <WindowOverlay windows={windows} boardWidth={boardWidth} boardHeight={boardHeight} />
          )}
        </div>
      ) : (
        <p className="review-empty">Not available for this level.</p>
      )}
    </section>
  )
}

function ColorChip({ color }) {
  if (!color) {
    return <span className="review-color-chip review-color-chip-empty">n/a</span>
  }
  return (
    <span className="review-color-chip" title={color}>
      <span className="review-color-swatch" style={{ backgroundColor: color }} />
      {color}
    </span>
  )
}

export default function ReviewPage() {
  const [levels, setLevels] = useState([])
  const [selectedLevelId, setSelectedLevelId] = useState('')
  const [selectedLevel, setSelectedLevel] = useState(null)
  const [loadingLevels, setLoadingLevels] = useState(false)
  const [loadingLevelData, setLoadingLevelData] = useState(false)
  const [error, setError] = useState('')
  const [selectedPreviewColor, setSelectedPreviewColor] = useState('')
  const [previewProcessing, setPreviewProcessing] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [previewHasRun, setPreviewHasRun] = useState(false)
  const [previewWindows, setPreviewWindows] = useState([])
  const [previewProcessedBackgroundUrl, setPreviewProcessedBackgroundUrl] = useState('')
  const [previewCandidateRows, setPreviewCandidateRows] = useState([])
  const [previewSaving, setPreviewSaving] = useState(false)
  const [previewSaveNote, setPreviewSaveNote] = useState('')

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
        let withDraft = nextLevels
        try {
          const rawDraft = localStorage.getItem(REVIEW_DRAFT_KEY)
          const draft = rawDraft ? JSON.parse(rawDraft) : null
          if (draft && typeof draft === 'object') {
            withDraft = [
              {
                id: REVIEW_DRAFT_ID,
                title: draft.title || 'Current Generation (In Progress)',
                theme: draft.theme || 'current generation',
                created_at: draft.created_at || '',
              },
              ...nextLevels,
            ]
          }
        } catch (_) {
          // Ignore invalid local draft payloads.
        }

        setLevels(withDraft)
        if (withDraft.length > 0) {
          setSelectedLevelId(withDraft[0].id)
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

    if (selectedLevelId === REVIEW_DRAFT_ID) {
      try {
        const rawDraft = localStorage.getItem(REVIEW_DRAFT_KEY)
        const draft = rawDraft ? JSON.parse(rawDraft) : null
        setSelectedLevel(draft)
      } catch (_) {
        setSelectedLevel(null)
      }
      setLoadingLevelData(false)
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

  useEffect(() => {
    // Reset preview mode when switching levels.
    setSelectedPreviewColor('')
    setPreviewProcessing(false)
    setPreviewError('')
    setPreviewHasRun(false)
    setPreviewWindows([])
    setPreviewProcessedBackgroundUrl('')
    setPreviewCandidateRows([])
    setPreviewSaving(false)
    setPreviewSaveNote('')
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
  const originalImageUrl = selectedLevel?.original_background_url || selectedLevel?.background_url || ''
  const colorDecision = selectedLevel?.color_decision && typeof selectedLevel.color_decision === 'object'
    ? selectedLevel.color_decision
    : null
  const selectedWindows = Array.isArray(colorDecision?.selected_windows)
    ? colorDecision.selected_windows
    : []
  const reviewWindows = windows.length > 0 ? windows : selectedWindows
  const supportedKeyColors = Array.isArray(colorDecision?.supported_key_colors)
    ? colorDecision.supported_key_colors
    : []
  const candidateScores = Array.isArray(colorDecision?.candidate_scores)
    ? colorDecision.candidate_scores
    : []

  function rgbToHex(r, g, b) {
    const toHex = (value) => Math.max(0, Math.min(255, value)).toString(16).padStart(2, '0')
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`.toUpperCase()
  }

  function calculatePreviewScore(candidateWindows, width, height) {
    if (!Array.isArray(candidateWindows) || candidateWindows.length === 0) return 0
    const imageArea = Math.max(1, width * height)
    const areas = candidateWindows.map((win) => Number(win.width || 0) * Number(win.height || 0))
    const totalArea = areas.reduce((sum, area) => sum + area, 0)
    const largestArea = Math.max(...areas, 0)
    const windowCount = candidateWindows.length

    if (largestArea > imageArea * 0.45) return 0
    if (totalArea < 1000) return 0

    const countFactor = windowCount >= 4 && windowCount <= 30 ? 1.0 : 0.55
    return totalArea * countFactor - largestArea * 0.25
  }

  function upsertPreviewCandidate(keyColor, values = {}) {
    setPreviewCandidateRows((prev) => {
      const next = prev.filter((row) => String(row.key_color || '').toUpperCase() !== keyColor)
      next.push({
        key_color: keyColor,
        score: null,
        window_count: null,
        total_area: null,
        largest_area: null,
        has_key_color_conflict: null,
        preview_status: 'pending',
        ...values,
      })
      return next
    })
  }

  async function runPreviewReprocess(color) {
    if (!originalImageUrl) return
    setPreviewProcessing(true)
    setPreviewError('')
    upsertPreviewCandidate(color, { preview_status: 'processing' })

    try {
      const response = await fetch('/serve-assets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_url: originalImageUrl,
          window_key_color: color,
          character_descriptions: [],
        }),
      })
      if (!response.ok) {
        throw new Error(`Preview reprocess failed (${response.status})`)
      }

      const payload = await response.json()
      const windowsFromPreview = Array.isArray(payload.windows) ? payload.windows : []
      const totalArea = windowsFromPreview.reduce(
        (sum, win) => sum + Number(win.width || 0) * Number(win.height || 0),
        0,
      )
      const largestArea = windowsFromPreview.reduce(
        (largest, win) => Math.max(largest, Number(win.width || 0) * Number(win.height || 0)),
        0,
      )
      const score = calculatePreviewScore(windowsFromPreview, boardWidth, boardHeight)

      setPreviewHasRun(true)
      setPreviewWindows(windowsFromPreview)
      setPreviewProcessedBackgroundUrl(payload.processed_background_url || '')
      upsertPreviewCandidate(color, {
        score,
        window_count: windowsFromPreview.length,
        total_area: totalArea,
        largest_area: largestArea,
        has_key_color_conflict: null,
        preview_status: 'ready',
      })
    } catch (err) {
      setPreviewHasRun(false)
      setPreviewWindows([])
      setPreviewProcessedBackgroundUrl('')
      setPreviewError(err.message)
      upsertPreviewCandidate(color, { preview_status: 'error' })
    } finally {
      setPreviewProcessing(false)
    }
  }

  function handleOriginalImageClick(event) {
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
    runPreviewReprocess(hex)
  }

  const displayCandidates = [...candidateScores, ...previewCandidateRows]
  const displayWindows = previewHasRun ? previewWindows : reviewWindows
  const activePreviewCandidate = previewCandidateRows.find(
    (row) => String(row?.key_color || '').toUpperCase() === String(selectedPreviewColor || '').toUpperCase(),
  ) || null
  const transformedImageUrl = previewHasRun
    ? (previewProcessedBackgroundUrl || originalImageUrl)
    : (selectedLevel?.background_url || '')
  const spriteUrls = Array.isArray(selectedLevel?.sprite_urls) ? selectedLevel.sprite_urls : []
  const monstersMeta = Array.isArray(selectedLevel?.monsters_meta) ? selectedLevel.monsters_meta : []
  const spriteEntries = Array.from(
    { length: Math.max(spriteUrls.length, monstersMeta.length) },
    (_, index) => ({
      spriteUrl: spriteUrls[index] || '',
      monster: monstersMeta[index] || null,
      index,
    }),
  )

  async function applyPreviewPermanent() {
    if (!selectedLevelId || selectedLevelId === REVIEW_DRAFT_ID) {
      setPreviewSaveNote('Draft entries in local storage cannot be persisted to backend levels.')
      return
    }
    if (!previewHasRun || !previewProcessedBackgroundUrl || !selectedPreviewColor) {
      setPreviewSaveNote('Run a preview first, then apply it.')
      return
    }

    setPreviewSaving(true)
    setPreviewSaveNote('')
    try {
      const previewCandidate = activePreviewCandidate
        ? {
          key_color: selectedPreviewColor,
          score: activePreviewCandidate.score,
          window_count: activePreviewCandidate.window_count,
          total_area: activePreviewCandidate.total_area,
          largest_area: activePreviewCandidate.largest_area,
          has_key_color_conflict: activePreviewCandidate.has_key_color_conflict,
        }
        : null

      const response = await fetch(`/levels/${selectedLevelId}/apply-preview`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          window_key_color: selectedPreviewColor,
          windows: previewWindows,
          processed_background_url: previewProcessedBackgroundUrl,
          preview_candidate: previewCandidate,
        }),
      })

      if (!response.ok) {
        throw new Error(`Could not persist preview (${response.status})`)
      }

      const updatedLevel = await response.json()
      setSelectedLevel(updatedLevel)
      setPreviewHasRun(false)
      setPreviewWindows([])
      setPreviewProcessedBackgroundUrl('')
      setPreviewCandidateRows([])
      setPreviewSaveNote('Preview applied permanently to this level.')
    } catch (err) {
      setPreviewSaveNote(err.message)
    } finally {
      setPreviewSaving(false)
    }
  }

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
                {level.theme || 'unknown theme'}
                {typeof level.version === 'number' ? ` | v${level.version}` : ''}
                {typeof level.versions_count === 'number' && level.versions_count > 1 ? ` (${level.versions_count} versions)` : ''}
                {' | '}
                {level.updated_at
                  ? new Date(level.updated_at).toLocaleString()
                  : (level.created_at ? new Date(level.created_at).toLocaleString() : 'unknown date')}
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
                View mode: {selectedLevelId === REVIEW_DRAFT_ID ? 'Draft (local only)' : 'Saved level'}
              </p>
              <p>
                Board: {boardWidth}x{boardHeight} | Windows: {windows.length} | Sprites: {spriteUrls.filter(Boolean).length}/{spriteUrls.length}
              </p>
              {selectedLevelId !== REVIEW_DRAFT_ID && selectedLevel?.id && (
                <p>
                  <a
                    href={`/?levelId=${encodeURIComponent(selectedLevel.id)}`}
                    className="review-open-game-link"
                  >
                    Open This Exact Level In Game
                  </a>
                </p>
              )}
            </section>

            <section className="review-color-decision">
              <h3>Mask Color Decision</h3>
              {colorDecision ? (
                <>
                  <p className="review-color-row">
                    Colors sent to model:
                    {supportedKeyColors.length > 0
                      ? supportedKeyColors.map((color) => <ColorChip key={`supported-${color}`} color={color} />)
                      : <ColorChip color="" />}
                  </p>
                  <p className="review-color-row">
                    Model returned: <ColorChip color={colorDecision.model_returned_key_color} />
                    {!colorDecision.model_returned_supported && colorDecision.model_returned_key_color
                      ? <span className="review-color-note">(not in supported set, fallback applied)</span>
                      : null}
                  </p>
                  <p className="review-color-row">
                    Prompt requested: <ColorChip color={colorDecision.prompt_requested_key_color} />
                  </p>
                  <p className="review-color-row">
                    Backend selected: <ColorChip color={colorDecision.selected_key_color} />
                  </p>
                  <p className="review-color-row">
                    Final mask removal color: <ColorChip color={colorDecision.final_mask_removal_color || selectedLevel.window_key_color} />
                  </p>
                  {typeof colorDecision.attempt === 'number' && (
                    <p className="review-color-detail">Accepted on attempt: {colorDecision.attempt}</p>
                  )}
                  {typeof colorDecision.selected_window_count === 'number' && (
                    <p className="review-color-detail">
                      Selected color window count: {colorDecision.selected_window_count} | conflict: {String(!!colorDecision.selected_has_key_color_conflict)}
                    </p>
                  )}

                  <div className="review-preview-controls">
                    <p className="review-color-row">
                      Preview color from original image:
                      {selectedPreviewColor ? <ColorChip color={selectedPreviewColor} /> : <span className="review-empty">Click the original image</span>}
                    </p>
                    <button
                      type="button"
                      className="review-reprocess-btn"
                      disabled={!selectedPreviewColor || previewProcessing}
                      onClick={() => runPreviewReprocess(selectedPreviewColor)}
                    >
                      {previewProcessing ? 'Reprocessing…' : 'Reprocess'}
                    </button>
                    <button
                      type="button"
                      className="review-reprocess-btn"
                      disabled={
                        !previewHasRun
                        || !selectedPreviewColor
                        || previewProcessing
                        || previewSaving
                        || selectedLevelId === REVIEW_DRAFT_ID
                      }
                      onClick={applyPreviewPermanent}
                    >
                      {previewSaving ? 'Saving…' : 'Apply Preview Permanently'}
                    </button>
                    {previewError && <p className="error">Preview error: {previewError}</p>}
                    {previewSaveNote && (
                      <p className={previewSaveNote.startsWith('Preview applied') ? 'review-preview-note' : 'error'}>
                        {previewSaveNote}
                      </p>
                    )}
                  </div>

                  {displayCandidates.length > 0 && (
                    <div className="review-candidate-table-wrap">
                      <table className="review-candidate-table">
                        <thead>
                          <tr>
                            <th>Candidate</th>
                            <th>Score</th>
                            <th>Windows</th>
                            <th>Total Area</th>
                            <th>Largest Area</th>
                            <th>Conflict</th>
                          </tr>
                        </thead>
                        <tbody>
                          {displayCandidates.map((candidate, index) => {
                            const keyColor = String(candidate?.key_color || '')
                            const isSelected = keyColor && keyColor === colorDecision.selected_key_color
                            return (
                              <tr key={`candidate-${keyColor || index}`} className={isSelected ? 'selected' : ''}>
                                <td>
                                  <ColorChip color={keyColor} />
                                  {candidate?.preview_status && <span className="review-preview-badge">preview</span>}
                                </td>
                                <td>{typeof candidate?.score === 'number' ? candidate.score.toFixed(1) : 'n/a'}</td>
                                <td>{candidate?.window_count ?? 'n/a'}</td>
                                <td>{candidate?.total_area ?? 'n/a'}</td>
                                <td>{candidate?.largest_area ?? 'n/a'}</td>
                                <td>
                                  {typeof candidate?.has_key_color_conflict === 'boolean'
                                    ? String(candidate.has_key_color_conflict)
                                    : 'n/a'}
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              ) : (
                <p className="review-empty">No color decision metadata on this saved level.</p>
              )}
            </section>

            <section className="review-image-grid">
              <ReviewImageCard
                title="1) Original Image"
                imageUrl={originalImageUrl}
                windows={[]}
                boardWidth={boardWidth}
                boardHeight={boardHeight}
                showWindows={false}
                onImageClick={handleOriginalImageClick}
              />
              <ReviewImageCard
                title="2) Identified Boundary Boxes"
                imageUrl={originalImageUrl}
                windows={displayWindows}
                boardWidth={boardWidth}
                boardHeight={boardHeight}
                showWindows={true}
              />
              <ReviewImageCard
                title={`3) Transformed Image${previewHasRun ? ' (Preview Only)' : ''}`}
                imageUrl={transformedImageUrl}
                windows={[]}
                boardWidth={boardWidth}
                boardHeight={boardHeight}
                showWindows={false}
              />
            </section>

            <section className="review-sprites">
              <h3>Sprite Selection</h3>
              {spriteEntries.length === 0 && <p className="review-empty">No sprites available for this level.</p>}
              {spriteEntries.length > 0 && (
                <div className="review-sprite-grid">
                  {spriteEntries.map(({ spriteUrl, monster, index }) => {
                    const name = monster?.name || `Generated Sprite ${index + 1}`
                    const flavor = monster?.flavor || ''

                    return (
                      <article key={`review-slot-${index}`} className="review-sprite-card">
                        <div className="review-sprite-preview">
                          {spriteUrl ? <img src={spriteUrl} alt={name} /> : <span className="review-empty">No sprite</span>}
                        </div>
                        <p className="review-sprite-name">{name}</p>
                        {flavor && <p className="review-sprite-flavor">{flavor}</p>}
                        <p className="review-sprite-window">
                          Sprite index {index + 1}
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
