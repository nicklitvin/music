import { describe, expect, it } from 'vitest'
import { buildOnsets } from './positionTracking'
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
