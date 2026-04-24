import React from 'react'
import { motion, AnimatePresence } from 'framer-motion'

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
 *   renderSprite {boolean} – Whether to animate and render a sprite in the window.
 */
export default function WindowNode({
  window: win,
  windowId,
  activeSpriteUrl = '',
  showMissMarker = false,
  onWhack,
  debugBounds = false,
  renderSprite = true,
}) {
  const visible = renderSprite && Boolean(activeSpriteUrl)

  function handleClick() {
    if (!visible) return
    if (visible && activeSpriteUrl) {
      onWhack(windowId, activeSpriteUrl)
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
        cursor: renderSprite && visible ? 'pointer' : 'default',
      }}
      onClick={handleClick}
    >
      <AnimatePresence>
        {visible && (
          <motion.img
            key={activeSpriteUrl}
            src={activeSpriteUrl}
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

      <AnimatePresence>
        {showMissMarker && !visible && (
          <motion.div
            key="miss-marker"
            className="miss-marker"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 1.15 }}
            transition={{ duration: 0.2 }}
          >
            X
          </motion.div>
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
