# Working in this repo

## Workflow

- After every prompt where changes are made, commit and push straight to
  `main` on `origin` (no confirmation needed — this is a standing
  instruction from the user, given explicitly).
- Frontend and backend are independent projects (`frontend/`, `backend/`);
  keep them decoupled — no shared code, no root-level build tooling.

## Stack

- Frontend: Vite + React + TypeScript, tested with Vitest + Testing Library.
- Backend: FastAPI (Python), tested with pytest.
- Deployment target: Railway, as two separate services (see root README and
  each folder's `railway.json`).

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
