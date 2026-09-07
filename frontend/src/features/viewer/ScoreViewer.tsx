import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getScore, saveScore } from '../../lib/db'
import { reprocessScore } from '../../lib/api'
import type { NoteBoundingBox, NoteDetectionEvent, ScoreRecord } from '../../lib/types'
import { useAudioTracking } from '../../audio/useAudioTracking'
import { groupBoundingBoxesIntoLines, LineTracker, type ScoreLine } from '../../audio/scoreFollowing'
import { MelodyTracker } from '../../audio/melodyTracking'

type TrackingMode = 'melody' | 'line'

const MAX_LOG_ENTRIES = 200

// How long after a scroll stops before it counts as the reader settling
// somewhere, rather than still on their way there.
const SCROLL_SETTLE_MS = 250
// How long our own smooth auto-scroll is expected to still be moving.
// Scroll events inside this window are ours, not the reader's.
const AUTO_SCROLL_SETTLE_MS = 800

interface LogEntry extends NoteDetectionEvent {
  receivedAt: number
}

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
  const [trackingMode, setTrackingMode] = useState<TrackingMode>('melody')

  const lineTrackerRef = useRef<LineTracker | null>(null)
  const melodyTrackerRef = useRef<MelodyTracker | null>(null)
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

  const lines = useMemo(() => {
    if (!score) return []
    const pageHeights = Object.fromEntries(score.pages.map((page) => [page.pageIndex, page.height]))
    return groupBoundingBoxesIntoLines(score.boundingBoxes, pageHeights)
  }, [score])

  // Lets the melody tracker's single-note results reuse the line-based
  // highlight/scroll anchors, by finding which line a given note belongs to.
  const boxLineKey = useMemo(() => {
    const map = new Map<NoteBoundingBox, string>()
    for (const line of lines) {
      for (const box of line.boxes) map.set(box, lineKey(line))
    }
    return map
  }, [lines])

  const goToLine = useCallback((key: string) => {
    setActiveLineKey(key)
    // Our own scroll, not the reader's -- mark it so the scroll listener
    // does not read it back as a manual correction and hint the tracker
    // toward the position the tracker itself just chose.
    autoScrollUntilRef.current = performance.now() + AUTO_SCROLL_SETTLE_MS
    // Keep the newly-detected line comfortably in view -- centered, so it
    // is never pinned at the bottom edge of the viewport as the score
    // scrolls along with playback.
    lineAnchorsRef.current.get(key)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])


  const handleNoteDetection = useCallback(
    (event: NoteDetectionEvent) => {
      setActiveNotes(event.notes)
      if (event.notes.length > 0) {
        setLogs((prev) => [{ ...event, receivedAt: performance.now() }, ...prev].slice(0, MAX_LOG_ENTRIES))
      }

      if (trackingMode === 'line') {
        const newLine = lineTrackerRef.current?.observe(event.notes)
        if (newLine) goToLine(lineKey(newLine))
      } else {
        const newNote = melodyTrackerRef.current?.observe(event.notes)
        const key = newNote && boxLineKey.get(newNote)
        if (key) goToLine(key)
      }
    },
    [trackingMode, boxLineKey, goToLine],
  )

  const { isTracking, error, start, stop } = useAudioTracking({ onNoteDetection: handleNoteDetection })

  // A manual scroll says the reader is looking somewhere else, and is
  // usually a correction -- they scrolled because the highlight was wrong.
  // Find whichever line is nearest the middle of the viewport and move the
  // active tracker there.
  const handleManualScroll = useCallback(() => {
    if (performance.now() < autoScrollUntilRef.current) return

    const viewportMiddle = window.innerHeight / 2
    let nearest: { key: string; distance: number } | null = null
    for (const [key, element] of lineAnchorsRef.current) {
      const distance = Math.abs(element.getBoundingClientRect().top - viewportMiddle)
      if (!nearest || distance < nearest.distance) nearest = { key, distance }
    }
    if (!nearest) return

    const target = nearest.key
    const line = lines.find((candidate) => lineKey(candidate) === target)
    if (!line) return

    lineTrackerRef.current?.hintPosition(line)
    if (line.boxes.length > 0) melodyTrackerRef.current?.hintPosition(line.boxes[0])
    setActiveLineKey(target)
  }, [lines])

  useEffect(() => {
    if (!isTracking) return
    let timer: number | undefined
    const onScroll = () => {
      // Wait for scrolling to stop: hinting on every intermediate event of
      // a long scroll would drag the tracker across everything passed on
      // the way to where the reader was actually heading.
      window.clearTimeout(timer)
      timer = window.setTimeout(handleManualScroll, SCROLL_SETTLE_MS)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.clearTimeout(timer)
    }
  }, [isTracking, handleManualScroll])

  const handleStart = useCallback(() => {
    setLogs([])
    setActiveLineKey(null)
    lineTrackerRef.current = trackingMode === 'line' ? new LineTracker(lines) : null
    melodyTrackerRef.current = trackingMode === 'melody' ? new MelodyTracker(score?.boundingBoxes ?? []) : null
    start()
  }, [start, lines, score, trackingMode])

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
            onClick={handleReprocess}
            disabled={isReprocessing || !score.sourcePdf}
            title={score.sourcePdf ? 'Re-run note parsing on the original PDF' : 'No stored PDF to reprocess (uploaded before this feature existed)'}
          >
            {isReprocessing ? 'Reprocessing…' : 'Reprocess score'}
          </button>
          <select
            value={trackingMode}
            onChange={(e) => setTrackingMode(e.target.value as TrackingMode)}
            disabled={isTracking}
            title="How position on the sheet is tracked from the detected audio"
          >
            <option value="melody">Melody (highest note)</option>
            <option value="line">Line matching (chords)</option>
          </select>
          <button onClick={isTracking ? stop : handleStart}>
            {isTracking ? 'Stop Tracking' : 'Start Tracking'}
          </button>
        </div>
        {error && <span role="alert">{error}</span>}
        {reprocessError && <span role="alert">{reprocessError}</span>}
      </header>

      <div className="active-notes">Detected: {activeNotes.join(', ') || '—'}</div>

      <div className="viewer-body">
        <div className="pages">
          {score.pages.map((page, pageIndex) => {
            const pageLines = lines.filter((line) => line.pageIndex === page.pageIndex)
            return (
              <div key={page.pageIndex} className="page" style={{ position: 'relative' }}>
                <img src={pageUrls[pageIndex]} alt={`Page ${page.pageIndex + 1}`} width={page.width} height={page.height} />
                <svg
                  className="overlay"
                  viewBox={`0 0 ${page.width} ${page.height}`}
                  style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}
                >
                  {pageLines
                    .filter((line) => lineKey(line) === activeLineKey)
                    .map((line) => {
                      const minY = Math.min(...line.boxes.map((b) => b.y))
                      const maxY = Math.max(...line.boxes.map((b) => b.y + b.height))
                      const padding = (maxY - minY) * 0.5 || 8
                      return (
                        <rect
                          key={lineKey(line)}
                          x={0}
                          y={minY - padding}
                          width={page.width}
                          height={maxY - minY + padding * 2}
                          fill="rgba(90, 140, 255, 0.12)"
                        />
                      )
                    })}
                  {score.boundingBoxes
                    .filter((box) => box.pageIndex === page.pageIndex)
                    .map((box, i) => (
                      <rect
                        key={i}
                        x={box.x}
                        y={box.y}
                        width={box.width}
                        height={box.height}
                        fill={activeNotes.includes(box.pitch) ? 'rgba(76, 217, 100, 0.5)' : 'transparent'}
                        stroke={activeNotes.includes(box.pitch) ? 'seagreen' : 'none'}
                      />
                    ))}
                </svg>
                {pageLines.map((line) => (
                  <div
                    key={lineKey(line)}
                    ref={(el) => {
                      if (el) lineAnchorsRef.current.set(lineKey(line), el)
                      else lineAnchorsRef.current.delete(lineKey(line))
                    }}
                    style={{ position: 'absolute', left: 0, top: `${(line.y / page.height) * 100}%`, width: 1, height: 1 }}
                  />
                ))}
              </div>
            )
          })}
        </div>

        {(isTracking || logs.length > 0) && (
          <aside className="tracking-log" aria-label="Backend detection log">
            <div className="tracking-log-header">
              <h2>Detection log</h2>
              <div className="tracking-log-actions">
                <button onClick={handleDownloadLogs} disabled={logs.length === 0}>
                  Download log
                </button>
                <button onClick={handleDownloadNotes}>Download notes</button>
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
