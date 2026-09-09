import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getScore, saveScore } from '../../lib/db'
import { reprocessScore } from '../../lib/api'
import type { NoteBoundingBox, ScorePositionEvent, ScoreRecord, TrackingEvent } from '../../lib/types'
import { useAudioTracking } from '../../audio/useAudioTracking'
import { computeLineBands, groupBoundingBoxesIntoLines, type ScoreLine } from '../../audio/scoreFollowing'
import { buildOnsets, nearestOnset } from '../../audio/positionTracking'

const MAX_LOG_ENTRIES = 200

// How long after a scroll stops before it counts as the reader settling
// somewhere, rather than still on their way there.
const SCROLL_SETTLE_MS = 250
// How long our own smooth auto-scroll is expected to still be moving.
// Scroll events inside this window are ours, not the reader's.
const AUTO_SCROLL_SETTLE_MS = 800
// The active line only re-scrolls into view when it drifts outside this
// vertical band of the viewport. Inside it, the highlight moves but the
// page stays put -- what made following feel jumpy was re-centering on
// every small step.
const COMFORT_TOP = 0.2
const COMFORT_BOTTOM = 0.75

type LogEntry = TrackingEvent & { receivedAt: number }

function lineKey(line: Pick<ScoreLine, 'pageIndex' | 'lineIndex'>): string {
  return `${line.pageIndex}-${line.lineIndex}`
}

function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function slugify(title: string): string {
  return title.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'score'
}

