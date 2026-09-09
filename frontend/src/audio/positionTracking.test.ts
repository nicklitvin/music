import { describe, expect, it } from 'vitest'
import { buildOnsets, nearestOnset } from './positionTracking'
import type { NoteBoundingBox } from '../lib/types'

function box(overrides: Partial<NoteBoundingBox>): NoteBoundingBox {
  return {
    x: 0,
    y: 0,
    width: 10,
    height: 10,
    note: 'quarter',
    pitch: 'C4',
    measureIndex: 1,
    pageIndex: 0,
    ...overrides,
  }
}

describe('buildOnsets', () => {
  it('groups notes at the same x into one onset, in reading order', () => {
    const onsets = buildOnsets([
      box({ pitch: 'G4', x: 200 }),
      box({ pitch: 'C4', x: 100 }),
      box({ pitch: 'E4', x: 103 }), // within tolerance of the C4 -> same onset
    ])

    expect(onsets.map((o) => o.map((b) => b.pitch))).toEqual([['C4', 'E4'], ['G4']])
  })

  it('separates onsets more than the x tolerance apart', () => {
    const onsets = buildOnsets([box({ x: 100 }), box({ x: 120 })])
    expect(onsets).toHaveLength(2)
  })

  it('orders by page, then measure, then x', () => {
    const onsets = buildOnsets([
      box({ pitch: 'later', pageIndex: 1, x: 0 }),
      box({ pitch: 'first', pageIndex: 0, measureIndex: 1, x: 500 }),
      box({ pitch: 'mid', pageIndex: 0, measureIndex: 2, x: 0 }),
    ])

    expect(onsets.map((o) => o[0].pitch)).toEqual(['first', 'mid', 'later'])
  })
})

describe('nearestOnset', () => {
  const onsets = buildOnsets([
    box({ pitch: 'a', x: 100, y: 100, measureIndex: 1 }),
    box({ pitch: 'b', x: 300, y: 100, measureIndex: 2 }),
    box({ pitch: 'c', x: 100, y: 600, measureIndex: 5 }),
  ])

  it('returns the index of the onset closest to a point', () => {
    expect(onsets[nearestOnset(onsets, 0, 110, 105)][0].pitch).toBe('a')
    expect(onsets[nearestOnset(onsets, 0, 290, 120)][0].pitch).toBe('b')
    expect(onsets[nearestOnset(onsets, 0, 90, 590)][0].pitch).toBe('c')
  })

  it('returns -1 when no onset is on the given page', () => {
    expect(nearestOnset(onsets, 5, 100, 100)).toBe(-1)
  })
})
