import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getScore } from '../../lib/db'
import type { NoteDetectionEvent, ScoreRecord } from '../../lib/types'
import { useAudioTracking } from '../../audio/useAudioTracking'
import { groupBoundingBoxesIntoLines, LineTracker, type ScoreLine } from '../../audio/scoreFollowing'

const MAX_LOG_ENTRIES = 200

interface LogEntry extends NoteDetectionEvent {
  receivedAt: number
}

function lineKey(line: Pick<ScoreLine, 'pageIndex' | 'lineIndex'>): string {
  return `${line.pageIndex}-${line.lineIndex}`
}

export function ScoreViewer() {
  const { scoreId } = useParams<{ scoreId: string }>()
  const [score, setScore] = useState<ScoreRecord | null>(null)
  const [pageUrls, setPageUrls] = useState<string[]>([])
  const [activeNotes, setActiveNotes] = useState<string[]>([])
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [activeLineKey, setActiveLineKey] = useState<string | null>(null)

  const lineTrackerRef = useRef<LineTracker | null>(null)
  const lineAnchorsRef = useRef(new Map<string, HTMLDivElement>())

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

  const handleNoteDetection = useCallback((event: NoteDetectionEvent) => {
    setActiveNotes(event.notes)
    setLogs((prev) => [{ ...event, receivedAt: performance.now() }, ...prev].slice(0, MAX_LOG_ENTRIES))

    const newLine = lineTrackerRef.current?.observe(event.notes)
    if (newLine) {
      const key = lineKey(newLine)
      setActiveLineKey(key)
      // Keep the newly-detected line comfortably in view -- centered, so it
      // is never pinned at the bottom edge of the viewport as the score
      // scrolls along with playback.
      lineAnchorsRef.current.get(key)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [])

  const { isTracking, error, start, stop } = useAudioTracking({ onNoteDetection: handleNoteDetection })

  const handleStart = useCallback(() => {
    setLogs([])
    setActiveLineKey(null)
    lineTrackerRef.current = new LineTracker(lines)
    start()
  }, [start, lines])

  if (!score) return <p>Loading score…</p>

  return (
    <div className="viewer">
      <header className="viewer-header">
        <h1>{score.title}</h1>
        <button onClick={isTracking ? stop : handleStart}>
          {isTracking ? 'Stop Tracking' : 'Start Tracking'}
        </button>
        {error && <span role="alert">{error}</span>}
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
            <h2>Detection log</h2>
            <ul>
              {logs.map((entry, i) => (
                <li key={i} className={entry.notes.length > 0 ? 'has-notes' : ''}>
                  <span className="log-time">{entry.timestamp.toFixed(3)}s</span>{' '}
                  <span className="log-notes">{entry.notes.length > 0 ? entry.notes.join(', ') : 'silence'}</span>{' '}
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
