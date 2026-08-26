import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getScore } from '../../lib/db'
import type { NoteDetectionEvent, ScoreRecord } from '../../lib/types'
import { useAudioTracking } from '../../audio/useAudioTracking'

const MAX_LOG_ENTRIES = 200

interface LogEntry extends NoteDetectionEvent {
  receivedAt: number
}

export function ScoreViewer() {
  const { scoreId } = useParams<{ scoreId: string }>()
  const [score, setScore] = useState<ScoreRecord | null>(null)
  const [pageUrls, setPageUrls] = useState<string[]>([])
  const [activeNotes, setActiveNotes] = useState<string[]>([])
  const [logs, setLogs] = useState<LogEntry[]>([])

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

  const handleNoteDetection = useCallback((event: NoteDetectionEvent) => {
    setActiveNotes(event.notes)
    setLogs((prev) => [{ ...event, receivedAt: performance.now() }, ...prev].slice(0, MAX_LOG_ENTRIES))
  }, [])

  const { isTracking, error, start, stop } = useAudioTracking({ onNoteDetection: handleNoteDetection })

  const handleStart = useCallback(() => {
    setLogs([])
    start()
  }, [start])

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
          {score.pages.map((page, pageIndex) => (
            <div key={page.pageIndex} className="page" style={{ position: 'relative' }}>
              <img src={pageUrls[pageIndex]} alt={`Page ${page.pageIndex + 1}`} width={page.width} height={page.height} />
              <svg
                className="overlay"
                viewBox={`0 0 ${page.width} ${page.height}`}
                style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}
              >
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
            </div>
          ))}
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
