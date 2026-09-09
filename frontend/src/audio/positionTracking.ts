import type { NoteBoundingBox } from '../lib/types'

// Notes within this many pixels of each other (same page + measure) are
// one simultaneous onset. Must match backend score_timeline.ONSET_X_TOLERANCE
// so the onset indices the backend tracker reports line up with these.
const ONSET_X_TOLERANCE = 8

// Groups the score's notes into simultaneous onsets in reading order, the
// exact same way the backend does (app/services/score_timeline.py). The
// backend's position tracker reports an index into this sequence; the
// viewer maps that back to a line to highlight.
export function buildOnsets(boxes: NoteBoundingBox[]): NoteBoundingBox[][] {
  const ordered = [...boxes].sort(
    (a, b) =>
      a.pageIndex - b.pageIndex ||
      a.measureIndex - b.measureIndex ||
      a.x - b.x ||
      a.y - b.y,
  )

  const onsets: NoteBoundingBox[][] = []
  for (const box of ordered) {
    const previous = onsets[onsets.length - 1]
    const sameOnset =
      previous !== undefined &&
      previous[0].pageIndex === box.pageIndex &&
      previous[0].measureIndex === box.measureIndex &&
      Math.abs(box.x - previous[0].x) <= ONSET_X_TOLERANCE
    if (sameOnset) previous.push(box)
    else onsets.push([box])
  }
  return onsets
}
