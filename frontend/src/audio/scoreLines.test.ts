import { describe, expect, it } from 'vitest'
import { buildOnsets } from './positionTracking'
import { buildLines, lineForOnset } from './scoreLines'
import type { NoteBoundingBox } from '../lib/types'

function note(x: number, y: number, measureIndex: number, pageIndex = 0): NoteBoundingBox {
  return { x, y, width: 10, height: 20, note: 'quarter', pitch: 'C4', measureIndex, pageIndex }
}

// Two grand-staff systems: each is a treble row and a bass row close
// together, with a much bigger gap to the next system.
function twoSystems(pageIndex = 0): NoteBoundingBox[] {
  return [
    note(100, 100, 1, pageIndex), note(200, 100, 1, pageIndex), note(300, 100, 2, pageIndex),
    note(100, 180, 1, pageIndex), note(200, 180, 1, pageIndex), note(300, 180, 2, pageIndex),
    note(100, 600, 3, pageIndex), note(200, 600, 3, pageIndex), note(300, 600, 4, pageIndex),
    note(100, 680, 3, pageIndex), note(200, 680, 3, pageIndex), note(300, 680, 4, pageIndex),
  ]
}

describe('buildLines', () => {
  it('treats the two staves of a grand staff as one line', () => {
    const lines = buildLines(buildOnsets(twoSystems()))

    expect(lines).toHaveLength(2)
    // First line spans treble top through bass bottom.
    expect(lines[0].top).toBe(100)
    expect(lines[0].bottom).toBe(200)
    expect(lines[1].top).toBe(600)
  })

  it('never merges across a page boundary', () => {
    const lines = buildLines(buildOnsets([...twoSystems(0), ...twoSystems(1)]))

    expect(lines).toHaveLength(4)
    expect(lines.map((l) => l.pageIndex)).toEqual([0, 0, 1, 1])
  })

  it('maps an onset back to the line it sits on', () => {
    const onsets = buildOnsets(twoSystems())
    const lines = buildLines(onsets)

    expect(lineForOnset(lines, lines[1].firstOnset)).toBe(lines[1])
    expect(lineForOnset(lines, 0)).toBe(lines[0])
  })

  it('handles a score with no notes', () => {
    expect(buildLines([])).toEqual([])
    expect(lineForOnset([], 0)).toBeNull()
  })
})

describe('buildLines merge limits', () => {
  // Rows at an even spacing with no "system" pairing: nothing should be
  // merged into a band several lines tall.
  function evenRows(count: number): NoteBoundingBox[] {
    const out: NoteBoundingBox[] = []
    for (let row = 0; row < count; row++) {
      for (let i = 0; i < 3; i++) out.push(note(100 + i * 100, row * 200, row * 2 + 1))
    }
    return out
  }

  it('never folds more than a grand staff into one line', () => {
    const lines = buildLines(buildOnsets(evenRows(8)))
    // At most two rows per line, so at least half as many lines as rows.
    expect(lines.length).toBeGreaterThanOrEqual(4)
    for (const line of lines) {
      expect(line.bottom - line.top).toBeLessThanOrEqual(220)
    }
  })

  it('keeps lines in reading order with non-overlapping onset ranges', () => {
    const lines = buildLines(buildOnsets(evenRows(6)))
    for (let i = 1; i < lines.length; i++) {
      expect(lines[i].firstOnset).toBeGreaterThanOrEqual(lines[i - 1].endOnset)
    }
  })
})

describe('rows that already span both clefs', () => {
  // The aliez case: OMR numbers measures per system, so treble and bass
  // interleave in reading order and a row already *is* a whole system.
  // Merging two of those gives a band from one system's bass to the next
  // system's treble -- a highlight straddling two lines.
  function systemRow(y: number, measure: number): NoteBoundingBox[] {
    const out: NoteBoundingBox[] = []
    for (let i = 0; i < 6; i++) {
      // Treble and bass of the same system, interleaved left to right.
      out.push(note(100 + i * 60, y, measure))
      out.push({ ...note(100 + i * 60, y + 180, measure), pitch: 'C2' })
    }
    return out.map((n, i) => (i % 2 === 0 ? { ...n, pitch: 'G5' } : n))
  }

  it('does not weld one system to the next', () => {
    // Two systems, close enough together that a gap rule alone would merge.
    const notes = [...systemRow(200, 1), ...systemRow(460, 3)]
    const lines = buildLines(buildOnsets(notes))

    expect(lines).toHaveLength(2)
    expect(lines[0].bottom).toBeLessThan(lines[1].top)
  })

  it('still rejoins genuine single-clef halves', () => {
    // Treble row then bass row, each covering one clef only.
    const treble = [0, 1, 2].map((i) => ({ ...note(100 + i * 60, 200, 1), pitch: 'G5' }))
    const bass = [0, 1, 2].map((i) => ({ ...note(100 + i * 60, 260, 1), pitch: 'C2' }))
    const lines = buildLines(buildOnsets([...treble, ...bass]))

    expect(lines).toHaveLength(1)
    expect(lines[0].top).toBe(200)
    expect(lines[0].bottom).toBe(280)
  })
})
