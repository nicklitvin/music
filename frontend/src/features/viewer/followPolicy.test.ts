import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { buildOnsets } from '../../audio/positionTracking'
import { buildLines, type ScoreLine } from '../../audio/scoreLines'
import {
  DEFAULT_ANCHOR,
  LineFollower,
  centreLine,
  followDecision,
  isVisible,
  visibleLines,
} from './followPolicy'
import type { NoteBoundingBox } from '../../lib/types'

// Real OMR output, if this clone has it. content/ is gitignored (it holds
// copyrighted sheet music), so this is skipped rather than failing when
// absent.
const ALIEZ_NOTES = resolve(process.cwd(), '../content/full/aliez/notes.json')
const hasAliez = existsSync(ALIEZ_NOTES)

const VIEWPORT = 800

/** Lays a score out the way the viewer does: pages stacked at full width,
 * each line's band converted to an absolute offset in the scroll column. */
function layout(lines: ScoreLine[], pageWidth: number, pageHeight: number, screenWidth: number) {
  const renderedPageHeight = (pageHeight / pageWidth) * screenWidth
  return lines.map((line) => ({
    top: line.pageIndex * renderedPageHeight + (line.top / pageHeight) * renderedPageHeight,
    bottom: line.pageIndex * renderedPageHeight + (line.bottom / pageHeight) * renderedPageHeight,
  }))
}

/** A synthetic score: `pages` pages of `perPage` evenly spaced lines. */
function syntheticLines(pages: number, perPage: number): ScoreLine[] {
  const out: ScoreLine[] = []
  let onset = 0
  for (let page = 0; page < pages; page++) {
    for (let i = 0; i < perPage; i++) {
      out.push({
        pageIndex: page,
        top: 200 + i * 450,
        bottom: 200 + i * 450 + 345,
        firstOnset: onset,
        endOnset: onset + 20,
      })
      onset += 20
    }
  }
  return out
}

/** Drives the follow policy through a play-through, line by line, exactly
 * as the viewer does: observe a reported line, scroll if it changed. */
function playThrough(
  bands: { top: number; bottom: number }[],
  reported: number[],
  { viewportHeight = VIEWPORT, confirmations = 2 } = {},
) {
  const follower = new LineFollower(confirmations)
  let scrollTop = 0
  let scrolls = 0
  const offScreen: number[] = []

  reported.forEach((lineIndex) => {
    const changed = follower.observe(lineIndex)
    if (changed !== null) {
      const target = followDecision(scrollTop, bands[changed], { viewportHeight })
      if (target !== null) {
        scrollTop = target
        scrolls += 1
      }
    }
    // After reacting, the line actually being played must be on screen.
    if (!isVisible(scrollTop, bands[lineIndex], viewportHeight)) offScreen.push(lineIndex)
  })

  return { scrollTop, scrolls, offScreen }
}

describe('followDecision', () => {
  it('centres the line so the ones before and after stay in view', () => {
    const band = { top: 5000, bottom: 5056 }
    const target = followDecision(0, band, { viewportHeight: VIEWPORT })

    expect(target).not.toBeNull()
    const centreOnScreen = (band.top + band.bottom) / 2 - target!
    expect(centreOnScreen).toBeCloseTo(VIEWPORT * DEFAULT_ANCHOR, 0)
    // Room above and below for neighbouring lines.
    expect(centreOnScreen).toBeGreaterThan(VIEWPORT * 0.2)
    expect(centreOnScreen).toBeLessThan(VIEWPORT * 0.8)
  })

  it('stays put when the line is already where it should be', () => {
    const band = { top: 5000, bottom: 5056 }
    const settled = followDecision(0, band, { viewportHeight: VIEWPORT })!
    expect(followDecision(settled, band, { viewportHeight: VIEWPORT })).toBeNull()
  })

  it('never scrolls above the top of the sheet', () => {
    const target = followDecision(500, { top: 10, bottom: 66 }, { viewportHeight: VIEWPORT })
    expect(target).toBeGreaterThanOrEqual(0)
  })
})

