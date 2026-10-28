import { describe, expect, it } from 'vitest'
import { isTap, lineAt } from './tap'

describe('isTap', () => {
  it('accepts a quick touch that barely moves', () => {
    expect(isTap(2, 120)).toBe(true)
  })

  it('rejects a scroll or drag', () => {
    expect(isTap(80, 200)).toBe(false)
  })

  it('rejects a long press', () => {
    expect(isTap(1, 900)).toBe(false)
  })

  it('tolerates the wobble of a real finger', () => {
    expect(isTap(9, 180)).toBe(true)
  })
})

describe('lineAt', () => {
  const bands = [
    { top: 100, bottom: 200 },
    { top: 260, bottom: 360 },
    { top: 420, bottom: 520 },
  ]

  it('picks the line under the click', () => {
    expect(lineAt(150, bands)).toBe(0)
    expect(lineAt(300, bands)).toBe(1)
    expect(lineAt(519, bands)).toBe(2)
  })

  it('picks the nearer line for a click between two', () => {
    expect(lineAt(220, bands)).toBe(0)
    expect(lineAt(250, bands)).toBe(1)
  })

  it('picks the first or last line for a click in the margins', () => {
    expect(lineAt(10, bands)).toBe(0)
    expect(lineAt(5000, bands)).toBe(2)
  })

  it('skips lines that are not laid out yet', () => {
    expect(lineAt(10, [{ top: 0, bottom: 0 }, ...bands])).toBe(1)
    expect(lineAt(10, [])).toBeNull()
  })
})
