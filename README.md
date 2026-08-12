# Sheet Music Tracker

Upload a PDF of sheet music, view it page-by-page in the browser, and get
real-time note-detection overlays while you play along on a piano. Visual
assets (page images, MusicXML, bounding boxes) are stored **only** in the
browser's IndexedDB — the backend never persists them to disk.

## Structure

This is a two-service monorepo, deployed as two separate Railway services:

```
frontend/   Vite + React + TypeScript + Vitest
backend/    FastAPI (Python) + WebSockets
```

Each has its own `railway.json`. When creating Railway services, point one
at this repo with **Root Directory** set to `frontend`, and the other with
**Root Directory** set to `backend`.

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
dev (Vite proxies to `localhost:8000`); set to the deployed backend's
Railway URL in production.

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

## Known gaps (by design, for now)

- **OMR**: page images are real (rendered via PyMuPDF at 300 DPI); actual
  note/measure recognition (MusicXML + bounding boxes) is stubbed pending
  integration with a real OMR engine (e.g. Audiveris, run out-of-process).
- **Pitch detection**: the WebSocket contract is real and working, but note
  detection is a simple RMS energy gate, not a real polyphonic transcription
  model (e.g. Onsets and Frames). Swappable behind `app/services/pitch_detection.py`.
