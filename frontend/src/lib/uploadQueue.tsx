import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { discardScoreJob, fetchScoreResult, getScoreJob, submitScore, UploadRejectedError } from './api'
import {
  completePendingUpload,
  db,
  deletePendingUpload,
  listPendingUploads,
  savePendingUpload,
  updatePendingUpload,
} from './db'
import { countPdfPages } from './pdfPages'
import type { PendingUploadRecord } from './types'
import { uuid } from './uuid'

// Measured on the machine this runs on: oemer takes a median 308s per page
// (296-374s over 57 pages), CPU-only. Used to turn "processing..." into an
// actual estimate -- at this rate a 12-page sheet is over an hour, which the
// reader deserves to be told up front rather than discovering.
export const SECONDS_PER_PAGE = 308

// How often to ask the server how a sheet is getting on. Pages take
// minutes, so there's nothing to gain from asking more often.
export const POLL_INTERVAL_MS = 5000
// While the server can't be reached (restarting for a deploy, network
// dropped) back off up to this, rather than hammering it or giving up.
const MAX_RETRY_MS = 60000

export interface PendingUpload extends Omit<PendingUploadRecord, 'file' | 'pdf'> {
  // The server couldn't be reached on the last attempt; still retrying.
  stalled?: boolean
}

interface UploadQueueValue {
  pending: PendingUpload[]
  // Kicks off processing in the background and returns immediately -- the
  // caller (the upload page) can navigate away right after calling this;
  // the scores list renders `pending` as a placeholder with a spinner
  // until it resolves into a real saved score.
  startUpload: (file: File) => void
  retryUpload: (id: string) => void
  // Stops tracking an upload, whether it's still going or has failed.
  cancelUpload: (id: string) => void
}

const UploadQueueContext = createContext<UploadQueueValue | null>(null)

function toView({ file: _file, pdf: _pdf, ...rest }: PendingUploadRecord): PendingUpload {
  return rest
}

function storedPdf(record: PendingUploadRecord): Blob | undefined {
  if (record.pdf) return new Blob([record.pdf], { type: 'application/pdf' })
  return record.file instanceof Blob ? record.file : undefined
}

export function UploadQueueProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingUpload[]>([])
  // Uploads with a drive loop running, so a retry or a re-render can't
  // start a second one for the same upload.
  const driving = useRef(new Set<string>())
  // The File exactly as picked, for uploads started in this page load --
  // preferred over the stored copy, which only exists to survive a refresh.
  const pickedFiles = useRef(new Map<string, File>())
  const alive = useRef(true)

  const patch = useCallback((id: string, changes: Partial<PendingUpload>) => {
    setPending((prev) => prev.map((p) => (p.id === id ? { ...p, ...changes } : p)))
  }, [])

  const remove = useCallback((id: string) => {
    setPending((prev) => prev.filter((p) => p.id !== id))
  }, [])

  // Takes one upload from "PDF in IndexedDB" to "score in IndexedDB",
  // surviving anything short of the server actually rejecting it: a refresh
  // re-enters here from the stored record, a backend restart (which loses
  // the job) gets the PDF resubmitted, and an unreachable server is retried
  // with backoff.
  const drive = useCallback(
    async (id: string) => {
      if (driving.current.has(id)) return
      driving.current.add(id)
      let delay = POLL_INTERVAL_MS

      const fail = async (message: string) => {
        await updatePendingUpload(id, { error: message })
        patch(id, { error: message, stalled: false })
      }

      try {
        while (alive.current) {
          const record = await db.uploads.get(id)
          // Cancelled (possibly from another tab), or failed and waiting
          // on a retry.
          if (!record || record.error) return

          const pdf = pickedFiles.current.get(id) ?? storedPdf(record)
          if (!pdf) {
            await fail('The PDF for this upload was lost — please upload it again')
            return
          }

          try {
            const job = (await getScoreJob(id)) ?? (await submitScore(pdf, id))

            if (job.status === 'failed') {
              await fail(job.error ?? 'Processing failed')
              return
            }

            if (job.status === 'done') {
              const score = await fetchScoreResult(id, record.fileName, pdf)
              // null: the result went missing between the two calls (a
              // restart); go round again and resubmit.
              if (score) {
                await completePendingUpload(score)
                void discardScoreJob(id)
                remove(id)
                return
              }
            } else {
              const progress = {
                pagesDone: job.pagesDone,
                pageCount: job.pagesTotal ?? record.pageCount,
                queuePosition: job.queuePosition,
              }
              await updatePendingUpload(id, progress)
              patch(id, { ...progress, stalled: false })
            }
            delay = POLL_INTERVAL_MS
          } catch (err) {
            if (err instanceof UploadRejectedError) {
              await fail(err.message)
              return
            }
            patch(id, { stalled: true })
            delay = Math.min(delay * 2, MAX_RETRY_MS)
          }

          await new Promise((resolve) => setTimeout(resolve, delay))
        }
      } finally {
        driving.current.delete(id)
        if (!alive.current || !(await db.uploads.get(id))) pickedFiles.current.delete(id)
      }
    },
    [patch, remove],
  )

  // Pick up whatever was in flight when the page was last closed.
  useEffect(() => {
    alive.current = true
    void listPendingUploads().then((records) => {
      if (!alive.current) return
      setPending((prev) => {
        const known = new Set(prev.map((p) => p.id))
        return [...prev, ...records.filter((r) => !known.has(r.id)).map(toView)]
      })
      for (const record of records) {
        if (!record.error) void drive(record.id)
      }
    })
    return () => {
      alive.current = false
    }
  }, [drive])

  const startUpload = useCallback(
    (file: File) => {
      const id = uuid()
      const startedAt = Date.now()
      pickedFiles.current.set(id, file)
      setPending((prev) => [{ id, fileName: file.name, startedAt }, ...prev])

      void (async () => {
        const record: PendingUploadRecord = { id, fileName: file.name, startedAt, pdf: await file.arrayBuffer() }
        await savePendingUpload(record)
        void drive(record.id)

        // Fills the estimate in before the server reports a page count; the
        // upload is already under way by then and doesn't wait for it.
        const pageCount = await countPdfPages(file)
        if (pageCount) {
          setPending((prev) => prev.map((p) => (p.id === record.id && !p.pageCount ? { ...p, pageCount } : p)))
          await db.uploads.where('id').equals(record.id).filter((r) => !r.pageCount).modify({ pageCount })
        }
      })()
    },
    [drive],
  )

  const retryUpload = useCallback(
    (id: string) => {
      patch(id, { error: undefined, stalled: false })
      void updatePendingUpload(id, { error: undefined }).then(() => drive(id))
    },
    [drive, patch],
  )

  const cancelUpload = useCallback(
    (id: string) => {
      remove(id)
      pickedFiles.current.delete(id)
      void deletePendingUpload(id)
      void discardScoreJob(id)
    },
    [remove],
  )

  const value = useMemo(
    () => ({ pending, startUpload, retryUpload, cancelUpload }),
    [pending, startUpload, retryUpload, cancelUpload],
  )

  return <UploadQueueContext.Provider value={value}>{children}</UploadQueueContext.Provider>
}

export function useUploadQueue(): UploadQueueValue {
  const ctx = useContext(UploadQueueContext)
  if (!ctx) throw new Error('useUploadQueue must be used within UploadQueueProvider')
  return ctx
}
