import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import JSZip from 'jszip'
import { ChevronDown, ChevronUp, Download, Mic, Square } from 'lucide-react'
import { getScore } from '../../lib/db'
import type { ScorePositionEvent, ScoreRecord } from '../../lib/types'
import { useAudioTracking } from '../../audio/useAudioTracking'
import { buildOnsets } from '../../audio/positionTracking'
import { buildLines, lineForOnset, type ScoreLine } from '../../audio/scoreLines'
import { BottomNav, type PillAction } from '../../components/BottomNav'

// After the reader stops moving the sheet themselves, the model stays out
// of the way for this long. Measured from when scrolling *stops*, not when
// it starts, so a long scroll isn't interrupted halfway. The backend holds
// its reported position for a comparable window; this is the same promise
// kept on the client.
const MANUAL_HOLD_MS = 4000
// A scroll counts as finished once this long passes with no scroll events.
const SCROLL_SETTLE_MS = 400
// How far the tracked line must be from where the sheet already sits before
// it is worth moving, as a fraction of the viewport. Generous on purpose:
// the sheet should move when the reader reaches a new line, not whenever
// the estimate twitches.
const FOLLOW_DEADZONE = 0.55
// Lines past the bottom of the screen the model is allowed to consider. The
// reader is almost certainly on a line they can see; what the model is
// really for is noticing the move to the next one.
const LOOKAHEAD_LINES = 1
// The line being played sits in the middle of the screen, which leaves the
// line before and after it in view either side.
const FOLLOW_ANCHOR = 0.5
// Tapping "Previous" this many times while already at the top reveals the
// hidden Download action -- a deliberate, undiscoverable-by-accident
// gesture, not a normal navigation affordance.
const DOWNLOAD_UNLOCK_TAPS = 7

const INFO_CONTENT = (
  <>
    <p>
      The whole sheet is one continuous scroll. Press Record and play -- the sheet follows where it hears you.
    </p>
    <ul>
      <li>Scroll by hand any time; the model won't fight you for a few seconds after you do.</li>
      <li>Previous / Next move by one page.</li>
      <li>Nothing is recorded or uploaded -- audio is analysed as it arrives and discarded.</li>
    </ul>
  </>
)

function slugify(title: string): string {
  return title.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'score'
}