export function ScoreViewer() {
  const { scoreId } = useParams<{ scoreId: string }>()
  const [score, setScore] = useState<ScoreRecord | null>(null)
  const [pageUrls, setPageUrls] = useState<string[]>([])
  const [activeNotes, setActiveNotes] = useState<string[]>([])
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [activeLineKey, setActiveLineKey] = useState<string | null>(null)
  const [isReprocessing, setIsReprocessing] = useState(false)
  const [reprocessError, setReprocessError] = useState<string | null>(null)

  const lineAnchorsRef = useRef(new Map<string, HTMLDivElement>())
  const autoScrollUntilRef = useRef(0)

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

  const pageHeights = useMemo(
    () => Object.fromEntries((score?.pages ?? []).map((page) => [page.pageIndex, page.height])),
    [score],
  )

  const lines = useMemo(() => {
    if (!score) return []
    return groupBoundingBoxesIntoLines(score.boundingBoxes, pageHeights)
  }, [score, pageHeights])

  // The vertical strip to highlight per line -- sized to cover the whole
  // grand staff (treble + bass), not just the row of noteheads that
  // matched.
  const lineBands = useMemo(() => computeLineBands(lines, pageHeights), [lines, pageHeights])

  // The same onset grouping the backend tracker indexes into, so its
  // reported onsetIndex can be mapped back to a line here.
  const onsets = useMemo(() => (score ? buildOnsets(score.boundingBoxes) : []), [score])

  const boxLineKey = useMemo(() => {
    const map = new Map<NoteBoundingBox, string>()
    for (const line of lines) {
      for (const box of line.boxes) map.set(box, lineKey(line))
    }
    return map
  }, [lines])

  // First onset index that falls on each line -- where a scroll or click to
  // that line hints the tracker.
  const lineFirstOnset = useMemo(() => {
    const map = new Map<string, number>()
    onsets.forEach((onset, index) => {
      const key = boxLineKey.get(onset[0])
      if (key !== undefined && !map.has(key)) map.set(key, index)
    })
    return map
  }, [onsets, boxLineKey])

  const scrollLineIntoView = useCallback((key: string) => {
    const anchor = lineAnchorsRef.current.get(key)
    if (!anchor) return
    const { top } = anchor.getBoundingClientRect()
    const fraction = top / window.innerHeight
    if (fraction >= COMFORT_TOP && fraction <= COMFORT_BOTTOM) return // already comfortably visible
    autoScrollUntilRef.current = performance.now() + AUTO_SCROLL_SETTLE_MS
    anchor.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  const goToLine = useCallback(
    (key: string) => {
      setActiveLineKey(key)
      scrollLineIntoView(key)
    },
    [scrollLineIntoView],
  )

  const handleTracking = useCallback((event: TrackingEvent) => {
    setActiveNotes(event.notes)
    if (event.notes.length > 0) {
      setLogs((prev) => [{ ...event, receivedAt: performance.now() }, ...prev].slice(0, MAX_LOG_ENTRIES))
    }
  }, [])

  const handlePosition = useCallback(
    (event: ScorePositionEvent) => {
      const box = onsets[event.onsetIndex]?.[0]
      const key = box && boxLineKey.get(box)
      if (key) goToLine(key)
    },
    [onsets, boxLineKey, goToLine],
  )

  const { isTracking, error, start, stop, sendHint } = useAudioTracking({
    onTracking: handleTracking,
    onPosition: handlePosition,
    scoreNotes: score?.boundingBoxes,
  })

  // A manual scroll says the reader is looking somewhere else, and is
  // usually a correction. Nudge the backend tracker toward whichever line
  // is nearest the middle of the viewport.
  const handleManualScroll = useCallback(() => {
    if (!isTracking || performance.now() < autoScrollUntilRef.current) return

    const viewportMiddle = window.innerHeight / 2
    let nearest: { key: string; distance: number } | null = null
    for (const [key, element] of lineAnchorsRef.current) {
      const distance = Math.abs(element.getBoundingClientRect().top - viewportMiddle)
      if (!nearest || distance < nearest.distance) nearest = { key, distance }
    }
    if (!nearest) return

    const onsetIndex = lineFirstOnset.get(nearest.key)
    if (onsetIndex !== undefined) sendHint(onsetIndex)
    setActiveLineKey(nearest.key)
  }, [isTracking, lineFirstOnset, sendHint])

  useEffect(() => {
    if (!isTracking) return
    let timer: number | undefined
    const onScroll = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(handleManualScroll, SCROLL_SETTLE_MS)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.clearTimeout(timer)
    }
  }, [isTracking, handleManualScroll])

  // Clicking a spot on the sheet is a firm statement: "I am exactly here."
  const handlePageClick = useCallback(
    (pageIndex: number) => (event: React.MouseEvent<HTMLDivElement>) => {
      if (!isTracking) return
      const page = score?.pages.find((p) => p.pageIndex === pageIndex)
      if (!page) return
      const rect = event.currentTarget.getBoundingClientRect()
      const x = ((event.clientX - rect.left) / rect.width) * page.width
      const y = ((event.clientY - rect.top) / rect.height) * page.height

      const onsetIndex = nearestOnset(onsets, pageIndex, x, y)
      if (onsetIndex < 0) return
      sendHint(onsetIndex, { firm: true })
      const key = boxLineKey.get(onsets[onsetIndex][0])
      if (key) setActiveLineKey(key)
    },
    [isTracking, score, onsets, boxLineKey, sendHint],
  )

  const handleStart = useCallback(() => {
    setLogs([])
    // Starting tracking means "I am at the top of the score" -- show that
    // straight away, and let the backend seed its belief there too.
    setActiveLineKey(lines[0] ? lineKey(lines[0]) : null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
    start()
  }, [start, lines])

  const handleDownloadLogs = useCallback(() => {
    downloadJson(`${slugify(score?.title ?? 'score')}-detection-log.json`, logs)
  }, [logs, score])

  const handleDownloadNotes = useCallback(() => {
    downloadJson(`${slugify(score?.title ?? 'score')}-notes.json`, score?.boundingBoxes ?? [])
  }, [score])

  const handleReprocess = useCallback(async () => {
    if (!score) return
    setIsReprocessing(true)
    setReprocessError(null)
    try {
      const updated = await reprocessScore(score)
      await saveScore(updated)
      pageUrls.forEach((url) => URL.revokeObjectURL(url))
      setPageUrls(updated.pages.map((page) => URL.createObjectURL(page.image)))
      setScore(updated)
    } catch (err) {
      setReprocessError(err instanceof Error ? err.message : 'Reprocessing failed')
    } finally {
      setIsReprocessing(false)
    }
  }, [score, pageUrls])

  if (!score) return <p>Loading score…</p>

  return (
    <div className="viewer">
      <header className="viewer-header">
        <h1>{score.title}</h1>
        <div className="viewer-header-actions">
          <button
            className="btn btn-ghost"
            onClick={handleReprocess}
            disabled={isReprocessing || !score.sourcePdf}
            title={score.sourcePdf ? 'Re-run note parsing on the original PDF' : 'No stored PDF to reprocess (uploaded before this feature existed)'}
          >
            {isReprocessing ? 'Reprocessing…' : 'Reprocess score'}
          </button>
          <button
            className={`btn btn-primary${isTracking ? ' is-tracking' : ''}`}
            onClick={isTracking ? stop : handleStart}
          >
            {isTracking ? 'Stop tracking' : 'Start tracking'}
          </button>
        </div>
        {error && <span role="alert" className="viewer-alert">{error}</span>}
        {reprocessError && <span role="alert" className="viewer-alert">{reprocessError}</span>}
      </header>

      <div className="active-notes">
        Detected: {activeNotes.join(', ') || '—'}
        {isTracking && <span className="viewer-hint"> · click the sheet where you are to correct it</span>}
      </div>

      <div className="viewer-body">
        <div className="pages">
          {score.pages.map((page, pageIndex) => {
            const pageLines = lines.filter((line) => line.pageIndex === page.pageIndex)
            return (
              <div
                key={page.pageIndex}
                className={`page${isTracking ? ' page-clickable' : ''}`}
                style={{ position: 'relative' }}
                onClick={handlePageClick(page.pageIndex)}
              >
                <img src={pageUrls[pageIndex]} alt={`Page ${page.pageIndex + 1}`} width={page.width} height={page.height} />
                <svg
                  className="overlay"
                  viewBox={`0 0 ${page.width} ${page.height}`}
                  style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}
                >
                  {pageLines
                    .filter((line) => lineKey(line) === activeLineKey)
                    .map((line) => {
                      const band = lineBands.get(lineKey(line))
                      if (!band) return null
                      return (
                        <rect
                          key={lineKey(line)}
                          x={0}
                          y={band.top}
                          width={page.width}
                          height={band.height}
                          fill="rgba(90, 140, 255, 0.16)"
                          stroke="rgba(90, 140, 255, 0.55)"
                          strokeWidth={2}
                        />
                      )
                    })}
                </svg>
                {pageLines.map((line) => {
                  const band = lineBands.get(lineKey(line))
                  const centerFraction = band
                    ? (band.top + band.height / 2) / page.height
                    : line.y / page.height
                  return (
                    <div
                      key={lineKey(line)}
                      ref={(el) => {
                        if (el) lineAnchorsRef.current.set(lineKey(line), el)
                        else lineAnchorsRef.current.delete(lineKey(line))
                      }}
                      style={{ position: 'absolute', left: 0, top: `${centerFraction * 100}%`, width: 1, height: 1 }}
                    />
                  )
                })}
              </div>
            )
          })}
        </div>

        {(isTracking || logs.length > 0) && (
          <aside className="tracking-log" aria-label="Backend detection log">
            <div className="tracking-log-header">
              <h2>Detection log</h2>
              <div className="tracking-log-actions">
                <button className="btn btn-ghost btn-sm" onClick={handleDownloadLogs} disabled={logs.length === 0}>
                  Download log
                </button>
                <button className="btn btn-ghost btn-sm" onClick={handleDownloadNotes}>Download notes</button>
              </div>
            </div>
            <ul>
              {logs.map((entry, i) => (
                <li key={i}>
                  <span className="log-time">{entry.timestamp.toFixed(3)}s</span>{' '}
                  <span className="log-notes">{entry.notes.join(', ')}</span>{' '}
                  <span className="log-meta">
                    conf={entry.confidence.toFixed(2)} rms={entry.rms.toFixed(0)}
                  </span>
                </li>
              ))}
            </ul>
          </aside>
        )}
      </div>
    </div>
  )
}
