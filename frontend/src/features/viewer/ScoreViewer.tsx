import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getScore, saveScore } from '../../lib/db'
import { reprocessScore } from '../../lib/api'
import type { NoteBoundingBox, ScorePositionEvent, ScoreRecord, TrackingEvent } from '../../lib/types'
import { useAudioTracking } from '../../audio/useAudioTracking'
import { groupBoundingBoxesIntoLines, groupLinesIntoSystems } from '../../audio/scoreFollowing'
import { buildOnsets, nearestOnset } from '../../audio/positionTracking'

const MAX_LOG_ENTRIES = 200

// How long after a scroll stops before it counts as the reader settling
// somewhere, rather than still on their way there.
const SCROLL_SETTLE_MS = 250
// A smooth scroll we started can still be moving well after it began;
// scroll events inside this window are ours, not the reader's.
const AUTO_SCROLL_SETTLE_MS = 1500
// The highlight only moves once the tracker has reported a different
// system on this many consecutive frames -- one stray frame can't twitch
// it to a neighbour.
const SYSTEM_CHANGE_FRAMES = 2
// The active system only re-scrolls into view when it sits outside this
// vertical band of the viewport. Inside it, nothing scrolls.
const COMFORT_TOP = 0.18
const COMFORT_BOTTOM = 0.72

