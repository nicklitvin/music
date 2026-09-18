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
  in memory per-request and is discarded when the response is sent. One
  narrow, intentional exception: OMR (`oemer`) only accepts a file path, so
  `backend/app/services/oemer_engine.py` writes a page's PNG to a
  `TemporaryDirectory` for the duration of that page's processing and
  deletes it immediately after — nothing persists past the request.
- **Client-side persistence**: all visual/audio assets (page images,
  MusicXML, bounding boxes, and the original uploaded PDF) live in the
  browser's IndexedDB via Dexie — never in a server-side database.
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
  `content/full/<piece>/{score.pdf,notes.json,performance.*}` and
  `content/samples/<piece>/<variant>.wav`+`-truth.json`. The one exception
  is `backend/benchmark_results.json`, produced by
  `backend/scripts/run_benchmarks.py` and served by `GET /api/benchmarks`
  to the frontend's Benchmarks page — it **is** committed, but only ever
  holds aggregate metrics (F1 scores, accuracy percentages, jump counts).
  Never add per-note/per-pitch content to it.
