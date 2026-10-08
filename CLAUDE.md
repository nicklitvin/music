# Working in this repo

## Workflow

- After every prompt where changes are made, commit and push straight to
  `main` on `origin` (no confirmation needed — this is a standing
  instruction from the user, given explicitly).
- Frontend and backend are independent projects (`frontend/`, `backend/`);
  keep them decoupled — no shared code, no root-level build tooling.
- A pre-commit hook (`.githooks/pre-commit`) builds + tests whichever
  service(s) have staged changes and blocks the commit on failure. It's
  enabled via `git config core.hooksPath .githooks`, which is per-clone (not
  itself version-controlled) — run that once after cloning. If it's not set,
  run the checks manually before committing: `cd frontend && npm test`
  (build + vitest) and `cd backend && .venv/Scripts/python -m pytest`.

## Stack

- Frontend: Vite + React + TypeScript, tested with Vitest + Testing Library.
- Backend: FastAPI (Python), tested with pytest.

## Architecture constraints to preserve

- **Zero-server-image-storage**: the backend must never write uploaded PDFs
  or rendered page images to disk as persistent storage. Everything happens
  in memory and is discarded once done with. Score processing is the one
  thing that outlives a request: OMR runs as an in-memory background job
  (`backend/app/services/jobs.py`) whose result is held in memory until the
  client collects it (DELETE) or it expires — never on disk. This assumes a
  single uvicorn worker process; with several, a poll could land on a worker
  that doesn't have the job. One narrow, intentional exception: OMR (`oemer`) only accepts a file path, so
  `backend/app/services/oemer_engine.py` writes a page's PNG to a
  `TemporaryDirectory` for the duration of that page's processing and
  deletes it immediately after — nothing persists past that page.
- **Client-side persistence**: all visual/audio assets (page images,
  MusicXML, bounding boxes, and the original uploaded PDF) live in the
  browser's IndexedDB via Dexie — never in a server-side database. Uploads
  still being processed live there too (`uploads` table, PDF included), so a
  refresh resumes them and a backend restart just means resubmitting.
- **Note detection for position tracking** uses a vendored copy of
  Spotify's basic-pitch ONNX model (`backend/app/assets/basic_pitch/`,
  Apache 2.0 — keep its LICENSE/NOTICE alongside it). Deliberately vendored
  rather than `pip install basic-pitch`: the package wants a modern numpy,
  which collides with the older pins oemer needs, whereas the 230KB model
  file runs fine on the onnxruntime already pinned. Don't add the pip
  package. `USE_LEARNED_TRANSCRIPTION=false` reverts to the older
  hand-written estimator; keep both paths working.
- Pitch detection (`backend/app/services/pitch_detection.py`) does real
  frequency-domain detection (FFT peak-picking, single notes and chords) —
  no longer a stub. OMR (`backend/app/services/omr.py` +
  `oemer_engine.py`) runs the real `oemer` model per page — also no longer
  a stub, but slow (multiple minutes per page on CPU, no GPU here) and
  needs a pinned, older numpy/scipy/opencv/onnxruntime set to load oemer's
  pretrained models (see the comment in `backend/requirements.txt`) —
  don't casually bump those.
- **Test material lives in `content/`, entirely gitignored** (real, often
  copyrighted, sheet music PDFs and recordings) — laid out as
  `content/full/<piece>/{score.pdf,notes.json,performance.*}`. Accuracy
  testing uses only these real sheet+recording pairs (page 0, i.e. wherever
  OMR has actually been run) — there used to also be synthesized
  score-audio test cases under `content/samples/`, but those were retired
  and archived to `content/archive/samples/`; don't resurrect that pattern
  for new test cases, since a synthesized performance's exact-known ground
  truth doesn't tell you what real playing does to detection/tracking. The
  one exception to content/'s gitignore is `backend/benchmark_results.json`,
  produced by `backend/scripts/run_benchmarks.py` and served by
  `GET /api/benchmarks` for local inspection only (nothing in the UI reads
  it; see backend/README) — it **is** committed, but only ever holds
  aggregate metrics (F1 scores, accuracy percentages, lock times). Never
  add per-note/per-pitch content to it.
