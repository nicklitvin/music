import { describe, expect, it } from 'vitest'
import { computeLineFingerprint, groupBoundingBoxesIntoLines, LineTracker, type ScoreLine } from './scoreFollowing'
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

describe('groupBoundingBoxesIntoLines', () => {
  it('clusters notes with close y-values into the same line', () => {
    const boxes = [
      box({ pitch: 'C4', y: 100, x: 0 }),
      box({ pitch: 'E4', y: 102, x: 10 }),
      box({ pitch: 'G4', y: 99, x: 20 }),
      box({ pitch: 'A4', y: 300, x: 0 }),
    ]

    const lines = groupBoundingBoxesIntoLines(boxes, { 0: 1000 })

    expect(lines).toHaveLength(2)
    expect(lines[0].boxes.map((b) => b.pitch)).toEqual(['C4', 'E4', 'G4'])
    expect(lines[1].boxes.map((b) => b.pitch)).toEqual(['A4'])
  })

  it('keeps pages independent', () => {
    const boxes = [box({ pageIndex: 0, y: 100 }), box({ pageIndex: 1, y: 100 })]

    const lines = groupBoundingBoxesIntoLines(boxes, { 0: 1000, 1: 1000 })

    expect(lines).toHaveLength(2)
    expect(lines.map((l) => l.pageIndex)).toEqual([0, 1])
  })
})

describe('computeLineFingerprint', () => {
  it('prefers pitches not shared with neighboring lines', () => {
    const lines: ScoreLine[] = [
      { pageIndex: 0, lineIndex: 0, y: 0, boxes: [box({ pitch: 'C4' }), box({ pitch: 'E4' })] },
      { pageIndex: 0, lineIndex: 1, y: 1, boxes: [box({ pitch: 'E4' }), box({ pitch: 'G5' })] },
      { pageIndex: 0, lineIndex: 2, y: 2, boxes: [box({ pitch: 'E4' }), box({ pitch: 'C4' })] },
    ]

    expect(computeLineFingerprint(lines, 1)).toEqual(['G5'])
  })

  it('falls back to the full pitch set when nothing is distinctive', () => {
    const lines: ScoreLine[] = [
      { pageIndex: 0, lineIndex: 0, y: 0, boxes: [box({ pitch: 'C4' })] },
      { pageIndex: 0, lineIndex: 1, y: 1, boxes: [box({ pitch: 'C4' })] },
      { pageIndex: 0, lineIndex: 2, y: 2, boxes: [box({ pitch: 'C4' })] },
    ]

    expect(computeLineFingerprint(lines, 1)).toEqual(['C4'])
  })
})

function makeLines(pitchSets: string[][]): ScoreLine[] {
  return pitchSets.map((pitches, lineIndex) => ({
    pageIndex: 0,
    lineIndex,
    y: lineIndex * 100,
    boxes: pitches.map((pitch) => box({ pitch })),
  }))
}

describe('LineTracker', () => {
  it('advances to a line only once its fingerprint is observed repeatedly', () => {
    const lines = makeLines([['C4'], ['D4'], ['E4', 'G4'], ['F4']])
    const tracker = new LineTracker(lines, { requiredStreak: 2 })

    expect(tracker.observe(['E4', 'G4'])).toBeNull()
    const changed = tracker.observe(['E4', 'G4'])

    expect(changed?.lineIndex).toBe(2)
    expect(tracker.getCurrentLine()?.lineIndex).toBe(2)
  })

  it('does not advance on a single noisy detection', () => {
    const lines = makeLines([['C4'], ['D4'], ['E4']])
    const tracker = new LineTracker(lines, { requiredStreak: 2 })

    expect(tracker.observe(['E4'])).toBeNull()
    expect(tracker.getCurrentLine()?.lineIndex).toBe(0)
  })

  it('ignores matches outside the lookahead/lookbehind window', () => {
    const lines = makeLines([['C4'], ['D4'], ['E4'], ['F4'], ['G4'], ['A4'], ['B4']])
    const tracker = new LineTracker(lines, { requiredStreak: 1, lookaheadLines: 2 })

    // 'B4' belongs to the last line, far outside the lookahead window from line 0.
    expect(tracker.observe(['B4'])).toBeNull()
    expect(tracker.getCurrentLine()?.lineIndex).toBe(0)
  })

  it('resets the match streak when a different candidate interrupts it', () => {
    const lines = makeLines([['C4'], ['D4'], ['E4'], ['F4']])
    const tracker = new LineTracker(lines, { requiredStreak: 2 })

    expect(tracker.observe(['E4'])).toBeNull() // pending -> line 2
    expect(tracker.observe(['F4'])).toBeNull() // pending resets -> line 3, streak 1
    expect(tracker.observe(['E4'])).toBeNull() // pending resets -> line 2, streak 1
    expect(tracker.observe(['E4'])?.lineIndex).toBe(2) // streak 2 -> commit
  })
})
