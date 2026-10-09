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
- `POST /api/process-score` — accepts a PDF upload and starts a background
  job (keyed by `scoreId`, idempotent) that renders pages to PNG in memory
  (via PyMuPDF) and runs real OMR (`oemer`, see `app/services/omr.py` /
  `oemer_engine.py`) per page. Returns `202` with the job's status straight
  away — OMR is minutes per page, far longer than a request can be held open.
  OMR runs in its own worker process (spawned per job), so an out-of-memory
  kill fails the job with that reason rather than taking the backend down.
  `GET /api/process-score/{id}` reports progress (`pagesDone`/`pagesTotal`,
  queue position, the worker's current `stage` from oemer's own log lines,
  its `cpuSeconds`, and a `warning` when the worker is alive but starved of
  CPU -- in practice, the machine swapping for lack of memory), `GET …/{id}/result` returns MusicXML + note bounding boxes +
  base64 page images once done, and `DELETE …/{id}` frees the result. Jobs
  live in memory only (`app/services/jobs.py`), so a restart loses them; the
  frontend keeps pending uploads (PDF included) in IndexedDB and resubmits.
- `WS /ws/track-audio` — receives streamed PCM16 audio chunks; once sent an
  `INIT` frame with the score's notes, transcribes the audio and runs a
  Markov position tracker over it, streaming back which onset (and page) it
  believes is sounding. See `app/services/streaming_transcription.py`,
  `position_markov.py`, and `app/routers/audio_ws.py`.

### Note detection

