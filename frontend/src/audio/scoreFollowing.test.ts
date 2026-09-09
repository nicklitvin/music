import { describe, expect, it } from 'vitest'
import { groupBoundingBoxesIntoLines } from './scoreFollowing'
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
