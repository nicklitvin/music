# Sheet Music Tracker

Upload a PDF of sheet music, then play along on piano: the page turns
itself as it hears where you are. Everything you upload — page images,
MusicXML, bounding boxes — is stored **only** in the browser's IndexedDB;
the backend never persists them to disk.

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
  ever written to disk, one narrow OMR exception noted below)
- `POST /api/process-score` — accepts a PDF upload, renders pages to PNG in
  memory (via PyMuPDF), runs real OMR (`oemer`, see `app/services/omr.py` /
  `oemer_engine.py`) per page, and returns MusicXML + note bounding boxes +
  base64 page images. Slow on CPU (multiple minutes per page).
- `WS /ws/track-audio` — receives streamed PCM16 audio chunks; once sent an
  `INIT` frame with the score's notes, runs real harmonic-salience pitch
  detection and a Markov position tracker, and streams back which onset
  (and page) it believes is sounding. See `app/services/note_estimation.py`,
  `position_markov.py`, and `app/routers/audio_ws.py`.

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
only, never the underlying musical content). `GET /api/benchmarks` still
serves it for local inspection, but nothing in the UI shows it -- these
numbers live here instead.

```
cd backend
.venv\Scripts\python scripts\run_benchmarks.py          # everything
.venv\Scripts\python scripts\run_benchmarks.py --only aliez
```

### Latest results (2026-09-17), page 0 of each sheet

**Real recordings** -- ground truth recovered by aligning detected audio
onsets to the score, so treat this as "roughly this good/bad", not exact:

| Piece | Length | Onsets matched | Note detection F1 (exact / pitch-class) | Live-path MAE (onsets) | Within ±3 onsets |
|---|---|---|---|---|---|
| aliez | 4:51 | 353/353 | 0.39 / 0.61 | 2.7 | 76% |
| melissa | 5:04 | 138/138 | 0.35 / 0.48 | 5.8 | 58% |
| unravel | 4:08 | 231/244 | 0.37 / 0.58 | 14.6 | 44% |
| last-stardust | 6:33 | 190/195 | 0.26 / 0.43 | 31.1 | 25% |
| angel-thesis | 4:58 | 141/175 | 0.19 / 0.40 | 53.0 | 3% |
| guren | 1:55 | 486/503 | 0.09 / 0.24 | 65.5 | 6% |
| sugar-song | 4:28 | 137/137 | 0.10 / 0.23 | 116.3 | 3% |
| departure | 5:37 | 147/149 | 0.01 / 0.19 | 128.7 | 0% |

`aliez` and `melissa` track well; the rest range from mediocre to failing.
Every piece tracks near-perfectly on synthesized audio from the same notes
(next table), so the gap is real-recording-specific -- most likely page-0 OMR
quality or a mismatch between what the recording actually plays first and
what OMR extracted as page 0 (arrangement differences, a played intro OMR
didn't capture), rather than the tracker itself. Not yet root-caused
per piece.

**Synthesized samples** (exact known ground truth), live method
(`markov-filter`), best case (`clean`, note-perfect) vs. worst case
(`sloppy`, the roughest preset) rendered from the same page-0 notes:

| Piece | clean: within ±3 / MAE | sloppy: within ±3 / MAE |
|---|---|---|
| aliez | 100% / 0.12 | 93% / 2.16 |
| angel-thesis | 100% / 0.28 | 90% / 3.17 |
| departure | 100% / 0.13 | 89% / 3.11 |
| guren | 94% / 0.80 | 70% / 18.89 |
| last-stardust | 99% / 0.26 | 93% / 1.70 |
| melissa | 100% / 0.21 | 89% / 1.06 |
| sugar-song | 99% / 0.30 | 70% / 15.67 |
| unravel | 94% / 1.12 | 83% / 2.14 |

`miiro`, `owari-no-sekai-kara`, `resonance`, `this-game`, and
`weight-of-the-world` have sheets under `content/full/` but no
accompanying recording, so they aren't scored.

## Known gaps (by design, for now)

- **Real-recording tracking accuracy varies a lot by piece** (see above) --
  good on `aliez`/`melissa`, poor on several others despite near-perfect
  synthesized-audio accuracy across the board. Needs root-causing per
  piece, likely starting with whether page-0 OMR actually matches what's
  played first in each recording.
- **Note detection** on real recordings tops out around F1 0.4 (exact
  pitch) / 0.6 (pitch class) even on the good pieces -- octave errors are
  the largest failure mode (`app/services/note_estimation.py`).
- **OMR** (`oemer`) is CPU-only here and takes multiple minutes per page,
  which is why `content/full/<piece>/notes.json` is generated once and
  reused rather than re-run on every benchmark pass.
