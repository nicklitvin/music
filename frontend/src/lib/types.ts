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
