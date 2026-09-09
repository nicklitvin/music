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
