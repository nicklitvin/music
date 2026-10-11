import type { ProcessScoreJob, ProcessScoreResponse, ScoreRecord } from './types'

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

// A rejection the server meant (bad file, too large) as opposed to it being
// unreachable or restarting -- the upload queue gives up on the former and
// keeps retrying the latter.
export class UploadRejectedError extends Error {}

async function check(res: Response, what: string): Promise<Response> {
  if (res.ok) return res
  let detail = res.statusText
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') detail = body.detail
    // FastAPI's request-validation errors: a list of {loc, msg}.
    else if (Array.isArray(body?.detail)) {
      detail = body.detail.map((e: { loc?: unknown[]; msg?: string }) => `${e.loc?.at(-1) ?? ''}: ${e.msg}`).join('; ')
    }
  } catch {
    // Not JSON (a proxy error page, say); the status text will do.
  }
  const message = `${what} failed: ${res.status} ${detail}`
  // 5xx is the backend down or restarting (or the proxy in front of it
  // saying so); anything else is a verdict on the upload itself.
  throw res.status >= 500 ? new Error(message) : new UploadRejectedError(message)
}

// Every request gets a time limit. A phone that backgrounds the tab can
// leave a request hanging forever -- never resolving, never failing -- which
// used to freeze an upload on "Connecting..." for good. Timing out turns that
// into an ordinary error the upload queue retries.
const STATUS_TIMEOUT_MS = 20_000
// The result carries every page image, so it can be tens of MB.
const RESULT_TIMEOUT_MS = 180_000
// Generous: a 50 MB PDF on a slow mobile connection.
const UPLOAD_TIMEOUT_MS = 600_000

function fetchWithTimeout(url: string, timeoutMs: number, init: RequestInit = {}): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  return fetch(url, { ...init, signal: controller.signal }).finally(() => clearTimeout(timer))
}

// fetch can't report upload progress, so the upload itself goes through
// XMLHttpRequest. Resolves to a Response so it shares check() with the rest.
function postWithProgress(url: string, body: FormData, onProgress?: (fraction: number) => void): Promise<Response> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.timeout = UPLOAD_TIMEOUT_MS
    if (onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total)
      }
    }
    xhr.onload = () => resolve(new Response(xhr.responseText, { status: xhr.status, statusText: xhr.statusText }))
    // Network failure: the queue treats a plain Error as "retry later".
    xhr.onerror = () => reject(new Error('Upload failed: the server could not be reached'))
    xhr.ontimeout = xhr.onerror
    xhr.send(body)
  })
}

// Uploads the PDF and starts OMR as a background job on the server,
// resolving as soon as the upload has landed. Idempotent on scoreId, so
// resubmitting an upload the server already has just returns the existing
// job.
export async function submitScore(
  file: Blob,
  scoreId: string,
  onUploadProgress?: (fraction: number) => void,
): Promise<ProcessScoreJob> {
  const formData = new FormData()
  formData.append('file', file, 'score.pdf')
  formData.append('scoreId', scoreId)

  const res = await postWithProgress(`${API_BASE_URL}/api/process-score`, formData, onUploadProgress)
  return (await check(res, 'Upload')).json()
}

// null means the server has no such job -- it restarted, or the result
// expired -- and the upload needs submitting again.
export async function getScoreJob(scoreId: string): Promise<ProcessScoreJob | null> {
  const res = await fetchWithTimeout(
    `${API_BASE_URL}/api/process-score/${encodeURIComponent(scoreId)}`,
    STATUS_TIMEOUT_MS,
  )
  if (res.status === 404) return null
  return (await check(res, 'Checking progress')).json()
}

export async function fetchScoreResult(
  scoreId: string,
  fileName: string,
  sourcePdf: Blob,
): Promise<ScoreRecord | null> {
  const res = await fetchWithTimeout(
    `${API_BASE_URL}/api/process-score/${encodeURIComponent(scoreId)}/result`,
    RESULT_TIMEOUT_MS,
  )
  if (res.status === 404) return null
  const payload: ProcessScoreResponse = await (await check(res, 'Fetching the result')).json()

  return {
    id: payload.scoreId,
    title: fileName.replace(/\.pdf$/i, ''),
    uploadDate: new Date().toISOString(),
    musicXml: payload.musicXml,
    boundingBoxes: payload.boundingBoxes,
    pages: mapPages(payload.pages),
    sourcePdf,
  }
}

// Lets the server drop the result from memory once it's saved here.
export async function discardScoreJob(scoreId: string): Promise<void> {
  try {
    await fetchWithTimeout(`${API_BASE_URL}/api/process-score/${encodeURIComponent(scoreId)}`, STATUS_TIMEOUT_MS, {
      method: 'DELETE',
    })
  } catch {
    // It expires on its own anyway.
  }
}

function mapPages(pages: ProcessScoreResponse['pages']): ScoreRecord['pages'] {
  return pages.map((page) => ({
    pageIndex: page.pageIndex,
    image: base64ToBlob(page.imageBase64),
    width: page.width,
    height: page.height,
  }))
}
