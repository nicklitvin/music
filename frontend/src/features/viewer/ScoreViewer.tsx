import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import JSZip from 'jszip'
import { getScore } from '../../lib/db'
import type { ScorePositionEvent, ScoreRecord } from '../../lib/types'
import { useAudioTracking } from '../../audio/useAudioTracking'
import { buildOnsets } from '../../audio/positionTracking'
import { BottomNav, type PillAction } from '../../components/BottomNav'

// The tracked page only changes once the backend has reported a different
// page for this many consecutive frames -- a single ambiguous frame can't
// flip the page back and forth.
const PAGE_CHANGE_FRAMES = 2
// Vertical drag distance (px) needed to commit a page turn, TikTok-style.
const SWIPE_THRESHOLD_PX = 80
// Tapping "Previous" this many times while already on the first page
// reveals the hidden Download action -- a deliberate, undiscoverable-by-
// accident gesture, not a normal navigation affordance.
const DOWNLOAD_UNLOCK_TAPS = 7

const INFO_CONTENT = (
  <>
    <p>Play along -- the page turns itself as it hears where you are.</p>
    <ul>
      <li>Drag the sheet up or down to turn pages by hand, like flipping through a feed.</li>
      <li>Previous / Next below do the same thing.</li>
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
  const [pageIndex, setPageIndex] = useState(0)
  const [dragPixels, setDragPixels] = useState(0)
  const [isDragging, setIsDragging] = useState(false)
  const [downloadUnlocked, setDownloadUnlocked] = useState(false)

  const pointerRef = useRef<{ id: number; startY: number } | null>(null)
  const pendingPageRef = useRef<{ index: number; count: number } | null>(null)
  const prevTapsAtFirstPageRef = useRef(0)

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
  // reported onsetIndex can be mapped back to which page it's on.
  const onsets = useMemo(() => (score ? buildOnsets(score.boundingBoxes) : []), [score])

  const pageArrayIndexByPageNumber = useMemo(() => {
    const map = new Map<number, number>()
    score?.pages.forEach((page, index) => map.set(page.pageIndex, index))
    return map
  }, [score])

  const onsetPageIndex = useMemo(
    () => onsets.map((onset) => pageArrayIndexByPageNumber.get(onset[0].pageIndex) ?? 0),
    [onsets, pageArrayIndexByPageNumber],
  )

  const handlePosition = useCallback(
    (event: ScorePositionEvent) => {
      const target = onsetPageIndex[event.onsetIndex]
      if (target === undefined) return
      setPageIndex((current) => {
        if (target === current) {
          pendingPageRef.current = null
          return current
        }
        const pending = pendingPageRef.current
        if (pending && pending.index === target) {
          pending.count += 1
          if (pending.count >= PAGE_CHANGE_FRAMES) {
            pendingPageRef.current = null
            return target
          }
        } else {
          pendingPageRef.current = { index: target, count: 1 }
        }
        return current
      })
    },
    [onsetPageIndex],
  )

  const { error: trackingError, start, stop, sendHint } = useAudioTracking({
    onTracking: () => {},
    onPosition: handlePosition,
    scoreNotes: score?.boundingBoxes,
  })

  // Listening is the whole point of this page -- start as soon as there is
  // a score to follow, stop when leaving.
  useEffect(() => {
    if (!score) return
    start()
    return () => stop()
    // start/stop change identity every render (they close over callbacks
    // above); this should still only fire once per score load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [score])

  const hintPage = useCallback(
    (index: number) => {
      const onsetIndex = onsetPageIndex.findIndex((p) => p === index)
      if (onsetIndex >= 0) sendHint(onsetIndex, { firm: true })
    },
    [onsetPageIndex, sendHint],
  )

  const goToPage = useCallback(
    (index: number) => {
      pendingPageRef.current = null
      setPageIndex(index)
      hintPage(index)
    },
    [hintPage],
  )

  const handlePrevious = useCallback(
    (fromSwipe: boolean) => {
      if (pageIndex === 0) {
        if (!fromSwipe) {
          prevTapsAtFirstPageRef.current += 1
          if (prevTapsAtFirstPageRef.current >= DOWNLOAD_UNLOCK_TAPS) setDownloadUnlocked(true)
        }
        return
      }
      prevTapsAtFirstPageRef.current = 0
      goToPage(pageIndex - 1)
    },
    [pageIndex, goToPage],
  )

  const handleNext = useCallback(
    (_fromSwipe: boolean) => {
      if (!score || pageIndex >= score.pages.length - 1) return
      prevTapsAtFirstPageRef.current = 0
      goToPage(pageIndex + 1)
    },
    [score, pageIndex, goToPage],
  )

  const onPointerDown = (event: React.PointerEvent) => {
    pointerRef.current = { id: event.pointerId, startY: event.clientY }
    event.currentTarget.setPointerCapture(event.pointerId)
    setIsDragging(true)
  }

  const onPointerMove = (event: React.PointerEvent) => {
    if (!pointerRef.current || pointerRef.current.id !== event.pointerId || !score) return
    let delta = event.clientY - pointerRef.current.startY
    // Rubber-band resistance at the ends of the score.
    if (pageIndex === 0 && delta > 0) delta *= 0.35
    if (pageIndex === score.pages.length - 1 && delta < 0) delta *= 0.35
    setDragPixels(delta)
  }

  const onPointerUp = (event: React.PointerEvent) => {
    if (!pointerRef.current || pointerRef.current.id !== event.pointerId) return
    pointerRef.current = null
    setIsDragging(false)
    if (dragPixels <= -SWIPE_THRESHOLD_PX) handleNext(true)
    else if (dragPixels >= SWIPE_THRESHOLD_PX) handlePrevious(true)
    setDragPixels(0)
  }

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
    { key: 'prev', label: 'Previous', icon: '◀', onClick: () => handlePrevious(false), disabled: pageIndex === 0 },
    {
      key: 'next',
      label: 'Next',
      icon: '▶',
      onClick: () => handleNext(false),
      disabled: pageIndex === score.pages.length - 1,
    },
  ]
  if (downloadUnlocked) {
    actions.push({ key: 'download', label: 'Download', icon: '⬇️', onClick: handleDownload, emphasized: true })
  }

  return (
    <div className="sheet-page">
      {trackingError && <div className="sheet-tracking-error">{trackingError}</div>}

      <div
        className="sheet-viewport"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <div
          className="sheet-track"
          style={{
            height: `${score.pages.length * 100}dvh`,
            transform: `translateY(calc(${-pageIndex * 100}dvh + ${dragPixels}px))`,
            transition: isDragging ? 'none' : 'transform 320ms cubic-bezier(0.22, 1, 0.36, 1)',
          }}
        >
          {score.pages.map((page, index) => (
            <div className="sheet-slide" key={page.pageIndex}>
              <img
                className="sheet-slide-image"
                src={pageUrls[index]}
                width={page.width}
                height={page.height}
                alt={`Page ${page.pageIndex + 1}`}
                draggable={false}
              />
            </div>
          ))}
        </div>
      </div>

      <BottomNav infoTitle={score.title} infoContent={INFO_CONTENT} actions={actions} />
    </div>
  )
}
