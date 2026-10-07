// Where a tap on the sheet lands, and what it means.
//
// Mirrors the arrow keys exactly: left/right move a line, up/down move a
// page. Keeping the mapping in one tested function rather than inline
// arithmetic means the two input methods cannot drift apart.
//
//   +----------+-------------------+----------+
//   |          |   previous page   |          |
//   | previous |-------------------|   next   |
//   |   line   |     next page     |   line   |
//   |          |-------------------|          |
//   |          |  (nav pill below) |          |
//   +----------+-------------------+----------+

export type TapAction = 'previous-line' | 'next-line' | 'previous-page' | 'next-page' | null

/** Fraction of the width taken by each side column. */
const SIDE_COLUMN = 0.28
/** Fraction of the height at the bottom left alone, where the pill sits. */
const BOTTOM_RESERVED = 0.16

export function tapAction(x: number, y: number, width: number, height: number): TapAction {
  if (width <= 0 || height <= 0) return null
  const relativeX = x / width
  const relativeY = y / height

  // The nav pill floats here; a tap near it is aimed at a button, and
  // treating a near-miss as a page turn would be worse than ignoring it.
  if (relativeY > 1 - BOTTOM_RESERVED) return null

  if (relativeX < SIDE_COLUMN) return 'previous-line'
  if (relativeX > 1 - SIDE_COLUMN) return 'next-line'
  return relativeY < 0.5 ? 'previous-page' : 'next-page'
}

/** Was this pointer interaction a tap rather than a scroll or a drag? */
export function isTap(
  movedPixels: number,
  elapsedMs: number,
  { maxMovement = 12, maxDuration = 500 } = {},
): boolean {
  return movedPixels <= maxMovement && elapsedMs <= maxDuration
}
