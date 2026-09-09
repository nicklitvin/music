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

export interface LineBand {
  top: number
  height: number
}

// The vertical strip to highlight for each line. A row of OMR noteheads is
// often only one staff tall, but a piano system is a grand staff (treble +
// bass), so a strip sized to the boxes alone marks just one clef. Instead
// each band is sized to the local row-to-row spacing (clamped), which
// covers the whole system the line sits in without bleeding far into its
// neighbours. Keyed by `${pageIndex}-${lineIndex}`.
export function computeLineBands(
  lines: ScoreLine[],
  pageHeights: Record<number, number>,
): Map<string, LineBand> {
  const byPage = new Map<number, ScoreLine[]>()
  for (const line of lines) {
    const list = byPage.get(line.pageIndex) ?? []
    list.push(line)
    byPage.set(line.pageIndex, list)
  }

  const bands = new Map<string, LineBand>()
  for (const [pageIndex, pageLines] of byPage) {
    const pageHeight = pageHeights[pageIndex] ?? 1000
    const rows = [...pageLines]
      .map((line) => {
        const top = Math.min(...line.boxes.map((b) => b.y))
        const bottom = Math.max(...line.boxes.map((b) => b.y + b.height))
        return { line, top, bottom, mid: (top + bottom) / 2 }
      })
      .sort((a, b) => a.mid - b.mid)

    rows.forEach((row, i) => {
      const gaps: number[] = []
      if (rows[i - 1]) gaps.push(row.mid - rows[i - 1].mid)
      if (rows[i + 1]) gaps.push(rows[i + 1].mid - row.mid)
      const rowGap = gaps.length ? Math.min(...gaps) : pageHeight * 0.06

      const height = Math.min(
        Math.max(rowGap * 1.15, pageHeight * 0.05, row.bottom - row.top + 24),
        pageHeight * 0.11,
      )
      bands.set(`${pageIndex}-${row.line.lineIndex}`, { top: row.mid - height / 2, height })
    })
  }
  return bands
}
