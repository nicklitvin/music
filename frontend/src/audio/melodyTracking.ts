import type { NoteBoundingBox } from '../lib/types'

// A different, simpler position-tracking strategy than scoreFollowing.ts's
// LineTracker: instead of matching a whole line's set/chord of pitches,
// only look at the single highest-pitched note in each detection event
// (usually the melody line, and the easiest single note to pick out of a
// noisy signal) and ask "which note in the score matching this pitch is
// most plausibly next, given where we currently are". LineTracker is left
// as-is (not replaced) -- this is an alternative to try alongside it.

const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

function pitchToMidi(pitch: string): number | null {
  const match = /^([A-G]#?)(-?\d+)$/.exec(pitch)
  if (!match) return null
  const index = NOTE_NAMES.indexOf(match[1])
  if (index === -1) return null
  return (Number(match[2]) + 1) * 12 + index
}

// The highest-pitched note in a detection event's `notes` (a single note or
// a chord) -- null if `notes` is empty or nothing parses as a pitch.
export function highestNote(notes: string[]): string | null {
  let best: { pitch: string; midi: number } | null = null
  for (const pitch of notes) {
    const midi = pitchToMidi(pitch)
    if (midi === null) continue
    if (!best || midi > best.midi) best = { pitch, midi }
  }
  return best?.pitch ?? null
}

export interface OrderedNote {
  index: number
  box: NoteBoundingBox
}

// Flattens the score's notes into one reading-order sequence (page, then
// measure, then top-to-bottom/left-to-right) -- the axis "distance from
// where we currently are" is measured along.
export function buildNoteSequence(boxes: NoteBoundingBox[]): OrderedNote[] {
  const ordered = [...boxes].sort((a, b) => {
    if (a.pageIndex !== b.pageIndex) return a.pageIndex - b.pageIndex
    if (a.measureIndex !== b.measureIndex) return a.measureIndex - b.measureIndex
    if (Math.abs(a.y - b.y) > 1e-6) return a.y - b.y
    return a.x - b.x
  })
  return ordered.map((box, index) => ({ index, box }))
}

interface MelodyTrackerOptions {
  // Distance (in notes) over which a forward candidate's weight decays by
  // ~63% (i.e. an exp(-distance/scale) time constant) -- bigger means
  // candidates further ahead still get meaningful weight.
  forwardDecay?: number
  // Same, but for candidates behind the current position.
  backwardDecay?: number
  // Extra flat multiplier on every backward candidate's weight, on top of
  // its own decay -- sheet music is read forward, so a same-distance
  // backward jump should lose to a forward one.
  backwardPenalty?: number
  // Consecutive detections that must agree on the same candidate before
  // the tracked position actually moves, since any single detection can be
  // a misread.
  requiredStreak?: number
}

// Tracks the most plausible current note purely from the stream of
// detected "highest note" pitches, weighting candidate notes elsewhere in
// the score by their distance from the current position rather than only
// searching a fixed nearby window.
export class MelodyTracker {
  private sequence: OrderedNote[]
  private currentIndex: number
  private pendingIndex: number | null = null
  private pendingStreak = 0
  private readonly forwardDecay: number
  private readonly backwardDecay: number
  private readonly backwardPenalty: number
  private readonly requiredStreak: number

  constructor(boxes: NoteBoundingBox[], options: MelodyTrackerOptions = {}) {
    this.sequence = buildNoteSequence(boxes)
    this.currentIndex = 0
    this.forwardDecay = options.forwardDecay ?? 10
    this.backwardDecay = options.backwardDecay ?? 4
    this.backwardPenalty = options.backwardPenalty ?? 0.3
    this.requiredStreak = options.requiredStreak ?? 2
  }

  getCurrentNote(): NoteBoundingBox | null {
    return this.sequence[this.currentIndex]?.box ?? null
  }

  private weight(distance: number): number {
    if (distance >= 0) return Math.exp(-distance / this.forwardDecay)
    return this.backwardPenalty * Math.exp(distance / this.backwardDecay)
  }

  // Feed one detection event's notes in. Returns the newly-current note
  // only when the tracked position actually changed this call.
  observe(detectedNotes: string[]): NoteBoundingBox | null {
    const target = highestNote(detectedNotes)
    if (target === null) {
      this.pendingIndex = null
      this.pendingStreak = 0
      return null
    }

    let bestIndex: number | null = null
    let bestWeight = 0
    for (const entry of this.sequence) {
      if (entry.box.pitch !== target) continue
      const w = this.weight(entry.index - this.currentIndex)
      if (w > bestWeight) {
        bestWeight = w
        bestIndex = entry.index
      }
    }

    if (bestIndex === null || bestIndex === this.currentIndex) {
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
    return this.sequence[bestIndex].box
  }

  // The reader scrolled the page by hand. That is a statement about where
  // they actually are, and usually a correction -- they scrolled precisely
  // because the highlight was in the wrong place. So it overrides the
  // tracked position outright rather than competing with it: audio
  // evidence that led somewhere the reader just contradicted is not
  // evidence worth preserving.
  //
  // Any part-built streak is dropped too, since it was accumulated toward
  // a position the reader has now rejected.
  hintPosition(box: NoteBoundingBox): void {
    const entry = this.sequence.find((candidate) => candidate.box === box)
    if (!entry) return
    this.currentIndex = entry.index
    this.pendingIndex = null
    this.pendingStreak = 0
  }

  // Nearest note to a point on the page, for turning a scroll offset into
  // a position hint.
  nearestTo(pageIndex: number, y: number): NoteBoundingBox | null {
    let best: { box: NoteBoundingBox; distance: number } | null = null
    for (const { box } of this.sequence) {
      if (box.pageIndex !== pageIndex) continue
      const distance = Math.abs(box.y + box.height / 2 - y)
      if (!best || distance < best.distance) best = { box, distance }
    }
    return best?.box ?? null
  }
}
