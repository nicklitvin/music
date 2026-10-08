import { afterEach, describe, expect, it, vi } from 'vitest'
import { uuid } from './uuid'

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

afterEach(() => vi.unstubAllGlobals())

describe('uuid', () => {
  it('produces a v4 uuid', () => {
    expect(uuid()).toMatch(V4)
  })

  it('does not repeat itself', () => {
    const seen = new Set(Array.from({ length: 500 }, () => uuid()))
    expect(seen.size).toBe(500)
  })

  // The production bug: served over plain HTTP from an IP, the page is not
  // a secure context, so crypto.randomUUID does not exist. Calling it threw
  // inside the file-picker handler before any state was set, so choosing a
  // PDF appeared to do nothing at all.
  it('still works where crypto.randomUUID is missing (insecure context)', () => {
    vi.stubGlobal('crypto', { getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto) })

    expect(() => uuid()).not.toThrow()
    expect(uuid()).toMatch(V4)
    const seen = new Set(Array.from({ length: 200 }, () => uuid()))
    expect(seen.size).toBe(200)
  })

  it('still works with no Web Crypto at all', () => {
    vi.stubGlobal('crypto', undefined)

    expect(() => uuid()).not.toThrow()
    expect(uuid()).toMatch(V4)
  })
})
