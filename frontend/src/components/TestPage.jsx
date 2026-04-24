import React, { useState } from 'react'
import GameBoard from './GameBoard.jsx'

const TEST_SPRITE_URL =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 160'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='0' y2='1'%3E%3Cstop offset='0' stop-color='%23ffd66b'/%3E%3Cstop offset='1' stop-color='%23ff8a4c'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cellipse cx='60' cy='146' rx='38' ry='10' fill='%23000000' fill-opacity='0.25'/%3E%3Cpath d='M60 12c25 0 45 21 45 46 0 15-7 29-19 38l-2 48H36l-2-48C22 87 15 73 15 58c0-25 20-46 45-46z' fill='url(%23g)'/%3E%3Ccircle cx='45' cy='60' r='8' fill='%231a1a1a'/%3E%3Ccircle cx='75' cy='60' r='8' fill='%231a1a1a'/%3E%3Cpath d='M42 85c7 8 29 8 36 0' stroke='%231a1a1a' stroke-width='6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E"

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('Failed to read image file'))
    reader.readAsDataURL(file)
  })
}

function getImageDimensions(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => {
      resolve({ width: image.naturalWidth, height: image.naturalHeight })
    }
    image.onerror = () => reject(new Error('Failed to load uploaded image'))
    image.src = dataUrl
  })
}