export function ScoreViewer() {
  const { scoreId } = useParams<{ scoreId: string }>()
  const [score, setScore] = useState<ScoreRecord | null>(null)
  const [pageUrls, setPageUrls] = useState<string[]>([])
  const [listening, setListening] = useState(false)
  const [downloadUnlocked, setDownloadUnlocked] = useState(false)
  const [currentLine, setCurrentLine] = useState<ScoreLine | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<(HTMLDivElement | null)[]>([])
  const holdUntilRef = useRef(0)
  const scrollSettleRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const programmaticUntilRef = useRef(0)
  const prevTapsAtTopRef = useRef(0)

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

  // Where on the continuous sheet a line sits: its page's offset plus how
  // far down that page the line is, centred in the viewport.
  const offsetForLine = useCallback(
    (line: ScoreLine): number | null => {
      const container = scrollRef.current
      if (!container || !score) return null
      const pageIndex = pageArrayIndexByPageNumber.get(line.pageIndex)
      if (pageIndex === undefined) return null
      const element = pageRefs.current[pageIndex]
      const page = score.pages[pageIndex]
      if (!element || !page) return null
      const middleOfLine = ((line.top + line.bottom) / 2 / page.height) * element.offsetHeight
      return element.offsetTop + middleOfLine - container.clientHeight * FOLLOW_ANCHOR
    },
    [pageArrayIndexByPageNumber, score],
  )

  const handlePosition = useCallback(
    (event: ScorePositionEvent) => {
      const line = lineForOnset(lines, event.onsetIndex)
      if (!line) return
      setCurrentLine((previous) => (previous?.firstOnset === line.firstOnset ? previous : line))

      const container = scrollRef.current
      if (!container || Date.now() < holdUntilRef.current) return
      const target = offsetForLine(line)
      if (target === null) return
      // Only move for a real change of place, not for the estimate
      // wobbling by a note or two.
      if (Math.abs(target - container.scrollTop) < container.clientHeight * FOLLOW_DEADZONE) return
      scrollOurselves(container, target)
    },
    [lines, offsetForLine, scrollOurselves],
  )

  const { error: trackingError, start, stop, sendHint, sendViewport } = useAudioTracking({
    onTracking: () => {},
    onPosition: handlePosition,
    scoreNotes: score?.boundingBoxes,
  })

  // Which lines are on screen right now, as a range of onsets. This is the
  // constraint that keeps the model honest: it may only place the reader on
  // music they can actually see, plus a line of lookahead for the moment
  // they move on.
  const reportViewport = useCallback(() => {
    const container = scrollRef.current
    if (!container || !score || !lines.length) return
    const top = container.scrollTop
    const bottom = top + container.clientHeight

    const visible: number[] = []
    lines.forEach((line, index) => {
      const pageIndex = pageArrayIndexByPageNumber.get(line.pageIndex)
      if (pageIndex === undefined) return
      const element = pageRefs.current[pageIndex]
      const page = score.pages[pageIndex]
      if (!element || !page) return
      const lineTop = element.offsetTop + (line.top / page.height) * element.offsetHeight
      const lineBottom = element.offsetTop + (line.bottom / page.height) * element.offsetHeight
      if (lineBottom >= top && lineTop <= bottom) visible.push(index)
    })
    if (!visible.length) return

    const last = Math.min(lines.length - 1, visible[visible.length - 1] + LOOKAHEAD_LINES)
    sendViewport({ firstOnset: lines[visible[0]].firstOnset, lastOnset: lines[last].endOnset - 1 })
  }, [lines, pageArrayIndexByPageNumber, score, sendViewport])

  // Scrolling by hand: keep pushing the hold out while it continues, and
  // only once it has been still for a moment tell the model what is now on
  // screen and let it start looking for the next line again.
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
      reportViewport()
    }, SCROLL_SETTLE_MS)
  }, [reportViewport])

  // Tracking starts when the reader asks for it, not on arrival: opening a
  // sheet to read should not switch the microphone on.
  const toggleListening = useCallback(() => {
    if (listening) {
      stop()
      setListening(false)
    } else {
      void start().then(() => {
        // Open with the model already confined to what's on screen, rather
        // than letting it range over the whole score for the first seconds.
        reportViewport()
      })
      setListening(true)
    }
  }, [listening, reportViewport, start, stop])

  useEffect(() => {
    return () => {
      stop()
      if (scrollSettleRef.current) clearTimeout(scrollSettleRef.current)
    }
  }, [stop])

  const currentPage = useCallback((): number => {
    const container = scrollRef.current
    if (!container || !score) return 0
    const middle = container.scrollTop + container.clientHeight * FOLLOW_ANCHOR
    let page = 0
    score.pages.forEach((_, index) => {
      const element = pageRefs.current[index]
      if (element && element.offsetTop <= middle) page = index
    })
    return page
  }, [score])

  const goToPage = useCallback(
    (index: number) => {
      const container = scrollRef.current
      const element = pageRefs.current[index]
      if (!container || !element) return
      holdOff()
      programmaticUntilRef.current = Date.now() + 1000
      container.scrollTo({ top: element.offsetTop, behavior: 'smooth' })
      // Tell the tracker where the reader went, so when the hold lapses it
      // carries on from here instead of yanking back, and re-aim the window
      // at the page they are now looking at.
      const onsetIndex = onsets.findIndex((onset) => {
        const page = pageArrayIndexByPageNumber.get(onset[0].pageIndex)
        return page === index
      })
      if (onsetIndex >= 0) sendHint(onsetIndex, { firm: true })
      setTimeout(reportViewport, SCROLL_SETTLE_MS + 400)
    },
    [holdOff, onsets, pageArrayIndexByPageNumber, reportViewport, sendHint],
  )

  const handlePrevious = useCallback(() => {
    const page = currentPage()
    if (page === 0) {
      prevTapsAtTopRef.current += 1
      if (prevTapsAtTopRef.current >= DOWNLOAD_UNLOCK_TAPS) setDownloadUnlocked(true)
      return
    }
    prevTapsAtTopRef.current = 0
    goToPage(page - 1)
  }, [currentPage, goToPage])

  const handleNext = useCallback(() => {
    if (!score) return
    const page = currentPage()
    if (page >= score.pages.length - 1) return
    prevTapsAtTopRef.current = 0
    goToPage(page + 1)
  }, [currentPage, goToPage, score])

  const handleDownload = useCallback(async () => {
    if (!score) return
    const zip = new JSZip()
    zip.file('notes.json', JSON.stringify(score.boundingBoxes, null, 2))
    score.pages.forEach((page, index) => zip.file(`page-${index + 1}.png`, page.image))
    const blob = await zip.generateAsync({ type: 'blob' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${slugify(score.title)}-sheet-data.zip`
    link.click()
    URL.revokeObjectURL(url)
  }, [score])

  if (!score) return <p>Loading score…</p>

  const actions: PillAction[] = [
    { key: 'prev', label: 'Previous', icon: <ChevronUp size={20} />, onClick: handlePrevious },
    {
      key: 'record',
      label: listening ? 'Stop' : 'Record',
      icon: listening ? <Square size={20} /> : <Mic size={20} />,
      onClick: toggleListening,
      emphasized: !listening,
      danger: listening,
    },
    { key: 'next', label: 'Next', icon: <ChevronDown size={20} />, onClick: handleNext },
  ]
  if (downloadUnlocked) {
    actions.push({
      key: 'download',
      label: 'Download',
      icon: <Download size={20} />,
      onClick: handleDownload,
      emphasized: true,
    })
  }

  return (
    <div className="sheet-page">
      {trackingError && <div className="sheet-tracking-error">{trackingError}</div>}

      <div className="sheet-scroll" ref={scrollRef} onScroll={onScroll} onPointerDown={holdOff}>
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
