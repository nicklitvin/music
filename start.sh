#!/usr/bin/env bash
# Starts the backend (FastAPI/uvicorn) and frontend (Vite) dev servers together.
# Ctrl+C stops both.
set -e

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
  echo "Stopping servers..."
  kill "$backend_pid" "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd "$root/backend" && .venv/Scripts/python -m uvicorn app.main:app --reload) &
backend_pid=$!

(cd "$root/frontend" && npm run dev) &
frontend_pid=$!

wait