export default function TestPage({ debugBounds }) {
  const [backgroundUrl, setBackgroundUrl] = useState('')
  const [displayBackgroundUrl, setDisplayBackgroundUrl] = useState('')
  const [overlayUrl, setOverlayUrl] = useState('')
  const [maskUrl, setMaskUrl] = useState('')
  const [boardWidth, setBoardWidth] = useState(1280)
  const [boardHeight, setBoardHeight] = useState(720)
  const [windows, setWindows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [imageName, setImageName] = useState('window-snap-test.png')

  const hasOutlineData = windows.length > 0
  const testSpriteUrls = windows.map(() => TEST_SPRITE_URL)

  async function handleImageUpload(event) {
    const file = event.target.files?.[0]
    if (!file) return

    setLoading(true)
    setError(null)

    try {
      const dataUrl = await readFileAsDataUrl(file)
      const dimensions = await getImageDimensions(dataUrl)
      setBackgroundUrl(dataUrl)
      setDisplayBackgroundUrl(dataUrl)
      setOverlayUrl('')
      setMaskUrl('')
      setBoardWidth(dimensions.width || 1280)
      setBoardHeight(dimensions.height || 720)
      setWindows([])
      setImageName(file.name || 'window-snap-test.png')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
      event.target.value = ''
    }
  }

  async function handleOutlineWindows() {
    if (!backgroundUrl) return

    setLoading(true)
    setError(null)

    try {
      const response = await fetch('/serve-assets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_url: backgroundUrl, character_descriptions: [] }),
      })

      if (!response.ok) {
        throw new Error(`Window outlining failed: ${response.status}`)
      }

      const data = await response.json()
      setWindows(Array.isArray(data.windows) ? data.windows : [])
      setDisplayBackgroundUrl(data.processed_background_url || backgroundUrl)
      setOverlayUrl(data.overlay_url || '')
      setMaskUrl(data.mask_url || '')
      if (Number.isFinite(Number(data.board_width)) && Number(data.board_width) > 0) {
        setBoardWidth(Number(data.board_width))
      }
      if (Number.isFinite(Number(data.board_height)) && Number(data.board_height) > 0) {
        setBoardHeight(Number(data.board_height))
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="test-page">
      <section className="test-panel">
        <div className="test-controls">
          <label className="upload-btn">
            <input type="file" accept="image/*" onChange={handleImageUpload} />
            Upload Image
          </label>
          <button
            type="button"
            onClick={handleOutlineWindows}
            disabled={!backgroundUrl || loading}
            className="generate-btn"
          >
            {loading ? 'Processing…' : 'Outline Windows'}
          </button>
          <button
            type="button"
            onClick={() => {
              setWindows([])
              setDisplayBackgroundUrl(backgroundUrl)
              setOverlayUrl('')
              setMaskUrl('')
            }}
            disabled={windows.length === 0 || loading}
            className="generate-btn secondary-btn"
          >
            Clear Outlines
          </button>
        </div>
        <p className="test-copy">
          Upload any background with pure green window areas, then run deterministic chroma-key outlining on that exact image.
        </p>
        <p className="test-copy">Current outlines: {windows.length}</p>
        {error && <p className="error">Error: {error}</p>}
      </section>

      {backgroundUrl && (
        <section className="test-panel test-steps">
          <div className="step-card">
            <h3>Step 1: Uploaded Image</h3>
            <img
              src={backgroundUrl}
              alt="Uploaded input"
              className="step-image"
              style={{ aspectRatio: `${boardWidth} / ${boardHeight}` }}
            />
          </div>
          <div className="step-card">
            <h3>Step 2: Green Mask</h3>
            {maskUrl ? (
              <div
                className="step-preview"
                style={{ aspectRatio: `${boardWidth} / ${boardHeight}` }}
              >
                <img src={maskUrl} alt="Detected green mask" className="step-image" />
                <div className="step-boxes-layer">
                  {windows.map((win, idx) => (
                    <div
                      key={`step-mask-${win.id ?? idx}`}
                      className="step-box"
                      style={{
                        left: `${(win.x / boardWidth) * 100}%`,
                        top: `${(win.y / boardHeight) * 100}%`,
                        width: `${(win.width / boardWidth) * 100}%`,
                        height: `${(win.height / boardHeight) * 100}%`,
                      }}
                    />
                  ))}
                </div>
              </div>
            ) : (
              <p>Run Outline Windows.</p>
            )}
          </div>
          <div className="step-card">
            <h3>Step 3: Processed Background</h3>
            <img
              src={displayBackgroundUrl || backgroundUrl}
              alt="Processed background"
              className="step-image"
              style={{ aspectRatio: `${boardWidth} / ${boardHeight}` }}
            />
          </div>
          <div className="step-card">
            <h3>Step 4: Building Overlay</h3>
            {overlayUrl ? (
              <div
                className="step-preview"
                style={{ aspectRatio: `${boardWidth} / ${boardHeight}` }}
              >
                <img
                  src={displayBackgroundUrl || backgroundUrl}
                  alt="Processed base"
                  className="step-image"
                />
                <div className="step-sprite-layer">
                  {windows.map((win, idx) => (
                    <img
                      key={`step-sprite-${win.id ?? idx}`}
                      src={TEST_SPRITE_URL}
                      alt="Test sprite preview"
                      className="step-sprite"
                      style={{
                        left: `${(win.x / boardWidth) * 100}%`,
                        top: `${(win.y / boardHeight) * 100}%`,
                        width: `${(win.width / boardWidth) * 100}%`,
                        height: `${(win.height / boardHeight) * 100}%`,
                      }}
                    />
                  ))}
                </div>
                <img src={overlayUrl} alt="Transparent overlay" className="step-image overlay-step-image" />
              </div>
            ) : (
              <p>Run Outline Windows.</p>
            )}
          </div>
        </section>
      )}

      {backgroundUrl ? (
        <GameBoard
          backgroundUrl={displayBackgroundUrl || backgroundUrl}
          overlayUrl={overlayUrl}
          windows={windows}
          spriteUrls={testSpriteUrls}
          boardWidth={boardWidth}
          boardHeight={boardHeight}
          debugBounds
          renderSprites={hasOutlineData}
          showScore={false}
          showDownloadButton={debugBounds}
          downloadFilename={imageName}
        />
      ) : (
        <div className="placeholder">
          <p>Upload an image to inspect how the current window snap process behaves.</p>
        </div>
      )}
    </div>
  )
}