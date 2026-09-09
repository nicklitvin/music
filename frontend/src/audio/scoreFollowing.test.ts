import { describe, expect, it } from 'vitest'
import { computeLineBands, groupBoundingBoxesIntoLines } from './scoreFollowing'
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

describe('computeLineBands', () => {
  const pageHeight = 3000

  it('makes each highlight band about a row-gap tall, so it spans a grand staff', () => {
    // Three thin rows (one staff each) 200px apart -- a highlight sized to
    // the noteheads alone would only cover one clef.
    const lines = groupBoundingBoxesIntoLines(
      [
        box({ y: 500, x: 0 }),
        box({ y: 510, x: 20 }),
        box({ y: 700, x: 0 }),
        box({ y: 900, x: 0 }),
      ],
      { 0: pageHeight },
    )
    const bands = computeLineBands(lines, { 0: pageHeight })

    const middle = bands.get('0-1')!
    expect(middle.height).toBeGreaterThan(180)
    expect(middle.height).toBeLessThanOrEqual(pageHeight * 0.11)
    // Centered on the row (~700), it reaches into both neighbours.
    expect(middle.top).toBeLessThan(700)
    expect(middle.top + middle.height).toBeGreaterThan(700)
  })

  it('never exceeds 11% of the page height', () => {
    const lines = groupBoundingBoxesIntoLines([box({ y: 100 }), box({ y: 2900 })], { 0: pageHeight })
    for (const band of computeLineBands(lines, { 0: pageHeight }).values()) {
      expect(band.height).toBeLessThanOrEqual(pageHeight * 0.11 + 1e-6)
    }
  })
})
