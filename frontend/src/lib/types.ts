export interface NoteBoundingBox {
  x: number
  y: number
  width: number
  height: number
  note: string
  pitch: string
  measureIndex: number
  pageIndex: number
}

export interface ScorePage {
  pageIndex: number
  image: Blob
  width: number
  height: number
}

export interface ScoreRecord {
  id: string
  title: string
  uploadDate: string
  musicXml: string
  boundingBoxes: NoteBoundingBox[]
  pages: ScorePage[]
  // The original uploaded PDF, kept client-side (per the zero-server-storage
  // constraint) so the OMR pipeline can be re-run later -- e.g. after the
  // parsing algorithm improves -- without asking the user to re-upload.
  // Optional because scores saved before this field existed won't have it.
  sourcePdf?: Blob
  // Manual ordering for the scores list (drag handles) -- ascending, ties
  // broken by uploadDate. Optional because scores saved before drag
  // reordering existed won't have it; listScores() backfills a value from
  // uploadDate for those on the fly.
  sortOrder?: number
}

// An upload that hasn't finished processing yet. Kept in IndexedDB (with
// the PDF itself) so it survives a refresh and can be resubmitted if the
// backend lost the job, e.g. to a restart.
export interface PendingUploadRecord {
  id: string
  fileName: string
  startedAt: number
  // The PDF's raw bytes, not a Blob: WebKit (every browser on iOS) doesn't
  // reliably hand a Blob back out of IndexedDB intact, and a broken one got
  // sent as a string, which the server rejected with a 422. ArrayBuffers
  // round-trip everywhere.
  pdf?: ArrayBuffer
  // How records saved before `pdf` existed hold it; read-only fallback.
  file?: Blob
  pageCount?: number
  pagesDone?: number
  // Set while waiting behind other sheets on the server.
  queuePosition?: number
  error?: string
}

export interface ProcessScoreJob {
  jobId: string
  status: 'queued' | 'running' | 'done' | 'failed'
  pagesDone: number
  pagesTotal: number | null
  queuePosition: number
  error: string | null
}

export interface ProcessScoreResponse {
  scoreId: string
  musicXml: string
  boundingBoxes: NoteBoundingBox[]
  pages: {
    pageIndex: number
    imageBase64: string
    width: number
    height: number
  }[]
}

export interface NoteDetectionEvent {
  type: 'NOTE_DETECTION'
  notes: string[]
  confidence: number
  rms: number
  timestamp: number
}

// A NOTE_DETECTION plus the score position the backend's tracker inferred
// from it. Sent once the socket has been INIT'd with a score's notes.
export interface ScorePositionEvent extends Omit<NoteDetectionEvent, 'type'> {
  type: 'POSITION'
  onsetIndex: number
  positionConfidence: number
}

export type TrackingEvent = NoteDetectionEvent | ScorePositionEvent
