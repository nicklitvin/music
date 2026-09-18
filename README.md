# Sheet Music Tracker

Upload a PDF of sheet music, view it page-by-page in the browser, and get
real-time note-detection overlays while you play along on a piano. Visual
assets (page images, MusicXML, bounding boxes) are stored **only** in the
browser's IndexedDB — the backend never persists them to disk.

## Structure

This is a two-project monorepo:

```
frontend/   Vite + React + TypeScript + Vitest
backend/    FastAPI (Python) + WebSockets
```

Frontend and backend are independent projects with no shared code or
root-level build tooling.

### Pre-commit checks

A hook builds + tests whichever service(s) you've staged changes in, and
blocks the commit if either fails. Enable it once per clone:

```
git config core.hooksPath .githooks
```

## Frontend (`frontend/`)

- React + TypeScript, built with Vite
- `Dexie.js` for IndexedDB persistence (scores, pages, bounding boxes)
- `pdfjs-dist` available for any client-side PDF needs
- An `AudioWorklet` (`public/worklets/pcm-worklet.js`) captures mic audio,
  downsamples to 16kHz mono PCM16, and streams it over WebSocket
- Vitest + Testing Library for tests

```
cd frontend
npm install
npm run dev        # local dev server, proxies /api and /ws to :8000
npm test           # vitest run
npm run build       # production build to dist/
```

Env vars (see `.env.example`): `VITE_API_BASE_URL` — leave unset for local
dev (Vite proxies to `localhost:8000`); set to the deployed backend's URL
in production.

## Backend (`backend/`)

- FastAPI, in-memory-only PDF processing (zero-server-storage: nothing is
  ever written to disk)
- `POST /api/process-score` — accepts a PDF upload, renders pages to PNG
  in memory (via PyMuPDF), and returns MusicXML + bounding boxes + base64
  page images. Note-level OMR and the real polyphonic pitch-detection model
  are currently **stubs** — see `app/services/omr.py` and
  `app/services/pitch_detection.py` for what's real vs. placeholder and how
  to swap in a real engine.
- `WS /ws/track-audio` — receives streamed PCM16 audio chunks, emits
  `NOTE_DETECTION` events

```
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt   # includes test deps
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
.venv\Scripts\python -m pytest
```

Env vars (see `.env.example`): `FRONTEND_ORIGIN` (CORS), `RENDER_DPI`.

## Accuracy benchmarks

`content/` (gitignored -- real, often copyrighted, sheet music and
recordings) holds the test material, laid out as:

```
content/
  full/<piece>/score.pdf          one sheet, as uploaded
              /notes.json         OMR output (scripts/extract_notes.py)
              /performance.mp3    the real accompanying recording, if any
  samples/<piece>/<variant>.wav          synthesized flawed performance
                  /<variant>-truth.json  its exact, known ground truth
```

`backend/scripts/run_benchmarks.py` scores every piece under `content/full/`
against its real recording (note detection + every position-tracking
method + the live seed-at-top/rate-limited path), and every synthesized
sample under `content/samples/` the same way `evaluate_tracking.py` does.
It writes `backend/benchmark_results.json` -- committed (aggregate metrics
only, never the underlying musical content) -- which `GET /api/benchmarks`
serves to the frontend's **Benchmarks** page (`/benchmarks`).

```
cd backend
.venv\Scripts\python scripts\run_benchmarks.py          # everything
.venv\Scripts\python scripts\run_benchmarks.py --only aliez
```

## Known gaps (by design, for now)

- **OMR**: page images are real (rendered via PyMuPDF at 300 DPI); actual
  note/measure recognition (MusicXML + bounding boxes) is stubbed pending
  integration with a real OMR engine (e.g. Audiveris, run out-of-process).
- **Pitch detection**: the WebSocket contract is real and working, but note
  detection is a simple RMS energy gate, not a real polyphonic transcription
  model (e.g. Onsets and Frames). Swappable behind `app/services/pitch_detection.py`.
