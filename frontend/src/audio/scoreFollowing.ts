import type { NoteBoundingBox } from '../lib/types'

// Rows of notes ("lines" of sheet music) grouped by vertical position, in
// reading order (page, then top-to-bottom within the page). This is a
// row-clustering heuristic over whatever bounding boxes the score has --
// it works whether those boxes come from a single box per page (today's
// OMR placeholder) or real per-note boxes (once OMR is implemented for
// real), it just won't be useful for line-following until there's more
// than one row of real notes per page to distinguish.
export interface ScoreLine {
  pageIndex: number
  lineIndex: number
  y: number
  boxes: NoteBoundingBox[]
}

// A note is considered part of the same line as the previous one if its y
// is within this fraction of the page height -- tuned for staff lines,
// which are much shorter than a page.
const LINE_Y_TOLERANCE_FRACTION = 0.02

export function groupBoundingBoxesIntoLines(
  boxes: NoteBoundingBox[],
  pageHeights: Record<number, number>,
): ScoreLine[] {
  const byPage = new Map<number, NoteBoundingBox[]>()
  for (const box of boxes) {
    const list = byPage.get(box.pageIndex) ?? []
    list.push(box)
    byPage.set(box.pageIndex, list)
  }

  const lines: ScoreLine[] = []
  const pageIndices = [...byPage.keys()].sort((a, b) => a - b)

  for (const pageIndex of pageIndices) {
    const pageBoxes = [...byPage.get(pageIndex)!].sort((a, b) => a.y - b.y)
    const tolerance = (pageHeights[pageIndex] ?? 1000) * LINE_Y_TOLERANCE_FRACTION

    let current: NoteBoundingBox[] = []
    let lineIndex = 0
    const flush = () => {
      if (current.length === 0) return
      const y = current.reduce((sum, b) => sum + b.y, 0) / current.length
      // Reading order within a line is left-to-right, independent of the
      // small y-jitter used to cluster them.
      const boxesInReadingOrder = [...current].sort((a, b) => a.x - b.x)
      lines.push({ pageIndex, lineIndex: lineIndex++, y, boxes: boxesInReadingOrder })
      current = []
    }

    for (const box of pageBoxes) {
      const last = current[current.length - 1]
      if (last && box.y - last.y > tolerance) {
        flush()
      }
      current.push(box)
    }
    flush()
  }

  return lines
}

// A system of piano notation: a grand staff, i.e. a treble line and a bass
// line played together. `groupBoundingBoxesIntoLines` splits those into two
// rows because they are vertically apart; highlighting one of them marks
// only one clef, and because consecutive onsets alternate between the two
// staves, a per-line highlight also flickers between them. So the viewer
// works in systems, not lines.
export interface ScoreSystem {
  pageIndex: number
  systemIndex: number
  lineIndexes: number[]
  // Full pixel extent of the system's noteheads, plus a little padding.
  top: number
  bottom: number
}

// Groups lines into systems. Within a page: a gap between consecutive rows
// noticeably larger than the typical gap is a real break between systems;
// inside each run between breaks, rows pair up two-to-a-system (grand
// staff), with any odd row left as its own system. Robust to the common
// OMR mess where the two staves of a system are evenly spaced with
// everything else, which pure gap clustering cannot split.
export function groupLinesIntoSystems(
  lines: ScoreLine[],
  pageHeights: Record<number, number>,
): ScoreSystem[] {
  const byPage = new Map<number, ScoreLine[]>()
  for (const line of lines) {
    const list = byPage.get(line.pageIndex) ?? []
    list.push(line)
    byPage.set(line.pageIndex, list)
  }

  const systems: ScoreSystem[] = []
  for (const [pageIndex, pageLines] of [...byPage.entries()].sort((a, b) => a[0] - b[0])) {
    const pad = (pageHeights[pageIndex] ?? 1000) * 0.012
    const rows = [...pageLines]
      .map((line) => {
        const top = Math.min(...line.boxes.map((b) => b.y))
        const bottom = Math.max(...line.boxes.map((b) => b.y + b.height))
        return { line, top, bottom, mid: (top + bottom) / 2 }
      })
      .sort((a, b) => a.top - b.top)

    const gaps = rows.slice(1).map((row, i) => row.mid - rows[i].mid)
    const sorted = [...gaps].sort((a, b) => a - b)
    const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0
    const breakThreshold = median * 1.4

    // Split rows into runs at the large gaps.
    const runs: (typeof rows)[] = [[]]
    rows.forEach((row, i) => {
      if (i > 0 && gaps[i - 1] > breakThreshold) runs.push([])
      runs[runs.length - 1].push(row)
    })

    let systemIndex = 0
    for (const run of runs) {
      for (let i = 0; i < run.length; i += 2) {
        const members = run.slice(i, i + 2)
        systems.push({
          pageIndex,
          systemIndex: systemIndex++,
          lineIndexes: members.map((m) => m.line.lineIndex),
          top: Math.min(...members.map((m) => m.top)) - pad,
          bottom: Math.max(...members.map((m) => m.bottom)) + pad,
        })
      }
    }
  }
  return systems
}
