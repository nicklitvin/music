import { describe, expect, it } from 'vitest'
import { isTap, tapAction } from './tapZones'

const W = 400
const H = 800

describe('tapAction', () => {
  it('maps the side columns to line movement', () => {
    expect(tapAction(20, 300, W, H)).toBe('previous-line')
    expect(tapAction(W - 20, 300, W, H)).toBe('next-line')
  })

  it('maps the middle column to page movement, top and bottom', () => {
    expect(tapAction(W / 2, 100, W, H)).toBe('previous-page')
    expect(tapAction(W / 2, H * 0.6, W, H)).toBe('next-page')
  })

  it('leaves the strip the nav pill sits in alone', () => {
    expect(tapAction(W / 2, H - 10, W, H)).toBeNull()
    // Including in the side columns, where a near-miss on Info or Settings
    // would otherwise jump a line.
    expect(tapAction(10, H - 10, W, H)).toBeNull()
    expect(tapAction(W - 10, H - 10, W, H)).toBeNull()
  })

  it('covers the whole usable area with no dead spots', () => {
    for (let x = 0; x < W; x += 10) {
      for (let y = 0; y < H * (1 - 0.16); y += 10) {
        expect(tapAction(x, y, W, H), `at ${x},${y}`).not.toBeNull()
      }
    }
  })

  it('is symmetric about the middle', () => {
    for (let y = 50; y < 600; y += 50) {
      expect(tapAction(5, y, W, H)).toBe('previous-line')
      expect(tapAction(W - 5, y, W, H)).toBe('next-line')
    }
  })

  it('handles a zero-sized element without throwing', () => {
    expect(tapAction(0, 0, 0, 0)).toBeNull()
  })

  it('works the same on a wide screen', () => {
    expect(tapAction(50, 400, 1200, H)).toBe('previous-line')
    expect(tapAction(600, 200, 1200, H)).toBe('previous-page')
    expect(tapAction(1150, 400, 1200, H)).toBe('next-line')
  })
})

describe('isTap', () => {
  it('accepts a still, brief touch', () => {
    expect(isTap(2, 120)).toBe(true)
  })

  it('rejects a scroll', () => {
    expect(isTap(80, 200)).toBe(false)
  })

  it('rejects a long press', () => {
    expect(isTap(1, 900)).toBe(false)
  })

  it('tolerates the small wobble of a real finger', () => {
    expect(isTap(9, 180)).toBe(true)
  })
})
