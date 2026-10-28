import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Mic, Square } from 'lucide-react'
import { getScore } from '../../lib/db'
import type { ScorePositionEvent, ScoreRecord } from '../../lib/types'
import { useAudioTracking } from '../../audio/useAudioTracking'
import { buildOnsets } from '../../audio/positionTracking'
import { buildLines, lineForOnset, type ScoreLine } from '../../audio/scoreLines'
import { LineFollower, followDecision, visibleLines, type LineBand } from './followPolicy'
import { isTap, lineAt } from './tap'
import { BottomNav, type PillAction } from '../../components/BottomNav'

// After the reader stops moving the sheet themselves, the model stays out
// of the way for this long. Measured from when scrolling *stops*, not when
// it starts, so a long scroll isn't interrupted halfway. The backend holds
// its reported position for a comparable window; this is the same promise
// kept on the client.
const MANUAL_HOLD_MS = 4000
// A scroll counts as finished once this long passes with no scroll events.
const SCROLL_SETTLE_MS = 400
// Lines either side of the reader's line (or, before they have picked one,
// of the screen) the model may consider. Forward is the point -- what the
// model is really for is noticing the move to the next line -- but a little
// slack behind stops it being boxed in when the reader sits right at the
// top of a line.
const LOOKAHEAD_LINES = 2
const LOOKBEHIND_LINES = 1

const INFO_CONTENT = (
  <>
    <p>
      The whole sheet is one continuous scroll. Press Record and play -- the sheet follows where it hears you.
    </p>
    <ul>
      <li>
        Click or tap a line to say where you are. The highlight moves there and the sheet follows on from it as you
        play.
      </li>
      <li>
        Scroll by hand to look around any time. It doesn't change where you are, and the sheet waits a few seconds
        before following again.
      </li>
      <li>
        Your playing is never recorded -- audio is analysed as it arrives and discarded. The server keeps a log of the
        notes it heard and where it placed you, to debug tracking.
      </li>
    </ul>
  </>
)

