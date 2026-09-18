import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { processScore } from './api'
import { nextSortOrderForNewScore, saveScore } from './db'

export interface PendingUpload {
  id: string
  fileName: string
  error?: string
}

interface UploadQueueValue {
  pending: PendingUpload[]
  // Kicks off processing in the background and returns immediately -- the
  // caller (the upload page) can navigate away right after calling this;
  // the scores list renders `pending` as a placeholder with a spinner
  // until it resolves into a real saved score.
  startUpload: (file: File) => void
  dismissError: (id: string) => void
}

const UploadQueueContext = createContext<UploadQueueValue | null>(null)

export function UploadQueueProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingUpload[]>([])

  const startUpload = useCallback((file: File) => {
    const id = crypto.randomUUID()
    setPending((prev) => [{ id, fileName: file.name }, ...prev])

    void (async () => {
      try {
        const score = await processScore(file, id)
        score.sortOrder = await nextSortOrderForNewScore()
        await saveScore(score)
        setPending((prev) => prev.filter((p) => p.id !== id))
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Upload failed'
        setPending((prev) => prev.map((p) => (p.id === id ? { ...p, error: message } : p)))
      }
    })()
  }, [])

  const dismissError = useCallback((id: string) => {
    setPending((prev) => prev.filter((p) => p.id !== id))
  }, [])

  const value = useMemo(() => ({ pending, startUpload, dismissError }), [pending, startUpload, dismissError])

  return <UploadQueueContext.Provider value={value}>{children}</UploadQueueContext.Provider>
}

export function useUploadQueue(): UploadQueueValue {
  const ctx = useContext(UploadQueueContext)
  if (!ctx) throw new Error('useUploadQueue must be used within UploadQueueProvider')
  return ctx
}
