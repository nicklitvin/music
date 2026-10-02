import type { NoteBoundingBox } from '../lib/types'

export interface ScoreLine {
  pageIndex: number
  // Vertical extent on the page, in the page image's own pixel coordinates.
  top: number
  bottom: number
  // Range of onset indices (inclusive start, exclusive end) that sit on it.
  firstOnset: number
  endOnset: number
}

// How far x may drop between consecutive onsets before it reads as the
// reading position wrapping to the next staff row rather than moving along
// the current one.
const X_RESET_DROP = 50

// Staff rows closer together than this fraction of the typical row spacing
// are the two halves of one grand staff (treble over bass), not two
// separate lines of music.
const SAME_SYSTEM_RATIO = 0.6

/** Groups onsets into the lines a reader sees -- one staff system each.
 *
 * Derived from the notes themselves rather than the page image: within a
 * system the reading position moves left to right, so x increasing means
 * "same row" and x dropping back to the margin means "next row". That
 * yields staff *rows*; on piano music a system is two of them (treble and
 * bass), which are much closer together vertically than consecutive
 * systems are -- so rows are merged when their gap is well under the
 * typical one.
 */
export function buildLines(onsets: NoteBoundingBox[][]): ScoreLine[] {
  if (!onsets.length) return []

  // 1. Split into rows wherever the page changes or x jumps backwards.
  const rows: ScoreLine[] = []
  onsets.forEach((onset, index) => {
    const pageIndex = onset[0].pageIndex
    const x = Math.min(...onset.map((n) => n.x))
    const top = Math.min(...onset.map((n) => n.y))
    const bottom = Math.max(...onset.map((n) => n.y + n.height))
    const current = rows[rows.length - 1]
    const isNewRow =
      current === undefined || pageIndex !== current.pageIndex || x < (current as ScoreLine & { lastX?: number }).lastX! - X_RESET_DROP

    if (isNewRow) {
      rows.push({ pageIndex, top, bottom, firstOnset: index, endOnset: index + 1 })
    } else {
      current.top = Math.min(current.top, top)
      current.bottom = Math.max(current.bottom, bottom)
      current.endOnset = index + 1
    }
    ;(rows[rows.length - 1] as ScoreLine & { lastX?: number }).lastX = x
  })

  // 2. Merge the row pairs that are really one grand staff. The threshold
  //    is taken from the data so it survives different engravings and page
  //    sizes rather than assuming a pixel distance.
  const gaps: number[] = []
  for (let i = 1; i < rows.length; i++) {
    if (rows[i].pageIndex === rows[i - 1].pageIndex) gaps.push(rows[i].top - rows[i - 1].bottom)
  }
  if (!gaps.length) return rows.map(stripInternals)
  const sorted = [...gaps].sort((a, b) => a - b)
  const typicalGap = sorted[Math.floor(sorted.length / 2)]

  const merged: ScoreLine[] = []
  for (const row of rows) {
    const previous = merged[merged.length - 1]
    const sameSystem =
      previous !== undefined &&
      previous.pageIndex === row.pageIndex &&
      row.top - previous.bottom < typicalGap * SAME_SYSTEM_RATIO
    if (sameSystem) {
      previous.bottom = Math.max(previous.bottom, row.bottom)
      previous.top = Math.min(previous.top, row.top)
      previous.endOnset = row.endOnset
    } else {
      merged.push({ ...row })
    }
  }
  return merged.map(stripInternals)
}

function stripInternals(line: ScoreLine): ScoreLine {
  const { pageIndex, top, bottom, firstOnset, endOnset } = line
  return { pageIndex, top, bottom, firstOnset, endOnset }
}

/** Which line an onset index falls on, or null if it is out of range. */
export function lineForOnset(lines: ScoreLine[], onsetIndex: number): ScoreLine | null {
  for (const line of lines) {
    if (onsetIndex >= line.firstOnset && onsetIndex < line.endOnset) return line
  }
  return lines.length ? lines[lines.length - 1] : null
}
