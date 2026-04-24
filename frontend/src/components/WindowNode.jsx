import React, { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

const VISIBLE_DURATION_MS = 2000  // how long the monster stays up
const POPUP_INTERVAL_MS  = 3500   // how often a monster tries to pop up

/**
 * WindowNode renders a single "window" on the game board and animates a
 * monster sprite sliding up from the bottom of the bounding box.
 *
 * The sprite is clipped to the bounding box so it appears to emerge from the
 * window opening rather than floating on top of it.
 *
 * Props:
 *   window    {{ id, x, y, width, height }} – Window position/size.
 *   spriteUrl {string}  – Image URL for the monster sprite.
 *   isWhacked {boolean} – Whether this window has been scored already.
 *   onWhack   {()=>void} – Callback invoked when the player clicks the monster.
 *   debugBounds {boolean} – Whether to show calibration outlines.
 */
export default function WindowNode({ window: win, spriteUrl, isWhacked, onWhack, debugBounds = false }) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (isWhacked) return

    // Pop up at a random offset so windows don't all fire simultaneously
    const jitter = Math.random() * POPUP_INTERVAL_MS
    const initialTimeout = setTimeout(startCycle, jitter)

    function startCycle() {
      setVisible(true)
      const hideTimeout = setTimeout(() => {
        setVisible(false)
      }, VISIBLE_DURATION_MS)

      return () => clearTimeout(hideTimeout)
    }

    return () => clearTimeout(initialTimeout)
  }, [isWhacked])

  // Restart cycling after monster hides (if not yet whacked)
  useEffect(() => {
    if (isWhacked || visible) return

    const intervalId = setInterval(() => {
      setVisible(true)
      setTimeout(() => setVisible(false), VISIBLE_DURATION_MS)
    }, POPUP_INTERVAL_MS)

    return () => clearInterval(intervalId)
  }, [isWhacked, visible])

  function handleClick() {
    if (visible && !isWhacked) {
      setVisible(false)
      onWhack()
    }
  }

  return (
    <div
      className={`window-node${debugBounds ? ' debug' : ''}`}
      style={{
        position: 'absolute',
        left: win.x,
        top: win.y,
        width: win.width,
        height: win.height,
        overflow: 'hidden',   // Clip sprite to bounding box (mask effect)
        cursor: visible && !isWhacked ? 'pointer' : 'default',
      }}
      onClick={handleClick}
    >
      <AnimatePresence>
        {visible && !isWhacked && (
          <motion.img
            key="monster"
            src={spriteUrl || 'https://placehold.co/100x150/ff6b6b/ffffff?text=👾'}
            alt="Monster"
            className="monster-sprite"
            style={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              width: '100%',
              height: '100%',
              objectFit: 'contain',
              objectPosition: 'bottom',
            }}
            // Slide up from below the bounding box, then slide back down on exit
            initial={{ y: '100%' }}
            animate={{ y: '0%' }}
            exit={{ y: '100%' }}
            transition={{ type: 'spring', stiffness: 260, damping: 20 }}
          />
        )}
      </AnimatePresence>

      {/* Subtle window frame overlay */}
      <div
        className="window-frame"
        style={{
          position: 'absolute',
          inset: 0,
          borderRadius: 4,
          pointerEvents: 'none',
        }}
      />
    </div>
  )
}
