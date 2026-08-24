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
  or rendered page images to disk. Everything happens in memory per-request
  and is discarded when the response is sent.
- **Client-side persistence**: all visual/audio assets (page images,
  MusicXML, bounding boxes) live in the browser's IndexedDB via Dexie —
  never in a server-side database.
- OMR (`backend/app/services/omr.py`) and pitch detection
  (`backend/app/services/pitch_detection.py`) are intentionally stubbed
  behind real interfaces — see the module docstrings before assuming either
  does real ML inference.
