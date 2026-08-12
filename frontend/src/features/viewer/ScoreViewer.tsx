import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getScore } from '../../lib/db'
import type { NoteDetectionEvent, ScoreRecord } from '../../lib/types'
import { useAudioTracking } from '../../audio/useAudioTracking'

export function ScoreViewer() {
  const { scoreId } = useParams<{ scoreId: string }>()
  const [score, setScore] = useState<ScoreRecord | null>(null)
  const [pageUrls, setPageUrls] = useState<string[]>([])
  const [activeNotes, setActiveNotes] = useState<string[]>([])

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
  }, [])

  const { isTracking, error, start, stop } = useAudioTracking({ onNoteDetection: handleNoteDetection })

  if (!score) return <p>Loading score…</p>

  return (
    <div className="viewer">
      <header className="viewer-header">
        <h1>{score.title}</h1>
        <button onClick={isTracking ? stop : start}>
          {isTracking ? 'Stop Tracking' : 'Start Tracking'}
        </button>
        {error && <span role="alert">{error}</span>}
      </header>

      <div className="active-notes">Detected: {activeNotes.join(', ') || '—'}</div>

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
    </div>
  )
}
