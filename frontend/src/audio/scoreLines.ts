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
// are candidates for being the two halves of one grand staff.
const SAME_SYSTEM_RATIO = 0.6

// A row covering a single clef spans about this much or less. A grand staff
// spans both and runs to 26-43 semitones on real sheets.
//
// This is the test that decides whether merging is wanted at all, and it
// matters because the x-reset split does *not* reliably produce half
// systems: OMR numbers measures per system, so treble and bass notes of one
// system usually interleave in reading order and come out as a single row
// already. Merging those pairs the bass of one system with the treble of
// the next -- a highlight straddling two lines, which is what this
// previously did on aliez. Requiring both rows to cover one clef each means
// merging only happens where there is genuinely a split to repair.
const SINGLE_CLEF_SEMITONES = 20

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
const PITCH_PATTERN = /^([A-G]#?)(-?\d+)$/

function pitchToMidi(pitch: string): number | null {
  const match = PITCH_PATTERN.exec(pitch)
  if (!match) return null
  return (parseInt(match[2], 10) + 1) * 12 + NOTE_NAMES.indexOf(match[1])
}

/** Spread of pitches in a row, ignoring outliers at either end. */
function pitchSpan(pitches: number[]): number {
  if (pitches.length < 4) return 0
  const sorted = [...pitches].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length * 0.9)] - sorted[Math.floor(sorted.length * 0.1)]
}

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
  type Row = ScoreLine & { lastX: number; pitches: number[] }
  const rows: Row[] = []
  onsets.forEach((onset, index) => {
    const pageIndex = onset[0].pageIndex
    const x = Math.min(...onset.map((n) => n.x))
    const top = Math.min(...onset.map((n) => n.y))
    const bottom = Math.max(...onset.map((n) => n.y + n.height))
    const current = rows[rows.length - 1]
    const isNewRow = current === undefined || pageIndex !== current.pageIndex || x < current.lastX - X_RESET_DROP

    if (isNewRow) {
      rows.push({ pageIndex, top, bottom, firstOnset: index, endOnset: index + 1, lastX: x, pitches: [] })
    } else {
      current.top = Math.min(current.top, top)
      current.bottom = Math.max(current.bottom, bottom)
      current.endOnset = index + 1
    }
    const row = rows[rows.length - 1]
    row.lastX = x
    for (const note of onset) {
      const midi = pitchToMidi(note.pitch)
      if (midi !== null) row.pitches.push(midi)
    }
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

  // A piano system is a treble row over a bass row -- two, never more.
  const MAX_ROWS_PER_SYSTEM = 2
  const merged: Row[] = []
  const rowsIn: number[] = []
  for (const row of rows) {
    const previous = merged[merged.length - 1]
    // Both rows must cover a single clef each for this to be a grand staff
    // that needs rejoining. When a row already spans both clefs it is
    // a whole system, and merging it with its neighbour would produce a
    // band running from one system's bass to the next one's treble.
    const bothSingleClef =
      previous !== undefined &&
      pitchSpan(previous.pitches) <= SINGLE_CLEF_SEMITONES &&
      pitchSpan(row.pitches) <= SINGLE_CLEF_SEMITONES
    const sameSystem =
      previous !== undefined &&
      previous.pageIndex === row.pageIndex &&
      rowsIn[rowsIn.length - 1] < MAX_ROWS_PER_SYSTEM &&
      bothSingleClef &&
      row.top - previous.bottom < typicalGap * SAME_SYSTEM_RATIO

    if (sameSystem) {
      rowsIn[rowsIn.length - 1] += 1
      previous.bottom = Math.max(previous.bottom, row.bottom)
      previous.top = Math.min(previous.top, row.top)
      previous.endOnset = row.endOnset
      previous.pitches = previous.pitches.concat(row.pitches)
    } else {
      rowsIn.push(1)
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
