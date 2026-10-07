// When, and how far, the sheet should scroll to follow the music.
//
// Pulled out of the component so it can be driven through a whole
// play-through in a test instead of only being judged by eye. The previous
// version lived inline and used a "don't move until the target is more than
// N% of the viewport away" rule, which was badly wrong: a staff line is
// about 7% of a phone viewport, so a 55% threshold meant drifting nearly
// eight lines before the sheet moved at all. Worse, the model's search
// window is derived from what is on screen, so a sheet that would not
// scroll also froze the window, and the two deadlocked.
//
// The unit that matters is the line, not a pixel distance: the sheet should
// move when the music reaches a new line, and otherwise hold still.

export interface LineBand {
  /** Absolute offsets within the scroll container, in px. */
  top: number
  bottom: number
}

export interface FollowOptions {
  viewportHeight: number
  /** Where a followed line is placed, as a fraction of the viewport. */
  anchor?: number
  /** Don't bother scrolling for less than this fraction of the viewport. */
  tolerance?: number
}

export const DEFAULT_ANCHOR = 0.42
export const DEFAULT_TOLERANCE = 0.04

/** Scroll offset that centres `line`, or null if it is already near enough.
 *
 * Centring (rather than only scrolling the line into view) is what keeps
 * the line before and the line after visible either side of it.
 */
export function followDecision(
  scrollTop: number,
  line: LineBand,
  { viewportHeight, anchor = DEFAULT_ANCHOR, tolerance = DEFAULT_TOLERANCE }: FollowOptions,
): number | null {
  if (viewportHeight <= 0) return null
  const centre = (line.top + line.bottom) / 2
  const desired = Math.max(0, centre - viewportHeight * anchor)
  if (Math.abs(desired - scrollTop) < viewportHeight * tolerance) return null
  return desired
}

/** Is any part of `line` on screen? */
export function isVisible(scrollTop: number, line: LineBand, viewportHeight: number): boolean {
  return line.bottom >= scrollTop && line.top <= scrollTop + viewportHeight
}

/** Indices of the lines currently on screen, in order. */
export function visibleLines(scrollTop: number, viewportHeight: number, bands: LineBand[]): number[] {
  const out: number[] = []
  bands.forEach((band, index) => {
    if (isVisible(scrollTop, band, viewportHeight)) out.push(index)
  })
  return out
}

/** The line nearest the middle of the screen -- the one the reader is most
 * likely actually looking at after scrolling by hand.
 *
 * This is the correction a manual scroll carries: not merely "limit
 * yourself to these lines", but "I am *here*". Without it the model keeps
 * its old belief and has to re-find itself inside the new window, which is
 * what made scrolling feel like it did not take.
 */
export function centreLine(scrollTop: number, viewportHeight: number, bands: LineBand[]): number | null {
  const middle = scrollTop + viewportHeight / 2
  let best: number | null = null
  let bestDistance = Infinity
  bands.forEach((band, index) => {
    const distance = Math.abs((band.top + band.bottom) / 2 - middle)
    if (distance < bestDistance) {
      bestDistance = distance
      best = index
    }
  })
  return best
}

/** Tracks which line to follow, ignoring one-off flickers.
 *
 * The model occasionally reports a neighbouring line for a frame or two.
 * Acting on that would bounce the sheet, so a new line has to be reported
 * `confirmations` times in a row before it is followed. This is the
 * anti-jitter mechanism -- not a distance threshold, which is what broke
 * following entirely.
 */
export class LineFollower {
  private current: number | null = null
  private pending: number | null = null
  private pendingCount = 0
  private readonly confirmations: number

  constructor(confirmations = 2) {
    this.confirmations = confirmations
  }

  /** Feed the latest reported line; returns the line to follow if it just
   * changed, else null. */
  observe(lineIndex: number): number | null {
    if (lineIndex === this.current) {
      this.pending = null
      this.pendingCount = 0
      return null
    }
    if (lineIndex === this.pending) {
      this.pendingCount += 1
    } else {
      this.pending = lineIndex
      this.pendingCount = 1
    }
    // The first line seen is adopted at once -- there is nothing on screen
    // worth protecting yet, and waiting just delays the first follow.
    if (this.current === null || this.pendingCount >= this.confirmations) {
      this.current = lineIndex
      this.pending = null
      this.pendingCount = 0
      return lineIndex
    }
    return null
  }

  /** The reader moved the sheet themselves: adopt where they put it so the
   * next genuine line change is still detected as a change. */
  reset(lineIndex: number | null = null): void {
    this.current = lineIndex
    this.pending = null
    this.pendingCount = 0
  }

  get currentLine(): number | null {
    return this.current
  }
}
