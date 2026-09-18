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
```

Testing used to also include a set of synthesized (score-rendered-to-audio)
performances with exactly-known ground truth, under `content/samples/`.
Those have been retired and archived to `content/archive/samples/`: a
synthesized performance is note-perfect timing and instrumentation, so it
never surfaced the failures that matter -- namely, whether the tracker can
find a real player's position at all when they start somewhere other than
the top of the page. All accuracy testing now runs on real sheet+recording
pairs only, tried from many starting points within the recording.

`backend/scripts/run_benchmarks.py` scores every piece under `content/full/`
against its real recording: note detection, every position-tracking method,
the live seed-at-top/rate-limited playthrough, and -- the metric that
actually matters for the product -- how often a *cold* tracker (no seed,
started at an arbitrary point in the recording) locks onto the correct
position within a few seconds. It writes `backend/benchmark_results.json`
-- committed (aggregate metrics only, never the underlying musical
content). `GET /api/benchmarks` still serves it for local inspection, but
nothing in the UI shows it -- these numbers live here instead.

```
cd backend
.venv\Scripts\python scripts\run_benchmarks.py          # everything
.venv\Scripts\python scripts\run_benchmarks.py --only aliez
```

### Latest results (2026-09-17), page 0 of each sheet, real recordings only

Ground truth is recovered by aligning detected audio onsets to the score,
so treat this as "roughly this good/bad", not exact. **Start anywhere**
is the product requirement this is ultimately in service of: from 15 points
spread across the recording, a *cold* tracker (no seed at all, matching
what a real player dropping in partway through the page looks like) is
fed audio from there and we measure whether it locks onto the correct
position -- within +/-3 onsets, held for 8 consecutive frames -- and how
fast. "Eventually" has no time limit (up to the end of the matched
recording); "within 5s" is the actual target (see `evaluate_start_points`
in `app/services/benchmark_eval.py`).

| Piece | Length | Onsets matched | Playthrough-from-top within ±3 | Start anywhere: locks eventually | Start anywhere: within 5s |
|---|---|---|---|---|---|
| melissa | 5:04 | 138/138 | 58% | 15/15 (100%) | 10/15 (67%) |
| unravel | 4:08 | 231/244 | 41% | 15/15 (100%) | 10/15 (67%) |
| aliez | 4:51 | 353/353 | 76% | 15/15 (100%) | 9/15 (60%) |
| angel-thesis | 4:58 | 141/175 | 12% | 14/15 (93%) | 8/15 (53%) |
| last-stardust | 6:33 | 190/195 | 25% | 7/15 (47%) | 6/15 (40%) |
| sugar-song | 4:28 | 137/137 | 5% | 13/15 (87%) | 1/15 (7%) |
| departure | 5:37 | 147/149 | 0% | 2/15 (13%) | 2/15 (13%) |
| guren | 1:55 | 486/503 | 2% | 7/15 (47%) | 1/15 (7%) |
| **overall** | | | | **88/120 (73%)** | **47/120 (39%)** |

Not yet at the 80%-within-a-few-seconds target. Two production bugs were
fixed as part of this round that were actively working against it (see
`app/services/position_markov.py` and `app/routers/audio_ws.py`):

1. Every live session used to seed the tracker's belief at onset 0 and call
   that "starting tracking" -- i.e. it assumed the reader always starts at
   the top of the page. A performer starting elsewhere had to fight that
   assumption instead of being found by it. Now the belief starts uniform
   (no assumption at all) unless a caller explicitly says otherwise.
2. `LIVE_CONFIG`'s locality window (limits how far one frame can move the
   belief, to stop a confidently-correct tracker being yanked to a
   repeated passage) used to apply unconditionally -- including to a
   just-started, still-uncertain tracker, which made it structurally
   impossible to ever find a start point outside the window. It now only
   applies once the tracker is actually settled.

Those fixes take "start anywhere" from *structurally can't work* to
*works some of the time, per above*. Hyperparameter sweeps on top of that
(temperature, evidence floor/ceiling, jump probability, skip parameters,
analysis window size, octave-tolerant salience matching) did not move the
numbers further -- the remaining gap tracks each piece's note-detection
quality, not the position tracker's tuning:

- `melissa`, `unravel`, `aliez` (and to a lesser extent `angel-thesis`)
  have real accompanying recordings whose note detection is usable enough
  that a cold tracker *always* eventually finds the right spot; getting
  there within 5s about half-to-two-thirds of the time is the actual
  current ceiling for this group.
- `departure`, `guren`, `sugar-song` have near-zero note-detection F1 (see
  below) -- most likely page-0 OMR not matching what the recording
  actually plays first -- so there usually isn't enough real signal to
  lock onto at all, regardless of tracker tuning. Fixing this is an OMR/
  note-detection problem, not a tracking-algorithm one.
- `last-stardust` sits in between: locks less than half the time even
  given the whole recording, worth root-causing specifically.

`miiro`, `owari-no-sekai-kara`, `resonance`, `this-game`, and
`weight-of-the-world` have sheets under `content/full/` but no
accompanying recording, so they aren't scored.

## Known gaps (by design, for now)

- **"Start anywhere" isn't at its 80%-within-5s target yet** (see above) --
  47/120 tried start points across 8 real recordings. The tracker
  eventually finds the right spot far more often (88/120) than it finds it
  *fast*, so the next lever is likely speeding up acquisition (e.g. a
  rhythm/timing signal alongside pitch, since isolated single notes are
  often genuinely ambiguous on their own) rather than more Markov-parameter
  tuning, which was swept without further gains this round.
- **Real-recording tracking accuracy varies a lot by piece** (see above) --
  good on `aliez`/`melissa`/`unravel`, poor on `departure`/`guren`/
  `sugar-song` specifically because of near-zero note detection on those
  recordings, likely a page-0 OMR mismatch. Needs root-causing per piece.
- **Note detection** on real recordings tops out around F1 0.4 (exact
  pitch) / 0.6 (pitch class) even on the good pieces -- octave errors are
  the largest failure mode (`app/services/note_estimation.py`).
- **OMR** (`oemer`) is CPU-only here and takes multiple minutes per page,
  which is why `content/full/<piece>/notes.json` is generated once and
  reused rather than re-run on every benchmark pass, and why only page 0
  of each sheet is tested for now.
