import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileUp, GripVertical, Trash2, Upload, X } from 'lucide-react'
import { BottomNav } from '../../components/BottomNav'
import { ConfirmModal } from '../../components/ConfirmModal'
import { Modal } from '../../components/Modal'
import { VersionChip } from '../../components/VersionChip'
import { deleteScore, listScores, reorderScores } from '../../lib/db'
import { useDragReorder } from '../../lib/useDragReorder'
import { SECONDS_PER_PAGE, useUploadQueue, type PendingUpload } from '../../lib/uploadQueue'
import type { ScoreRecord } from '../../lib/types'

const INFO_CONTENT = (
  <>
    <p>This is your library. Every sheet you upload lives only on this device, in the browser's storage.</p>
    <ul>
      <li>Tap a sheet to open and follow along while you play.</li>
      <li>Drag the handle on the left of an item to reorder your library.</li>
      <li>Use the delete button on the right to remove a sheet.</li>
      <li>Tap Upload below to add a new PDF.</li>
    </ul>
  </>
)

function formatDuration(seconds: number): string {
  if (seconds < 90) return 'less than a minute'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `about ${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `about ${hours}h ${rest}min` : `about ${hours}h`
}

// Note recognition is minutes per page, so "Processing..." on its own leaves
// someone staring at a spinner for an hour with no idea whether it's stuck.
function processingLabel(upload: PendingUpload, now: number): string {
  const elapsed = (now - upload.startedAt) / 1000
  if (!upload.pageCount) return 'Processing — about 5 min per page'

  const total = upload.pageCount * SECONDS_PER_PAGE
  const page = Math.min(upload.pageCount, Math.floor(elapsed / SECONDS_PER_PAGE) + 1)
  const remaining = total - elapsed
  const progress = `Page ~${page} of ${upload.pageCount}`
  // Past the estimate but still going: don't keep promising a time.
  return remaining <= 30 ? `${progress} — finishing up` : `${progress} — ${formatDuration(remaining)} left`
}

// Ticks while an upload is in flight so the estimate counts down on its own.
function useTicker(active: boolean, intervalMs = 10000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [active, intervalMs])
  return now
}

function useThumbnails(scores: ScoreRecord[]): Map<string, string> {
  const [urls, setUrls] = useState<Map<string, string>>(new Map())

  useEffect(() => {
    const next = new Map<string, string>()
    for (const score of scores) {
      const page = score.pages[0]
      if (!page) continue
      try {
        next.set(score.id, URL.createObjectURL(page.image))
      } catch {
        // Leave this score without a thumbnail rather than crash the list.
      }
    }
    setUrls(next)
    return () => {
      next.forEach((url) => URL.revokeObjectURL(url))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scores.map((s) => s.id).join(',')])

  return urls
}

export function Scores() {
  const navigate = useNavigate()
  const { pending, startUpload, dismissError } = useUploadQueue()
  const [scores, setScores] = useState<ScoreRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [deleteTarget, setDeleteTarget] = useState<ScoreRecord | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const thumbnails = useThumbnails(scores)
  const now = useTicker(pending.some((upload) => !upload.error))

  useEffect(() => {
    listScores()
      .then(setScores)
      .finally(() => setLoading(false))
    // Re-fetch whenever the upload queue's length changes -- an upload
    // just started (nothing to refetch yet, harmless) or just finished
    // (the new score is now in Dexie and needs to appear).
  }, [pending.length])

  const commitOrder = useCallback((orderedIds: string[]) => {
    void reorderScores(orderedIds)
  }, [])
  const { registerItemRef, onHandlePointerDown } = useDragReorder<ScoreRecord>(setScores, commitOrder)

  async function handleConfirmDelete() {
    if (!deleteTarget) return
    await deleteScore(deleteTarget.id)
    setScores((prev) => prev.filter((s) => s.id !== deleteTarget.id))
    setDeleteTarget(null)
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    // Processing (OMR takes minutes per page) happens in the background via
    // the upload queue, so the sheet closes immediately -- the new item
    // shows up at the top of the list with its own progress estimate.
    startUpload(file)
    setUploadOpen(false)
    e.target.value = ''
  }

  const isEmpty = !loading && scores.length === 0 && pending.length === 0

  return (
    <div className="scores-page">
      {isEmpty && <p className="scores-empty">No scores yet. Tap Upload below to add your first sheet.</p>}

      <ul className="scores-list">
        {pending.map((upload) => (
          <li key={upload.id} className="score-item score-item-pending">
            <div className="score-drag-handle score-drag-handle-placeholder" aria-hidden="true" />
            <div className="score-thumb score-thumb-loading">
              {!upload.error && <span className="spinner" aria-hidden="true" />}
            </div>
            <div className="score-info">
              <strong>{upload.fileName}</strong>
              <span className={upload.error ? 'score-item-error' : ''}>
                {upload.error ?? processingLabel(upload, now)}
              </span>
            </div>
            {upload.error && (
              <button className="score-delete" aria-label="Dismiss" onClick={() => dismissError(upload.id)}>
                <X size={20} />
              </button>
            )}
          </li>
        ))}

        {scores.map((score) => (
          <li key={score.id} ref={(el) => registerItemRef(score.id, el)} className="score-item">
            <button
              className="score-drag-handle"
              aria-label="Reorder"
              onPointerDown={onHandlePointerDown(score.id)}
            >
              <GripVertical size={20} />
            </button>
            <button className="score-open" onClick={() => navigate(`/scores/${score.id}`)}>
              {thumbnails.get(score.id) ? (
                <img className="score-thumb" src={thumbnails.get(score.id)} alt="" />
              ) : (
                <div className="score-thumb" />
              )}
              <span className="score-info">
                <strong>{score.title}</strong>
                <span>
                  {score.pages.length} page{score.pages.length === 1 ? '' : 's'}
                </span>
              </span>
            </button>
            <button
              className="score-delete score-delete-danger"
              aria-label={`Delete ${score.title}`}
              onClick={() => setDeleteTarget(score)}
            >
              <Trash2 size={20} />
            </button>
          </li>
        ))}
      </ul>

      {deleteTarget && (
        <ConfirmModal
          title="Delete score?"
          message={`"${deleteTarget.title}" will be removed from this device. This can't be undone.`}
          onConfirm={handleConfirmDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {uploadOpen && (
        <Modal title="Upload sheet music" onClose={() => setUploadOpen(false)}>
          <p>
            Choose a PDF of piano sheet music. It's parsed into notes and page images stored only on this
            device.
          </p>
          <p className="subtle-text">
            Recognition takes about 5 minutes per page, so a 10-page sheet is roughly an hour. You'll see an
            estimate in your library while it works, and you can carry on using the app.
          </p>
          <button className="btn btn-primary upload-sheet-btn" onClick={() => fileInputRef.current?.click()}>
            <FileUp size={18} /> Choose a PDF
          </button>
        </Modal>
      )}

      <input
        ref={fileInputRef}
        className="visually-hidden"
        type="file"
        accept="application/pdf"
        onChange={handleFileChange}
      />

      <VersionChip />

      <BottomNav
        infoTitle="Your scores"
        infoContent={INFO_CONTENT}
        actions={[
          { key: 'upload', label: 'Upload', icon: <Upload size={20} />, onClick: () => setUploadOpen(true) },
        ]}
      />
    </div>
  )
}