Position tracking is only as good as the per-frame evidence under it, and
that evidence comes from a vendored copy of Spotify's **basic-pitch**
transcription model (`app/assets/basic_pitch/`, Apache 2.0, with its
LICENSE and NOTICE). The model is 230KB and takes raw audio — the CQT front
end is inside the graph — so it runs on the onnxruntime the app already
pins, and the `basic-pitch` package itself is *not* a dependency (it wants
a modern numpy, which would collide with the older set oemer's models need).

`app/services/streaming_transcription.py` runs it over a live stream,
mirroring basic-pitch's own windowing because that windowing is
load-bearing: 1.99s windows, and the first and last 15 frames of each are
discarded because a convolutional model has no context past the window
edge. Discarding the trailing frames is what costs latency — a frame is
emitted once ~174ms of following audio exists — and inference runs every
~0.3s of new audio rather than every frame, which is what keeps it cheap.

On top of the transcribed pitch it also matches **note attacks**: the
tracker's templates describe what is *ringing* at a score position, which
is blurry by construction (a held chord looks the same for a second), while
where notes are *struck* pins a position down.

**To go back to the previous hand-written detector**
(`app/services/note_estimation.py`), set `USE_LEARNED_TRANSCRIPTION=false`
— nothing else needs changing, and both paths are covered by the test
suite. It is worth roughly 18 points of start-anywhere accuracy, so this is
a revert switch rather than a tuning knob.

```
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt   # includes test deps
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
.venv\Scripts\python -m pytest
```

Env vars (see `.env.example`): `FRONTEND_ORIGIN` (CORS), `RENDER_DPI`,
`USE_LEARNED_TRANSCRIPTION` (see "Note detection" above).

### How long OMR takes

Recognition is the slow part of the whole system: **a median 308s per page**
(range 296-374s over 57 timed pages), CPU-only, so a 10-page sheet is
around an hour and the full 65-page test corpus took ~5.6 hours. That is
why the upload UI shows an estimated time remaining rather than a bare
spinner, and why `scripts/extract_all_pages.py` is resumable and runs one
page per subprocess (oemer is memory-hungry enough to get OOM-killed on a
long run, and a kill should only cost the page in flight).

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

Every page of every sheet is OMR'd, not just page 0 -- `notes.json` is
built by `scripts/extract_all_pages.py`, which runs one page per
subprocess (oemer is memory-hungry and will OOM eventually) and is
resumable, so a kill only loses the page in flight. This matters more than
it sounds: see "why whole-sheet OMR" below.

```
cd backend
.venv\Scripts\python scripts\extract_all_pages.py       # OMR every page (hours)
.venv\Scripts\python scripts\run_benchmarks.py          # everything
.venv\Scripts\python scripts\run_benchmarks.py --only aliez
.venv\Scripts\python scripts\run_benchmarks.py --page 0 # single page, for comparison
```

### The metric: start anywhere

**Start anywhere** is the product requirement all of this is in service of:
the reader should be able to begin playing at an arbitrary point and have
the page find them within a few seconds. As a number: from 15 start points
per piece, a *cold* tracker (no seed at all) is fed audio from that point
and we measure whether the reported position becomes correct and *stays*
correct, and how fast.

Three details make this measure the right thing:

* **Start points are moments a note is actually struck** -- they are drawn
  from the recording's detected onsets, not spread evenly over the clock.
  Clock-spread starts land in rests, held chords and page turns, where
  there is nothing to identify a position from, and the tracker gets
  blamed for silence.
* **Correct means the right bar** (+/- 1), held for 8 consecutive frames so
  one lucky frame doesn't count. The requirement is really about staff
  *lines*, but a line cannot be recovered reliably from OMR geometry (the
  note rows of a grand staff don't cluster cleanly into systems -- a
  y-clustering attempt found 14 "lines" on a guren page that visibly has
  7). Bars come straight out of OMR and these sheets run 2-3 bars per
  system, so "the right bar or the one next to it" is the closest robust
  stand-in. The onset-count tolerance this replaced was badly behaved:
  +/-3 onsets is ~1s of music in a sparse passage but ~0.15s in a dense one
  (guren opens with 196 onsets in 10 seconds), so it silently demanded
  near-frame-exact tracking exactly where tracking is hardest.
* **"Eventually" vs "within 5s"** are reported separately. They are very
  different numbers, and the gap between them is the current problem.

### Latest results (2026-09-18), whole sheets, real recordings only

Ground truth is recovered by aligning detected audio onsets to the score,
so treat this as "roughly this good/bad", not exact. Every sheet is fully
OMR'd (65 pages across the 8 pieces that have recordings).

Run it with `scripts/compare_front_ends.py --streaming`, which scores the
same pieces, score, start points and lock rule against four note-detection
front ends. **streaming** is the one that matters: audio fed through
`StreamingTranscriber` in 75ms chunks exactly as the WebSocket handler
does, so nothing depends on audio that hasn't been played yet.

| Piece | Pages | Onsets | harmonic (old) | basic-pitch | + attacks | **streaming (live path)** |
|---|---|---|---|---|---|---|
| guren | 3 | 1042 | 11/15 | 14/15 | 14/15 | **13/15 (87%)** |
| angel-thesis | 11 | 1782 | 9/15 | 11/15 | 11/15 | **11/15 (73%)** |
| aliez | 4 | 1582 | 8/15 | 8/15 | 10/15 | **11/15 (73%)** |
| unravel | 6 | 1765 | 6/15 | 8/15 | 10/15 | **9/15 (60%)** |
| departure | 13 | 1855 | 5/15 | 10/15 | 12/15 | **8/15 (53%)** |
| last-stardust | 7 | 1609 | 3/15 | 6/15 | 7/15 | **8/15 (53%)** |
| melissa | 10 | 1536 | 6/15 | 8/15 | 8/15 | **8/15 (53%)** |
| sugar-song † | 11 | 1738 | 0/15 | 2/15 | 3/15 | **1/15 (7%)** |
| **within 5s** | **65** | | **48/120 (40%)** | 67/120 (56%) | 75/120 (62%) | **69/120 (58%)** |
| **locks eventually** | | | 106/120 (88%) | 115/120 (96%) | 116/120 (97%) | **114/120 (95%)** |

Streaming costs about 4 points against feeding the model whole files
(62% -> 58%), which is the price of only ever seeing a trailing window.
Excluding the key-mismatched `sugar-song`: **68/105 (65%)** within 5s.

† `sugar-song`'s recording is **11 semitones from its sheet's key** -- the
two are not the same arrangement, so it measures that mismatch rather than
the tracker. Pitch-class profiles correlate 0.81 when the recording is
shifted a semitone and **-0.45** in the sheet's own key; every other piece
matches at shift 0 (r 0.82-0.98). `evaluate_piece` now detects this and
says so in `warnings` instead of silently reporting a broken tracker.

### Why whole-sheet OMR

Testing used to run on page 0 only, and that quietly invalidated almost
every number. One page covers the first minute or so of a five-minute
recording, so detection and tracking were being scored against music that
was not being played. Re-running with the whole sheet:

| | page 0 only | whole sheet |
|---|---|---|
| guren, note detection F1 (exact) | 0.09 | **0.35** |
| guren, playthrough within tolerance | 1.7% | **38.7%** |
| guren, score covers | 73.5s of 115s | **110.2s of 115s** |
| departure, note detection F1 (exact) | 0.01 | **0.28** |
| last-stardust, score covers | **10%** of the recording | 80% |

It also cut the other way, which is the more important lesson: pieces that
looked *good* on page 0 were being scored over a tiny, easy slice.
`aliez` scored 15/15 on page 0 -- over the first 24% of its recording --
and 8/15 once the whole piece is in play. The page-0 numbers were not a
baseline, they were a different (easier) question.

`miiro`, `owari-no-sekai-kara`, `resonance`, `this-game`, and
`weight-of-the-world` have sheets under `content/full/` but no
accompanying recording, so they aren't scored.

### What changed this round

Five things, in order of how much they mattered:

0. **A learned note detector** (see "Note detection" above) -- replacing the
   hand-written harmonic-salience estimator with the vendored basic-pitch
   model, plus attack matching, is worth 40% -> 58% on its own, and took
   "locks eventually" from 88% to 95%.
1. **Whole-sheet OMR** (above) -- fixed the ground truth itself.
2. **The rate limiter no longer throttles first acquisition.**
   `ReportedPosition` exists to stop the highlight twitching during steady
   play, but it also applied before the tracker had ever locked on,
   creeping two onsets at a time from a placeholder 0 towards a belief
   that was already correct. Measured on guren, the belief locked in
   0.6-1.0s at several start points while the reported position took
   8-12s. It now follows the belief outright until the tracker is first
   confident.
3. **`LIVE_CONFIG` retuned for a whole-sheet search space.**
   `jump_probability` 1e-7 -> 0.03 (the mass spread over the score each
   frame, i.e. how fast belief can reach a distant hypothesis -- 1e-7 was
   tuned back when a session assumed you start at the top of a single page,
   where there is nowhere to migrate to) and `temperature` 0.12 -> 0.25.
   Applies only while unsettled, so steady tracking is untouched.
4. **No more assuming you start at the top**, and the locality window only
   applies once settled -- previously it made finding a start point outside
   the window structurally impossible.

Worth recording: an earlier round concluded "hyperparameter sweeps are flat,
we're at the tuning ceiling". That was measured against page-0 ground truth
and was simply wrong -- the same sweep on whole-sheet data moved
guren+departure from 33% to 53%. A flat sweep is evidence about the
measurement as much as about the thing being measured.

## Known gaps (by design, for now)

- **"Start anywhere" is at 58% within 5s on the live path, against an 80%
  target.** The shape of the gap is specific and worth stating precisely:
  the tracker almost always finds the right place (95% of start points lock
  eventually, 97% excluding the mismatched-key piece) -- it just doesn't
  always find it *fast*. This is an acquisition-speed problem, not a
  "can't find it" problem, and the two want different fixes.
- **Streaming costs ~4 points** against whole-file inference (62% -> 58%),
  because the model only ever sees a trailing window. Worth revisiting if
  it becomes the binding constraint; it currently is not.
- **Rhythm is now half-used.** Note *attacks* are matched (worth ~5 points),
  but inter-onset *timing* still is not: the score knows how long each onset
  should last, and the detected onsets give the played rhythm directly.
  Matching those interval patterns is the obvious next lever where pitch
  alone stays ambiguous.
- **Slow acquisition is concentrated in dense, repetitive passages.** On
  guren the failing start points are all in the first ~40s, where the
  opening riff repeats and the score runs ~20 onsets/second; the tracker
  cannot tell which repetition it is hearing until the music moves on.
  Some of this is irreducible ambiguity rather than a fixable defect.
- **Don't trust the note-detection F1 in `benchmark_results.json`.** It is
  computed from the top 6 pitches above a relative threshold, which throws
  away the shape of the evidence the tracker actually consumes. basic-pitch
  scores *worse* on it than the old estimator (0.16 vs 0.28 exact on
  departure) while tracking far better (11/15 vs 5/15). An earlier round
  concluded "the gap tracks each piece's note-detection F1" on the strength
  of that number; it was the wrong proxy.
- **`sugar-song` needs a matching recording** -- its current one is a
  semitone off the sheet, so it is not testing anything useful. Either
  source a recording of that arrangement or transpose one. (Making the
  tracker itself transposition-robust is a plausible *feature* -- detect
  the offset at INIT and shift the templates -- but it is not why the
  benchmark is red.)
- **OMR** (`oemer`) is CPU-only here and takes a median 308s per page, so
  `content/full/<piece>/notes.json` is generated once by
  `scripts/extract_all_pages.py` (~5.6 hours for all 65 pages) and reused
  rather than re-run on every benchmark pass. This is also the dominant
  cost a real user pays on upload -- see "How long OMR takes" above.
- **Line detection is approximated by bars.** Staff systems can be found
  from the page image by horizontal projection (staff lines are the only
  thin full-width runs of ink), and this works on most pages -- but dense
  engraving loses the odd staff, which then mis-pairs treble/bass into
  systems. Until that is solid the benchmark scores "right bar +/-1", which
  at 2-3 bars per system is close but not the same claim.
