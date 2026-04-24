import React, { useState } from 'react'
import GameBoard from './components/GameBoard.jsx'

const FALLBACK_BACKGROUND = 'https://placehold.co/1280x720/1a1a2e/ffffff?text=Monster+Game'
const DEFAULT_BOARD_WIDTH = 1280
const DEFAULT_BOARD_HEIGHT = 720

function getSafeDimension(value, fallback) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

export default function App() {
  const [levelData, setLevelData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [theme, setTheme] = useState('haunted house')
  const [showDebugBounds, setShowDebugBounds] = useState(import.meta.env.VITE_DEBUG_BOUNDS === 'true')

  async function handleGenerateLevel() {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/generate-level', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme, generate_images: true }),
      })
      if (!res.ok) throw new Error(`Server error: ${res.status}`)
      const data = await res.json()
      setLevelData(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>🧟 Monster Game</h1>
        <div className="controls">
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
          <label className="debug-toggle">
            <input
              type="checkbox"
              checked={showDebugBounds}
              onChange={(e) => setShowDebugBounds(e.target.checked)}
            />
            Show debug bounds
          </label>
        </div>
        {error && <p className="error">Error: {error}</p>}
      </header>

      {levelData ? (
        <GameBoard
          backgroundUrl={levelData.background_url || FALLBACK_BACKGROUND}
          windows={levelData.windows}
          spriteUrls={levelData.sprite_urls}
          boardWidth={getSafeDimension(levelData.board_width, DEFAULT_BOARD_WIDTH)}
          boardHeight={getSafeDimension(levelData.board_height, DEFAULT_BOARD_HEIGHT)}
          debugBounds={showDebugBounds}
        />
      ) : (
        <div className="placeholder">
          <p>Enter a theme and click <strong>Generate Level</strong> to start.</p>
        </div>
      )}
    </div>
  )
}