describe('LineFollower', () => {
  it('follows the first line it is told about immediately', () => {
    expect(new LineFollower(2).observe(4)).toBe(4)
  })

  it('ignores a one-frame flicker to a neighbouring line', () => {
    const follower = new LineFollower(2)
    follower.observe(5)
    expect(follower.observe(6)).toBeNull() // needs confirming
    expect(follower.observe(5)).toBeNull() // flicker over, still on 5
    expect(follower.currentLine).toBe(5)
  })

  it('follows a line that is reported consistently', () => {
    const follower = new LineFollower(2)
    follower.observe(5)
    expect(follower.observe(6)).toBeNull()
    expect(follower.observe(6)).toBe(6)
  })
})

describe('playing through a score', () => {
  // The regression this whole module exists for: with a viewport-fraction
  // deadzone, a line being ~7% of the viewport meant the sheet sat still
  // for nearly eight lines. Following must track the music line by line.
  it('keeps the played line on screen for a whole synthetic score', () => {
    const lines = syntheticLines(4, 7)
    const bands = layout(lines, 2480, 3509, 400)
    // The music advances a line at a time, each line held for ~15 updates.
    const reported = lines.flatMap((_, index) => Array(15).fill(index))

    const { scrolls, offScreen } = playThrough(bands, reported)

    expect(offScreen).toEqual([])
    // Roughly one scroll per line: not one per update, and not one per
    // eight lines. (The first few lines of the score all want a negative
    // offset and clamp to 0, so a handful of changes move nothing.)
    expect(scrolls).toBeGreaterThan(lines.length / 2)
    expect(scrolls).toBeLessThanOrEqual(lines.length + 2)
  })

  it.skipIf(!hasAliez)('keeps the played line on screen through the real aliez score', () => {
    const notes: NoteBoundingBox[] = JSON.parse(readFileSync(ALIEZ_NOTES, 'utf8'))
    const lines = buildLines(buildOnsets(notes))
    expect(lines.length).toBeGreaterThan(20)

    for (const screenWidth of [400, 900]) {
      const bands = layout(lines, 2480, 3509, screenWidth)
      const reported = lines.flatMap((_, index) => Array(12).fill(index))

      const { offScreen } = playThrough(bands, reported, { viewportHeight: VIEWPORT })

      expect(offScreen, `line off screen at ${screenWidth}px wide`).toEqual([])
    }
  })

  it('survives the model flickering between adjacent lines', () => {
    const lines = syntheticLines(2, 7)
    const bands = layout(lines, 2480, 3509, 400)
    // Advancing, but every other update reports the neighbouring line.
    const reported: number[] = []
    lines.forEach((_, index) => {
      for (let i = 0; i < 10; i++) reported.push(i % 2 === 0 ? index : Math.min(index + 1, lines.length - 1))
    })

    const { scrolls, offScreen } = playThrough(bands, reported)

    expect(offScreen).toEqual([])
    // Flicker must not turn into a scroll per update.
    expect(scrolls).toBeLessThanOrEqual(lines.length * 2)
  })

  it('a reader scrolling away is not fought, and following resumes after', () => {
    // (kept below; the fuller manual-scroll behaviour is its own describe)
    const lines = syntheticLines(3, 7)
    const bands = layout(lines, 2480, 3509, 400)
    const follower = new LineFollower(2)

    // Following line 3.
    follower.observe(3)
    let scrollTop = followDecision(0, bands[3], { viewportHeight: VIEWPORT })!

    // The reader scrolls off to line 15 themselves. The viewer holds the
    // model off and adopts where they landed.
    const manual = bands[15].top - VIEWPORT * DEFAULT_ANCHOR
    scrollTop = manual
    follower.reset(15)

    // Stale reports for the old place arrive during the hold and are
    // ignored by the viewer, so nothing moves.
    expect(follower.observe(15)).toBeNull()
    expect(scrollTop).toBe(manual)

    // Once the music reaches the new place, following resumes from there.
    expect(follower.observe(16)).toBeNull()
    expect(follower.observe(16)).toBe(16)
    const resumed = followDecision(scrollTop, bands[16], { viewportHeight: VIEWPORT })
    expect(resumed).not.toBeNull()
    expect(isVisible(resumed!, bands[16], VIEWPORT)).toBe(true)
  })
})

