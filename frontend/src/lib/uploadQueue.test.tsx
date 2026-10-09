import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Scores } from '../features/scores/Scores'
import { db } from './db'
import { POLL_INTERVAL_MS, UploadQueueProvider } from './uploadQueue'
import type { PendingUploadRecord } from './types'

function renderScores() {
  return render(
    <BrowserRouter>
      <UploadQueueProvider>
        <Scores />
      </UploadQueueProvider>
    </BrowserRouter>,
  )
}

function pendingUpload(overrides: Partial<PendingUploadRecord> = {}): PendingUploadRecord {
  return {
    id: 'upload-1',
    fileName: 'Nocturne.pdf',
    startedAt: Date.now(),
    pdf: new TextEncoder().encode('%PDF').buffer as ArrayBuffer,
    ...overrides,
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function job(status: string, extra: Record<string, unknown> = {}) {
  return { jobId: 'upload-1', status, pagesDone: 0, pagesTotal: 2, queuePosition: 0, error: null, ...extra }
}

const RESULT = {
  scoreId: 'upload-1',
  musicXml: '<score-partwise />',
  boundingBoxes: [],
  pages: [{ pageIndex: 0, imageBase64: btoa('png'), width: 10, height: 14 }],
}

// Routes fetch by method + path to a queue of canned responses per route.
function mockServer(routes: Record<string, Array<() => Response>>) {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const key = `${init?.method ?? 'GET'} ${url}`
    calls.push(key)
    const queue = routes[key]
    if (!queue?.length) return new Response(null, { status: 204 })
    return (queue.length > 1 ? queue.shift()! : queue[0])()
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

// The upload goes through XMLHttpRequest (for its progress events); this
// stands in for it by handing the request to whatever fetch is mocked as,
// after reporting the upload as half and then fully sent.
class FakeXHR {
  upload: { onprogress?: (event: { lengthComputable: boolean; loaded: number; total: number }) => void } = {}
  onload?: () => void
  onerror?: () => void
  ontimeout?: () => void
  status = 0
  statusText = ''
  responseText = ''
  private method = 'GET'
  private url = ''

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }

  send(body: unknown) {
    this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 })
    this.upload.onprogress?.({ lengthComputable: true, loaded: 100, total: 100 })
    void fetch(this.url, { method: this.method, body: body as BodyInit }).then(
      async (res) => {
        this.status = res.status
        this.statusText = res.statusText
        this.responseText = await res.text()
        this.onload?.()
      },
      () => this.onerror?.(),
    )
  }
}

