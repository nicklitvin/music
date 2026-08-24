import type { ProcessScoreResponse, ScoreRecord } from './types'

// Empty string means "same origin" -- used for local dev, where Vite proxies
// /api to the backend (see vite.config.ts). In production, set
// VITE_API_BASE_URL to the deployed backend's URL.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

function base64ToBlob(base64: string, contentType = 'image/png'): Blob {
  const byteChars = atob(base64)
  const byteNumbers = new Array(byteChars.length)
  for (let i = 0; i < byteChars.length; i++) {
    byteNumbers[i] = byteChars.charCodeAt(i)
  }
  return new Blob([new Uint8Array(byteNumbers)], { type: contentType })
}

export async function processScore(file: File, scoreId: string): Promise<ScoreRecord> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('scoreId', scoreId)

  const res = await fetch(`${API_BASE_URL}/api/process-score`, {
    method: 'POST',
    body: formData,
  })

  if (!res.ok) {
    throw new Error(`Score processing failed: ${res.status} ${res.statusText}`)
  }

  const payload: ProcessScoreResponse = await res.json()

  return {
    id: payload.scoreId,
    title: file.name.replace(/\.pdf$/i, ''),
    uploadDate: new Date().toISOString(),
    musicXml: payload.musicXml,
    boundingBoxes: payload.boundingBoxes,
    pages: payload.pages.map((page) => ({
      pageIndex: page.pageIndex,
      image: base64ToBlob(page.imageBase64),
      width: page.width,
      height: page.height,
    })),
  }
}
