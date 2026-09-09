import { describe, expect, it } from 'vitest'
import { groupBoundingBoxesIntoLines, groupLinesIntoSystems } from './scoreFollowing'
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

describe('groupLinesIntoSystems', () => {
  const pageHeight = 3500

  // Six evenly-spaced staff rows (~210px apart) then a clear gap and two
  // more -- like a page of grand staves where gap clustering alone can't
  // tell the two staves of a system apart.
  function pageOfRows(centers: number[]) {
    return groupBoundingBoxesIntoLines(
      centers.map((y, i) => box({ y, x: i * 5 })),
      { 0: pageHeight },
    )
  }

  it('pairs evenly spaced rows two-to-a-system', () => {
    const systems = groupLinesIntoSystems(pageOfRows([400, 610, 900, 1110, 1400, 1610]), { 0: pageHeight })

    expect(systems).toHaveLength(3)
    expect(systems.map((s) => s.lineIndexes)).toEqual([
      [0, 1],
      [2, 3],
      [4, 5],
    ])
    // The band spans both staves of the pair.
    expect(systems[0].top).toBeLessThan(400)
    expect(systems[0].bottom).toBeGreaterThan(610)
  })

  it('breaks a system at an unusually large vertical gap', () => {
    // rows at 400/610 (system) ... big gap ... 1400/1610 (system)
    const systems = groupLinesIntoSystems(pageOfRows([400, 610, 1400, 1610]), { 0: pageHeight })

    expect(systems.map((s) => s.lineIndexes)).toEqual([
      [0, 1],
      [2, 3],
    ])
  })

  it('leaves an odd row in a run as its own system', () => {
    const systems = groupLinesIntoSystems(pageOfRows([400, 610, 820]), { 0: pageHeight })
    expect(systems.map((s) => s.lineIndexes)).toEqual([[0, 1], [2]])
  })
})
