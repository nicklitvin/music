// Clicking (or tapping) a line of the sheet says "I am here".
//
// The click is the reader's word on where they are, so it has to be told
// apart from a scroll or a drag -- a finger that touches down to scroll
// must not also move their place -- and has to land on a line even when it
// falls in the gap between two.

import type { LineBand } from './followPolicy'

/** Was this pointer interaction a tap rather than a scroll or a drag? */
export function isTap(
  movedPixels: number,
  elapsedMs: number,
  { maxMovement = 12, maxDuration = 500 } = {},
): boolean {
  return movedPixels <= maxMovement && elapsedMs <= maxDuration
}

/** The line under `y` (an offset in the scroll column), or the nearest one
 * when it falls between lines or in a margin; null with no lines at all. */
export function lineAt(y: number, bands: LineBand[]): number | null {
  let best: number | null = null
  let bestDistance = Infinity
  bands.forEach((band, index) => {
    if (band.bottom <= band.top) return // not laid out
    const distance = y < band.top ? band.top - y : y > band.bottom ? y - band.bottom : 0
    if (distance < bestDistance) {
      bestDistance = distance
      best = index
    }
  })
  return best
}