describe('upload queue', () => {
  beforeEach(async () => {
    await db.scores.clear()
    await db.uploads.clear()
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('picks an in-flight upload back up after a refresh and saves it when done', async () => {
    // What a refresh mid-upload leaves behind: just the stored record.
    await db.uploads.put(pendingUpload())
    mockServer({
      'GET /api/process-score/upload-1': [() => json(job('done', { pagesDone: 2 }))],
      'GET /api/process-score/upload-1/result': [() => json(RESULT)],
    })

    renderScores()

    expect(await screen.findByText('Nocturne')).toBeInTheDocument()
    expect(await db.uploads.count()).toBe(0)
    const saved = await db.scores.get('upload-1')
    expect(saved?.pages).toHaveLength(1)
    expect(saved?.sourcePdf).toBeDefined()
  })

  it('resubmits the stored PDF when the server has lost the job', async () => {
    // A backend restart (every deploy) forgets every job.
    await db.uploads.put(pendingUpload())
    const calls = mockServer({
      'GET /api/process-score/upload-1': [() => json({ detail: 'No such job' }, 404)],
      'POST /api/process-score': [() => json(job('done'), 202)],
      'GET /api/process-score/upload-1/result': [() => json(RESULT)],
    })

    renderScores()

    expect(await screen.findByText('Nocturne')).toBeInTheDocument()
    expect(calls).toContain('POST /api/process-score')
  })

  it('shows real progress while the server works through the pages', async () => {
    await db.uploads.put(pendingUpload())
    mockServer({
      'GET /api/process-score/upload-1': [() => json(job('running', { pagesDone: 1 }))],
    })

    renderScores()

    expect(await screen.findByText(/1 of 2 pages done/)).toBeInTheDocument()
    expect((await db.uploads.get('upload-1'))?.pagesDone).toBe(1)
  })

  it('keeps a rejected upload on screen with its error, across a refresh', async () => {
    await db.uploads.put(pendingUpload())
    mockServer({
      'GET /api/process-score/upload-1': [() => json({ detail: 'No such job' }, 404)],
      'POST /api/process-score': [() => json({ detail: 'PDF exceeds maximum upload size' }, 413)],
    })

    const { unmount } = renderScores()
    expect(await screen.findByText(/PDF exceeds maximum upload size/)).toBeInTheDocument()
    unmount()

    renderScores()
    expect(await screen.findByText(/PDF exceeds maximum upload size/)).toBeInTheDocument()
  })

  it('sends the stored PDF as a file, not a string', async () => {
    // iOS once sent a Blob read back out of IndexedDB as a string field,
    // which the server rejected with a 422.
    await db.uploads.put(pendingUpload())
    let sent: FormDataEntryValue | null = null
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'POST') {
          sent = (init.body as FormData).get('file')
          return json(job('running'), 202)
        }
        return json({ detail: 'No such job' }, 404)
      }),
    )

    renderScores()

    await waitFor(() => expect(sent).toBeInstanceOf(Blob))
  })

  it('asks for a re-upload when a stored PDF is unreadable, rather than sending junk', async () => {
    // Saved before PDFs were kept as bytes, and the Blob didn't survive.
    await db.uploads.put(pendingUpload({ pdf: undefined, file: {} as Blob }))
    const calls = mockServer({})

    renderScores()

    expect(await screen.findByText(/upload it again/i)).toBeInTheDocument()
    expect(calls).not.toContain('POST /api/process-score')
  })

  it('shows what the server worker is doing, and says so when it is starved', async () => {
    await db.uploads.put(pendingUpload())
    mockServer({
      'GET /api/process-score/upload-1': [
        () => json(job('running', { stage: 'Extracting noteheads' })),
        () =>
          json(
            job('running', {
              stage: 'Extracting noteheads',
              warning: 'The server is out of memory, so processing has almost stopped',
            }),
          ),
      ],
    })

    renderScores()

    expect(await screen.findByText(/Extracting noteheads/)).toBeInTheDocument()
    expect(
      await screen.findByText(/out of memory/, {}, { timeout: POLL_INTERVAL_MS + 2000 }),
    ).toBeInTheDocument()
  }, 15000)

  it('uploads first, with a progress bar, then moves on to processing', async () => {
    const user = userEvent.setup()
    let release: () => void = () => {}
    const uploaded = new Promise<void>((resolve) => (release = resolve))
    // Hold the POST open so the uploading phase can be seen.
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') {
        await uploaded
        return json(job('running', { stage: 'Extracting stafflines' }), 202)
      }
      return json(job('running', { stage: 'Extracting stafflines' }))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderScores()
    await user.click(screen.getByRole('button', { name: /upload/i }))
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, new File(['%PDF'], 'Etude.pdf', { type: 'application/pdf' }))

    expect(await screen.findByRole('progressbar', { name: /uploading etude\.pdf/i })).toBeInTheDocument()
    expect(screen.getByText(/Uploading — \d+%/)).toBeInTheDocument()

    release()
    expect(await screen.findByText(/Extracting stafflines/)).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('keeps retrying, rather than failing, while the server is unreachable', async () => {
    await db.uploads.put(pendingUpload())
    mockServer({
      'GET /api/process-score/upload-1': [() => new Response('Bad Gateway', { status: 502 })],
    })

    renderScores()

    expect(await screen.findByText(/can't reach the server/i)).toBeInTheDocument()
    expect((await db.uploads.get('upload-1'))?.error).toBeUndefined()
  })

  it('cancelling an upload forgets it for good', async () => {
    const user = userEvent.setup()
    await db.uploads.put(pendingUpload())
    mockServer({
      'GET /api/process-score/upload-1': [() => json(job('running'))],
    })

    renderScores()
    await user.click(await screen.findByRole('button', { name: /cancel nocturne\.pdf/i }))

    await waitFor(() => expect(screen.queryByText('Nocturne.pdf')).not.toBeInTheDocument())
    await waitFor(async () => expect(await db.uploads.count()).toBe(0))
  })
})