export function ScoreViewer() {
  const { scoreId } = useParams<{ scoreId: string }>()
  const navigate = useNavigate()
  const [score, setScore] = useState<ScoreRecord | null>(null)
  const [pageUrls, setPageUrls] = useState<string[]>([])
  const [listening, setListening] = useState(false)
  const [currentLine, setCurrentLine] = useState<ScoreLine | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<(HTMLDivElement | null)[]>([])
  const holdUntilRef = useRef(0)
  const scrollSettleRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const programmaticUntilRef = useRef(0)
  const followerRef = useRef(new LineFollower())
  // Set once the reader has clicked a line: from then on the model is kept
  // to the lines around where they are, not around whatever is on screen,
  // so scrolling away to look ahead never drags their place along.
  const pickedLineRef = useRef(false)
  // Set below, once the socket that sends it exists; the position handler
  // has to be created before that.
  const confineAroundRef = useRef<(index: number) => void>(() => {})

  useEffect(() => {
    if (!scoreId) return
    getScore(scoreId).then((record) => {
      if (!record) return
      setScore(record)
      setPageUrls(record.pages.map((page) => URL.createObjectURL(page.image)))
    })
  }, [scoreId])

  useEffect(() => {
    return () => {
      pageUrls.forEach((url) => URL.revokeObjectURL(url))
    }
  }, [pageUrls])

  // The same onset grouping the backend tracker indexes into, so a
  // reported onsetIndex can be mapped back to a place on the sheet.
  const onsets = useMemo(() => (score ? buildOnsets(score.boundingBoxes) : []), [score])
  const lines = useMemo(() => buildLines(onsets), [onsets])

  // Typical line height, used to keep the highlight honest. Line grouping
  // occasionally welds two systems together on densely engraved pages, and
  // an 800px band over a 345px line reads as "four lines are highlighted".
  // Capping against the median bounds that damage to one line's worth.
  const medianLineHeight = useMemo(() => {
    if (!lines.length) return 0
    const heights = lines.map((line) => line.bottom - line.top).sort((a, b) => a - b)
    return heights[Math.floor(heights.length / 2)]
  }, [lines])

  const pageArrayIndexByPageNumber = useMemo(() => {
    const map = new Map<number, number>()
    score?.pages.forEach((page, index) => map.set(page.pageIndex, index))
    return map
  }, [score])

  const holdOff = useCallback(() => {
    holdUntilRef.current = Date.now() + MANUAL_HOLD_MS
  }, [])

  // Marks a scroll as ours for as long as a smooth scroll plausibly runs.
  const scrollOurselves = useCallback((container: HTMLDivElement, top: number) => {
    programmaticUntilRef.current = Date.now() + 1000
    container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
  }, [])

  // Every line's band as an absolute offset in the scroll column, measured
  // from the laid-out pages. Recomputed on demand because it depends on the
  // rendered width, which changes with the window.
  const lineBands = useCallback((): LineBand[] => {
    if (!score) return []
    return lines.map((line) => {
      const pageIndex = pageArrayIndexByPageNumber.get(line.pageIndex)
      const element = pageIndex === undefined ? null : pageRefs.current[pageIndex]
      const page = pageIndex === undefined ? null : score.pages[pageIndex]
      if (!element || !page) return { top: 0, bottom: 0 }
      const scale = element.offsetHeight / page.height
      return {
        top: element.offsetTop + line.top * scale,
        bottom: element.offsetTop + line.bottom * scale,
      }
    })
  }, [lines, pageArrayIndexByPageNumber, score])

  const handlePosition = useCallback(
    (event: ScorePositionEvent) => {
      const line = lineForOnset(lines, event.onsetIndex)
      if (line === null) return
      const lineIndex = lines.indexOf(line)
      setCurrentLine((previous) => (previous?.firstOnset === line.firstOnset ? previous : line))

      // Scroll on a confirmed change of line, not on a distance threshold.
      // A line is only ~7% of a phone viewport, so any threshold big enough
      // to damp jitter is also big enough to stop following altogether.
      const changed = followerRef.current.observe(lineIndex)
      if (changed === null) return
      // The window travels with the reader's line as the model follows it on.
      if (pickedLineRef.current) confineAroundRef.current(changed)

      const container = scrollRef.current
      if (!container || Date.now() < holdUntilRef.current) return
      const bands = lineBands()
      const target = followDecision(container.scrollTop, bands[changed], {
        viewportHeight: container.clientHeight,
      })
      if (target !== null) scrollOurselves(container, target)
    },
    [lineBands, lines, scrollOurselves],
  )

  const { error: trackingError, start, stop, sendHint, sendViewport } = useAudioTracking({
    onTracking: () => {},
    onPosition: handlePosition,
    scoreNotes: score?.boundingBoxes,
  })

  // Confine the model to a few lines either side of `first`..`last`. This is
  // the constraint that keeps it honest: rather than ranking the whole
  // score, it only has to notice the move to the next line.
  const confine = useCallback(
    (first: number, last: number) => {
      if (!lines.length) return
      const low = Math.max(0, first - LOOKBEHIND_LINES)
      const high = Math.min(lines.length - 1, last + LOOKAHEAD_LINES)
      sendViewport({ firstOnset: lines[low].firstOnset, lastOnset: lines[high].endOnset - 1 })
    },
    [lines, sendViewport],
  )
  confineAroundRef.current = (index: number) => confine(index, index)

  // Before the reader has picked a line, the best guess at where they are
  // is the music on screen: the model may only place them there.
  const confineToScreen = useCallback(() => {
    const container = scrollRef.current
    if (!container) return
    const visible = visibleLines(container.scrollTop, container.clientHeight, lineBands())
    if (visible.length) confine(visible[0], visible[visible.length - 1])
  }, [confine, lineBands])

  // The reader says where they are, and that is the source of truth: the
  // highlight moves there, the model is told firmly (it holds the position
  // for a few seconds, then follows on from it), and from now on its window
  // is kept around this line rather than the screen.
  const pickLine = useCallback(
    (index: number) => {
      pickedLineRef.current = true
      holdOff()
      followerRef.current.reset(index)
      setCurrentLine(lines[index])
      sendHint(lines[index].firstOnset, { firm: true })
      confine(index, index)
    },
    [confine, holdOff, lines, sendHint],
  )

  // Scrolling by hand is only looking around: it never moves the reader's
  // place. It does hold the model off for a few seconds after it stops, so
  // the sheet isn't yanked back while they read ahead. Until a line has
  // been picked, the window follows the screen instead.
  //
  // Smooth scrolls the model itself started also raise these events; without
  // skipping those it would hold itself off after every move it made and
  // never follow anything again.
  const onScroll = useCallback(() => {
    if (Date.now() < programmaticUntilRef.current) return
    holdUntilRef.current = Date.now() + MANUAL_HOLD_MS
    if (scrollSettleRef.current) clearTimeout(scrollSettleRef.current)
    scrollSettleRef.current = setTimeout(() => {
      holdUntilRef.current = Date.now() + MANUAL_HOLD_MS
      if (!pickedLineRef.current) confineToScreen()
    }, SCROLL_SETTLE_MS)
  }, [confineToScreen])

  // Tracking starts when the reader asks for it, not on arrival: opening a
  // sheet to read should not switch the microphone on.
  const toggleListening = useCallback(() => {
    if (listening) {
      stop()
      setListening(false)
    } else {
      void start().then(() => {
        // Open with the model already placed: on the line the reader
        // picked, or else confined to what's on screen, rather than letting
        // it range over the whole score for the first seconds.
        const picked = followerRef.current.currentLine
        if (pickedLineRef.current && picked !== null) pickLine(picked)
        else confineToScreen()
      })
      setListening(true)
    }
  }, [confineToScreen, listening, pickLine, start, stop])

  useEffect(() => {
    return () => {
      stop()
      if (scrollSettleRef.current) clearTimeout(scrollSettleRef.current)
    }
  }, [stop])

  // A click or tap on the sheet picks the line under it. Only a tap counts:
  // touching down to scroll, or dragging, must not move the reader's place.
  const tapStartRef = useRef<{ x: number; y: number; at: number } | null>(null)

  const onSheetPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      holdOff()
      tapStartRef.current = event.button === 0 ? { x: event.clientX, y: event.clientY, at: Date.now() } : null
    },
    [holdOff],
  )

  const onSheetPointerUp = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const start = tapStartRef.current
      tapStartRef.current = null
      const container = scrollRef.current
      if (!start || !container || !lines.length) return
      const moved = Math.hypot(event.clientX - start.x, event.clientY - start.y)
      if (!isTap(moved, Date.now() - start.at)) return

      const y = container.scrollTop + event.clientY - container.getBoundingClientRect().top
      const index = lineAt(y, lineBands())
      if (index === null) return
      pickLine(index)
    },
    [lineBands, lines.length, pickLine],
  )

  if (!score) return <p>Loading score…</p>

  const actions: PillAction[] = [
    // Phones and tablets have a system back gesture, so this would only be
    // taking up room in the pill where room is tightest; CSS hides it below
    // the breakpoint rather than this being a device sniff.
    {
      key: 'back',
      label: 'Scores',
      icon: <ArrowLeft size={20} />,
      onClick: () => navigate('/scores'),
      desktopOnly: true,
    },
    {
      key: 'record',
      label: listening ? 'Stop' : 'Record',
      icon: listening ? <Square size={20} /> : <Mic size={20} />,
      onClick: toggleListening,
      emphasized: !listening,
      danger: listening,
    },
  ]

  return (
    <div className="sheet-page">
      {trackingError && <div className="sheet-tracking-error">{trackingError}</div>}

      <div
        className="sheet-scroll"
        ref={scrollRef}
        onScroll={onScroll}
        onPointerDown={onSheetPointerDown}
        onPointerUp={onSheetPointerUp}
      >
        {score.pages.map((page, index) => (
          <div
            className="sheet-page-block"
            key={page.pageIndex}
            ref={(el) => {
              pageRefs.current[index] = el
            }}
          >
            <img
              className="sheet-page-image"
              src={pageUrls[index]}
              width={page.width}
              height={page.height}
              alt={`Page ${page.pageIndex + 1}`}
              draggable={false}
            />
            {currentLine?.pageIndex === page.pageIndex &&
              (() => {
                const full = currentLine.bottom - currentLine.top
                const capped = medianLineHeight ? Math.min(full, medianLineHeight * 1.4) : full
                // Keep the band centred on the line it came from, so a
                // capped band sits over the music rather than its top edge.
                const top = currentLine.top + (full - capped) / 2
                // Percentages, so the band tracks the image as it scales to
                // whatever width the screen is.
                return (
                  <div
                    className="sheet-line-highlight"
                    style={{
                      top: `${(top / page.height) * 100}%`,
                      height: `${(capped / page.height) * 100}%`,
                    }}
                  />
                )
              })()}
          </div>
        ))}
      </div>

      <BottomNav infoTitle={score.title} infoContent={INFO_CONTENT} actions={actions} />
    </div>
  )
}
