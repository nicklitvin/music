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
