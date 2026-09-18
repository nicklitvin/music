import type { ProcessScoreResponse, ScoreRecord } from './types'
import type { BenchmarkResults } from './benchmarkTypes'

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

async function callProcessScore(file: Blob, scoreId: string): Promise<ProcessScoreResponse> {
  const formData = new FormData()
  formData.append('file', file, 'score.pdf')
  formData.append('scoreId', scoreId)

  const res = await fetch(`${API_BASE_URL}/api/process-score`, {
    method: 'POST',
    body: formData,
  })

  if (!res.ok) {
    throw new Error(`Score processing failed: ${res.status} ${res.statusText}`)
  }

  return res.json()
}

function mapPages(pages: ProcessScoreResponse['pages']): ScoreRecord['pages'] {
  return pages.map((page) => ({
    pageIndex: page.pageIndex,
    image: base64ToBlob(page.imageBase64),
    width: page.width,
    height: page.height,
  }))
}

export async function processScore(file: File, scoreId: string): Promise<ScoreRecord> {
  const payload = await callProcessScore(file, scoreId)

  return {
    id: payload.scoreId,
    title: file.name.replace(/\.pdf$/i, ''),
    uploadDate: new Date().toISOString(),
    musicXml: payload.musicXml,
    boundingBoxes: payload.boundingBoxes,
    pages: mapPages(payload.pages),
    sourcePdf: file,
  }
}

// Re-runs OMR on an already-uploaded score's stored PDF (e.g. after the
// parsing algorithm changes) without asking the user to re-upload the file.
// Keeps id/title/uploadDate/sourcePdf, replaces the derived musicXml/
// boundingBoxes/pages with freshly parsed output.
export async function reprocessScore(score: ScoreRecord): Promise<ScoreRecord> {
  if (!score.sourcePdf) {
    throw new Error('This score has no stored PDF to reprocess (uploaded before this feature existed).')
  }

  const payload = await callProcessScore(score.sourcePdf, score.id)

  return {
    ...score,
    musicXml: payload.musicXml,
    boundingBoxes: payload.boundingBoxes,
    pages: mapPages(payload.pages),
  }
}

// Loads the dev-only sample score (see backend/app/routers/dev.py) --
// pre-computed note data for aLIEz.pdf's first page, served instantly from
// local content/ instead of running OMR. Lets the tracking UI be tested
// without waiting minutes per load. Throws if the backend has no local
// sample data to serve (e.g. scripts/extract_notes.py hasn't been run).
export async function loadSampleScore(page = 0): Promise<ScoreRecord> {
  const res = await fetch(`${API_BASE_URL}/api/dev/sample-score?page=${page}`)
  if (!res.ok) {
    throw new Error(`Sample score unavailable: ${res.status} ${res.statusText}`)
  }

  const payload: ProcessScoreResponse = await res.json()

  return {
    id: payload.scoreId,
    title: 'aLIEz (sample, page 1)',
    uploadDate: new Date().toISOString(),
    musicXml: payload.musicXml,
    boundingBoxes: payload.boundingBoxes,
    pages: mapPages(payload.pages),
    // No sourcePdf: the sample PDF is local-only (gitignored, copyrighted
    // sheet music), never sent to or stored in the browser.
  }
}

// Accuracy benchmark results (backend/scripts/run_benchmarks.py), for the
// Benchmarks page. 404s if nobody has run that script locally yet.
export async function getBenchmarks(): Promise<BenchmarkResults> {
  const res = await fetch(`${API_BASE_URL}/api/benchmarks`)
  if (!res.ok) {
    throw new Error(`Benchmarks unavailable: ${res.status} ${res.statusText}`)
  }
  return res.json()
}