type LogEntry = TrackingEvent & { receivedAt: number }

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
  const [activeSystemKey, setActiveSystemKey] = useState<string | null>(null)
  const [isReprocessing, setIsReprocessing] = useState(false)
  const [reprocessError, setReprocessError] = useState<string | null>(null)

  const systemAnchorsRef = useRef(new Map<string, HTMLDivElement>())
  const autoScrollUntilRef = useRef(0)
  const activeSystemKeyRef = useRef<string | null>(null)
  const pendingSystemRef = useRef<{ key: string; count: number } | null>(null)

  useEffect(() => {
    activeSystemKeyRef.current = activeSystemKey
  }, [activeSystemKey])

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

  const lines = useMemo(
    () => (score ? groupBoundingBoxesIntoLines(score.boundingBoxes, pageHeights) : []),
    [score, pageHeights],
  )

  // Grand staves -- a treble line and a bass line highlighted together, so
  // the highlight covers the whole system and doesn't flicker between the
  // two clefs as consecutive onsets alternate between them.
  const systems = useMemo(() => groupLinesIntoSystems(lines, pageHeights), [lines, pageHeights])

  const systemKeyByLineKey = useMemo(() => {
    const map = new Map<string, string>()
    for (const system of systems) {
      const key = `${system.pageIndex}-${system.systemIndex}`
      for (const lineIndex of system.lineIndexes) map.set(`${system.pageIndex}-${lineIndex}`, key)
    }
    return map
  }, [systems])

  // The same onset grouping the backend tracker indexes into, so its
  // reported onsetIndex can be mapped back to a system.
  const onsets = useMemo(() => (score ? buildOnsets(score.boundingBoxes) : []), [score])

  // onset index -> the key of the system it belongs to.
  const onsetSystemKey = useMemo(() => {
    const boxLineKey = new Map<NoteBoundingBox, string>()
    for (const line of lines) {
      for (const box of line.boxes) boxLineKey.set(box, `${line.pageIndex}-${line.lineIndex}`)
    }
    return onsets.map((onset) => {
      const lineKey = boxLineKey.get(onset[0])
      return lineKey ? systemKeyByLineKey.get(lineKey) : undefined
    })
  }, [onsets, lines, systemKeyByLineKey])

  const systemFirstOnset = useMemo(() => {
    const map = new Map<string, number>()
    onsetSystemKey.forEach((key, index) => {
      if (key !== undefined && !map.has(key)) map.set(key, index)
    })
    return map
  }, [onsetSystemKey])

  const scrollSystemIntoView = useCallback((key: string) => {
    const anchor = systemAnchorsRef.current.get(key)
    if (!anchor) return
    const fraction = anchor.getBoundingClientRect().top / window.innerHeight
    if (fraction >= COMFORT_TOP && fraction <= COMFORT_BOTTOM) return
    autoScrollUntilRef.current = performance.now() + AUTO_SCROLL_SETTLE_MS
    anchor.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  const goToSystem = useCallback(
    (key: string) => {
      if (key === activeSystemKeyRef.current) return
      activeSystemKeyRef.current = key
      setActiveSystemKey(key)
      scrollSystemIntoView(key)
    },
    [scrollSystemIntoView],
  )

  const handleTracking = useCallback((event: TrackingEvent) => {
    setActiveNotes(event.notes)
    if (event.notes.length > 0) {
      setLogs((prev) => [{ ...event, receivedAt: performance.now() }, ...prev].slice(0, MAX_LOG_ENTRIES))
    }
  }, [])

  const handlePosition = useCallback(
    (event: ScorePositionEvent) => {
      const key = onsetSystemKey[event.onsetIndex]
      if (!key || key === activeSystemKeyRef.current) {
        pendingSystemRef.current = null
        return
      }
      // Require the new system to hold for a couple of frames before moving.
      const pending = pendingSystemRef.current
      if (pending && pending.key === key) {
        pending.count += 1
        if (pending.count >= SYSTEM_CHANGE_FRAMES) {
          pendingSystemRef.current = null
          goToSystem(key)
        }
      } else {
        pendingSystemRef.current = { key, count: 1 }
      }
    },
    [onsetSystemKey, goToSystem],
  )

  const { isTracking, error, start, stop, sendHint } = useAudioTracking({
    onTracking: handleTracking,
    onPosition: handlePosition,
    scoreNotes: score?.boundingBoxes,
  })

  // A manual scroll says the reader is looking elsewhere. Nudge the tracker
  // toward whichever system is nearest the middle of the viewport.
  const handleManualScroll = useCallback(() => {
    if (!isTracking || performance.now() < autoScrollUntilRef.current) return

    const viewportMiddle = window.innerHeight / 2
    let nearest: { key: string; distance: number } | null = null
    for (const [key, element] of systemAnchorsRef.current) {
      const distance = Math.abs(element.getBoundingClientRect().top - viewportMiddle)
      if (!nearest || distance < nearest.distance) nearest = { key, distance }
    }
    if (!nearest || nearest.key === activeSystemKeyRef.current) return

    const onsetIndex = systemFirstOnset.get(nearest.key)
    if (onsetIndex !== undefined) sendHint(onsetIndex)
    activeSystemKeyRef.current = nearest.key
    setActiveSystemKey(nearest.key)
  }, [isTracking, systemFirstOnset, sendHint])

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
      pendingSystemRef.current = null
      const key = onsetSystemKey[onsetIndex]
      if (key) {
        activeSystemKeyRef.current = key
        setActiveSystemKey(key)
      }
    },
    [isTracking, score, onsets, onsetSystemKey, sendHint],
  )

  const handleStart = useCallback(() => {
    setLogs([])
    pendingSystemRef.current = null
    // Starting tracking means "I am at the top of the score" -- show that
    // straight away; the backend seeds its belief there too.
    const first = systems[0] ? `${systems[0].pageIndex}-${systems[0].systemIndex}` : null
    activeSystemKeyRef.current = first
    setActiveSystemKey(first)
    window.scrollTo({ top: 0, behavior: 'smooth' })
    start()
  }, [start, systems])

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
            const pageSystems = systems.filter((system) => system.pageIndex === page.pageIndex)
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
                  {pageSystems
                    .filter((system) => `${system.pageIndex}-${system.systemIndex}` === activeSystemKey)
                    .map((system) => (
                      <rect
                        key={system.systemIndex}
                        x={0}
                        y={system.top}
                        width={page.width}
                        height={system.bottom - system.top}
                        fill="rgba(90, 140, 255, 0.16)"
                        stroke="rgba(90, 140, 255, 0.55)"
                        strokeWidth={2}
                      />
                    ))}
                </svg>
                {pageSystems.map((system) => {
                  const key = `${system.pageIndex}-${system.systemIndex}`
                  const centerFraction = (system.top + system.bottom) / 2 / page.height
                  return (
                    <div
                      key={key}
                      ref={(el) => {
                        if (el) systemAnchorsRef.current.set(key, el)
                        else systemAnchorsRef.current.delete(key)
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