describe('manual scroll correction', () => {
  const lines = syntheticLines(3, 7)
  const bands = layout(lines, 2480, 3509, 400)

  /** What the viewer does when a scroll settles: work out what is on
   * screen, which line the reader is looking at, and tell the model. */
  function onScrollSettled(scrollTop: number, viewportHeight = VIEWPORT) {
    const visible = visibleLines(scrollTop, viewportHeight, bands)
    const centre = centreLine(scrollTop, viewportHeight, bands)
    return { visible, centre }
  }

  it('reports the line the reader scrolled to, not the one they left', () => {
    // Reader drags down to line 12.
    const scrollTop = bands[12].top - VIEWPORT * DEFAULT_ANCHOR
    const { centre, visible } = onScrollSettled(scrollTop)

    expect(centre).toBe(12)
    expect(visible).toContain(12)
  })

  it('the reported viewport actually contains the line it nominates', () => {
    for (let line = 0; line < bands.length; line++) {
      const scrollTop = Math.max(0, bands[line].top - VIEWPORT * DEFAULT_ANCHOR)
      const { centre, visible } = onScrollSettled(scrollTop)
      expect(visible).toContain(centre!)
      expect(isVisible(scrollTop, bands[centre!], VIEWPORT)).toBe(true)
    }
  })

  it('scrolling to an arbitrary offset still nominates a visible line', () => {
    const lowest = bands[bands.length - 1].bottom
    for (let scrollTop = 0; scrollTop < lowest; scrollTop += 137) {
      const { centre } = onScrollSettled(scrollTop)
      expect(centre).not.toBeNull()
      expect(isVisible(scrollTop, bands[centre!], VIEWPORT)).toBe(true)
    }
  })

  it('does not bounce back to where the model still thinks it is', () => {
    const follower = new LineFollower(2)
    follower.observe(2)
    let scrollTop = followDecision(0, bands[2], { viewportHeight: VIEWPORT })!

    // Reader scrolls far ahead to line 17 and the viewer adopts it.
    scrollTop = bands[17].top - VIEWPORT * DEFAULT_ANCHOR
    const { centre } = onScrollSettled(scrollTop)
    follower.reset(centre)

    // The model, still catching up, reports the old line twice. Since it
    // is outside the window the viewer reported, the backend would not send
    // this -- but even if it does, adopting it must not drag the reader
    // back without the usual confirmation.
    expect(follower.observe(2)).toBeNull()
    expect(scrollTop).toBe(bands[17].top - VIEWPORT * DEFAULT_ANCHOR)
  })

  it('a correction mid-flicker still lands on the reader choice', () => {
    const follower = new LineFollower(2)
    follower.observe(5)
    follower.observe(6) // half-confirmed move pending

    // Reader scrolls somewhere else entirely; the pending move must be
    // dropped, not applied after the fact.
    const scrollTop = bands[11].top - VIEWPORT * DEFAULT_ANCHOR
    follower.reset(centreLine(scrollTop, VIEWPORT, bands))

    expect(follower.currentLine).toBe(11)
    expect(follower.observe(6)).toBeNull() // stale, needs re-confirming
  })

  it('following resumes from the reader position, moving forward not back', () => {
    const follower = new LineFollower(2)
    const scrollTop = bands[9].top - VIEWPORT * DEFAULT_ANCHOR
    follower.reset(centreLine(scrollTop, VIEWPORT, bands))

    // Music carries on from there.
    expect(follower.observe(10)).toBeNull()
    const next = follower.observe(10)
    expect(next).toBe(10)

    const target = followDecision(scrollTop, bands[10], { viewportHeight: VIEWPORT })
    expect(target).not.toBeNull()
    expect(target!).toBeGreaterThan(scrollTop) // forward, not back
  })

  it('a reader scrolling backwards is followed backwards', () => {
    const follower = new LineFollower(2)
    const scrollTop = bands[4].top - VIEWPORT * DEFAULT_ANCHOR
    follower.reset(centreLine(scrollTop, VIEWPORT, bands))
    expect(follower.currentLine).toBe(4)

    expect(follower.observe(5)).toBeNull()
    expect(follower.observe(5)).toBe(5)
  })
})
