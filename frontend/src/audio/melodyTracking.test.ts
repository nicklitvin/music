import { describe, expect, it } from 'vitest'
import { buildNoteSequence, highestNote, MelodyTracker } from './melodyTracking'
import type { NoteBoundingBox } from '../lib/types'

function box(overrides: Partial<NoteBoundingBox>): NoteBoundingBox {
  return {
    x: 0,
    y: 0,
    width: 10,
    height: 10,
    note: 'quarter',
    pitch: 'C4',
    measureIndex: 0,
    pageIndex: 0,
    ...overrides,
  }
}

describe('highestNote', () => {
  it('picks the highest pitch out of a chord', () => {
    expect(highestNote(['C4', 'G4', 'E4'])).toBe('G4')
  })

  it('compares across octaves correctly, not alphabetically', () => {
    expect(highestNote(['B3', 'C4'])).toBe('C4')
  })

  it('handles sharps', () => {
    expect(highestNote(['C4', 'C#4'])).toBe('C#4')
  })

  it('returns null for no notes', () => {
    expect(highestNote([])).toBeNull()
  })
})

describe('buildNoteSequence', () => {
  it('orders by page, then measure, then position within a measure', () => {
    const boxes = [
      box({ pitch: 'B', pageIndex: 1, measureIndex: 0, y: 0, x: 0 }),
      box({ pitch: 'A', pageIndex: 0, measureIndex: 1, y: 0, x: 0 }),
      box({ pitch: 'C', pageIndex: 0, measureIndex: 0, y: 0, x: 10 }),
      box({ pitch: 'D', pageIndex: 0, measureIndex: 0, y: 0, x: 0 }),
    ]

    const sequence = buildNoteSequence(boxes)

    expect(sequence.map((entry) => entry.box.pitch)).toEqual(['D', 'C', 'A', 'B'])
    expect(sequence.map((entry) => entry.index)).toEqual([0, 1, 2, 3])
  })
})

function makeSequenceBoxes(pitches: string[]): NoteBoundingBox[] {
  return pitches.map((pitch, i) => box({ pitch, measureIndex: i, x: i * 10 }))
}

describe('MelodyTracker', () => {
  it('advances to the nearest forward match once confirmed', () => {
    const boxes = makeSequenceBoxes(['C4', 'D4', 'E4', 'E4', 'F4'])
    const tracker = new MelodyTracker(boxes, { requiredStreak: 2 })

    expect(tracker.observe(['E4'])).toBeNull() // pending
    const changed = tracker.observe(['E4'])

    // Nearest E4 ahead of index 0 is index 2, not the later index-3 one.
    expect(changed?.pitch).toBe('E4')
    expect(tracker.getCurrentNote()).toBe(boxes[2])
  })

  it('prefers a closer forward candidate over a farther one', () => {
    const boxes = makeSequenceBoxes(['C4', 'G4', 'D4', 'G4', 'E4', 'G4'])
    // Three G4 candidates at index 1, 3, 5 -- starting at 0, index 1 should win.
    const tracker = new MelodyTracker(boxes, { requiredStreak: 1 })

    const changed = tracker.observe(['G4'])
    expect(tracker.getCurrentNote()).toBe(boxes[1])
    expect(changed).toBe(boxes[1])
  })

  it('does not move on a single unconfirmed detection', () => {
    const boxes = makeSequenceBoxes(['C4', 'D4', 'E4'])
    const tracker = new MelodyTracker(boxes, { requiredStreak: 2 })

    expect(tracker.observe(['E4'])).toBeNull()
    expect(tracker.getCurrentNote()).toBe(boxes[0])
  })

  it('prefers a forward candidate over an equally-distant backward one', () => {
    const boxes = makeSequenceBoxes(['G4', 'C4', 'D4', 'E4', 'G4'])
    const tracker = new MelodyTracker(boxes, { requiredStreak: 1 })
    // Move to index 2 first.
    tracker.observe(['D4'])

    // G4 exists at index 0 and index 4 -- both exactly 2 notes from the
    // current index 2, but the backward penalty should make the forward
    // one (index 4) win despite the tie in raw distance.
    const changed = tracker.observe(['G4'])
    expect(changed).toBe(boxes[4])
  })

  it('returns null when the current note itself still matches (sustain)', () => {
    const boxes = makeSequenceBoxes(['C4', 'D4', 'E4'])
    const tracker = new MelodyTracker(boxes, { requiredStreak: 1 })

    expect(tracker.observe(['C4'])).toBeNull()
    expect(tracker.getCurrentNote()).toBe(boxes[0])
  })

  it('returns null when nothing in the score matches the detected pitch', () => {
    const boxes = makeSequenceBoxes(['C4', 'D4', 'E4'])
    const tracker = new MelodyTracker(boxes, { requiredStreak: 1 })

    expect(tracker.observe(['B5'])).toBeNull()
  })
})
