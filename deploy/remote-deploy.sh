#!/usr/bin/env bash
# Runs ON the server, piped in over SSH by .github/workflows/deploy.yml.
#
# Deliberately boring and re-runnable: fetch, hard-reset to the pushed
# commit, rebuild whichever side changed, restart. Running it twice in a row
# is a no-op rather than a mess.
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-/home/ubuntu/music}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
BACKEND_SERVICE="${BACKEND_SERVICE:-music-backend}"

say() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

cd "$DEPLOY_PATH"

previous=$(git rev-parse HEAD)
say "at $previous, fetching $DEPLOY_BRANCH"
git fetch --prune origin "$DEPLOY_BRANCH"

# Hard reset rather than pull: the server's checkout is a deployment
# artifact, not somewhere work happens, and a merge conflict at 3am is not
# a thing anyone wants to debug over SSH.
git reset --hard "origin/$DEPLOY_BRANCH"
current=$(git rev-parse HEAD)
say "now at $current"

if [ "$previous" = "$current" ]; then
  say "nothing new; still rebuilding in case a previous deploy half-failed"
fi

changed=$(git diff --name-only "$previous" "$current" || true)
changed_backend=$(printf '%s\n' "$changed" | grep -c '^backend/' || true)
changed_frontend=$(printf '%s\n' "$changed" | grep -c '^frontend/' || true)
# A first deploy, or a re-run with no diff, should still do everything.
if [ "$previous" = "$current" ]; then
  changed_backend=1
  changed_frontend=1
fi

if [ "${changed_backend:-0}" -gt 0 ]; then
  say "backend: installing dependencies"
  cd "$DEPLOY_PATH/backend"
  if [ ! -d .venv ]; then
    say "backend: creating virtualenv"
    python3 -m venv .venv
  fi
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
  cd "$DEPLOY_PATH"
else
  say "backend: unchanged, skipping dependency install"
fi

if [ "${changed_frontend:-0}" -gt 0 ]; then
  say "frontend: building"
  cd "$DEPLOY_PATH/frontend"
  # `npm ci` needs the lockfile to match package.json; it is the whole point
  # (reproducible installs) so let it fail loudly if they have diverged.
  npm ci --no-audit --no-fund
  npm run build
  cd "$DEPLOY_PATH"
else
  say "frontend: unchanged, skipping build"
fi

say "restarting $BACKEND_SERVICE"
sudo systemctl restart "$BACKEND_SERVICE"

# Give it a moment, then fail the deploy if the unit did not come back.
sleep 3
if ! systemctl is-active --quiet "$BACKEND_SERVICE"; then
  say "ERROR: $BACKEND_SERVICE is not running after restart"
  sudo journalctl -u "$BACKEND_SERVICE" -n 40 --no-pager || true
  exit 1
fi

if systemctl is-active --quiet nginx; then
  say "reloading nginx"
  sudo systemctl reload nginx
fi

say "deployed $current"
