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

function distinctPitches(line: ScoreLine): string[] {
  return [...new Set(line.boxes.map((box) => box.pitch))]
}

// The pitches for a line that best identify it on their own: pitches that
// don't also appear in nearby lines. Falls back to the line's full pitch
// set if every pitch it has is shared with a neighbor (e.g. a repeated
// note/scale run) -- still usable together with the candidate window in
// LineTracker, just less distinctive in isolation.
export function computeLineFingerprint(lines: ScoreLine[], index: number, neighborRadius = 2): string[] {
  const pitches = distinctPitches(lines[index])
  const neighborPitches = new Set<string>()
  for (let offset = -neighborRadius; offset <= neighborRadius; offset++) {
    if (offset === 0) continue
    const neighbor = lines[index + offset]
    if (neighbor) distinctPitches(neighbor).forEach((p) => neighborPitches.add(p))
  }
  const distinctive = pitches.filter((p) => !neighborPitches.has(p))
  return distinctive.length > 0 ? distinctive : pitches
}

interface TrackerOptions {
  // How many lines ahead of the current one to consider as the performer
  // catching up / a missed line, and how many behind to allow as a
  // same-line correction. Keeps matching a cheap local search instead of
  // scanning the whole piece on every detection.
  lookaheadLines?: number
  lookbehindLines?: number
  // Consecutive matching detections required before committing to a line
  // change, since any single detection event can be noisy.
  requiredStreak?: number
}

// Tracks which line of the score the performer is most likely playing,
// from a stream of detected note names (single notes and/or chords) --
// this is deliberately coarse: it identifies "which line", not exact
// note-by-note position within it.
export class LineTracker {
  private lines: ScoreLine[]
  private fingerprints: string[][]
  private currentIndex: number
  private pendingIndex: number | null = null
  private pendingStreak = 0
  private readonly lookaheadLines: number
  private readonly lookbehindLines: number
  private readonly requiredStreak: number

  constructor(lines: ScoreLine[], options: TrackerOptions = {}) {
    this.lines = lines
    this.fingerprints = lines.map((_, i) => computeLineFingerprint(lines, i))
    this.currentIndex = lines.length > 0 ? 0 : -1
    this.lookaheadLines = options.lookaheadLines ?? 4
    this.lookbehindLines = options.lookbehindLines ?? 1
    this.requiredStreak = options.requiredStreak ?? 2
  }

  getCurrentLine(): ScoreLine | null {
    return this.currentIndex >= 0 ? this.lines[this.currentIndex] : null
  }

  // Feed a detection event's notes in. Returns the newly active line only
  // when the active line actually changed this call.
  observe(detectedNotes: string[]): ScoreLine | null {
    if (this.currentIndex < 0 || detectedNotes.length === 0) {
      this.pendingIndex = null
      this.pendingStreak = 0
      return null
    }

    const from = Math.max(0, this.currentIndex - this.lookbehindLines)
    const to = Math.min(this.lines.length - 1, this.currentIndex + this.lookaheadLines)

    let bestIndex: number | null = null
    let bestScore = 0
    for (let i = from; i <= to; i++) {
      const fingerprint = this.fingerprints[i]
      const matched = fingerprint.filter((pitch) => detectedNotes.includes(pitch)).length
      if (matched === 0) continue
      const score = matched / fingerprint.length
      // Prefer the furthest-along matching line on ties, so a sustained
      // chord that also satisfies an earlier line's fingerprint still lets
      // playback advance.
      if (score > bestScore || (score === bestScore && bestIndex !== null && i > bestIndex)) {
        bestScore = score
        bestIndex = i
      }
    }

    if (bestIndex === null) {
      this.pendingIndex = null
      this.pendingStreak = 0
      return null
    }

    if (bestIndex === this.currentIndex) {
      this.pendingIndex = null
      this.pendingStreak = 0
      return null
    }

    if (this.pendingIndex === bestIndex) {
      this.pendingStreak += 1
    } else {
      this.pendingIndex = bestIndex
      this.pendingStreak = 1
    }

    if (this.pendingStreak < this.requiredStreak) {
      return null
    }

    this.currentIndex = bestIndex
    this.pendingIndex = null
    this.pendingStreak = 0
    return this.lines[bestIndex]
  }

  // The reader scrolled the page by hand, which says where they actually
  // are -- and usually says the tracker was wrong, since that is why they
  // scrolled. It therefore overrides the tracked line, and drops any
  // part-built streak, which was accumulated toward a line the reader has
  // just contradicted.
  hintPosition(line: ScoreLine): void {
    const index = this.lines.indexOf(line)
    if (index < 0) return
    this.currentIndex = index
    this.pendingIndex = null
    this.pendingStreak = 0
  }
}
