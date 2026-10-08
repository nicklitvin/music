#!/usr/bin/env bash
# Runs ON the server, piped in over SSH by .github/workflows/deploy.yml.
#
# Deliberately boring and re-runnable: fetch, hard-reset to the pushed
# commit, rebuild whichever side changed, restart. Running it twice in a row
# is a no-op rather than a mess.
set -euo pipefail

# Defaults to the login user's own home, so this works whether the box is
# Ubuntu (`ubuntu`), Amazon Linux (`ec2-user`) or anything else, without
# anyone having to configure a path.
DEPLOY_PATH="${DEPLOY_PATH:-$HOME/music}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
BACKEND_SERVICE="${BACKEND_SERVICE:-music-backend}"

say() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

if [ ! -d "$DEPLOY_PATH/.git" ]; then
  say "ERROR: no git checkout at $DEPLOY_PATH"
  say "Set the DEPLOY_PATH repository variable to wherever the repo actually is."
  say "Candidates on this machine:"
  # The checkout nginx serves is the one that matters, so look there first.
  nginx_roots=$(grep -rhoP '^\s*root\s+\K[^;]+' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | tr -d ' ' || true)
  for root in $nginx_roots; do
    say "  nginx serves: $root"
  done
  find /home /srv /opt /var/www -maxdepth 4 -type d -name .git 2>/dev/null |
    sed 's#/\.git$##' | while read -r repo; do say "  git checkout: $repo"; done
  exit 1
fi

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

# On this box the backend runs under PM2 (as `music-backend`), which also
# brings it back at boot via pm2-<user>.service. Restart it there if so; no
# sudo needed, since PM2 runs as the deploy user.
if command -v pm2 >/dev/null 2>&1 && pm2 describe "$BACKEND_SERVICE" >/dev/null 2>&1; then
  say "restarting $BACKEND_SERVICE (pm2)"
  pm2 restart "$BACKEND_SERVICE" --update-env

  # Give it a moment, then fail the deploy if it did not come back.
  sleep 3
  status=$(pm2 jlist | python3 -c '
import json, sys
name = sys.argv[1]
print(next((p["pm2_env"]["status"] for p in json.load(sys.stdin) if p["name"] == name), "missing"))
' "$BACKEND_SERVICE")
  if [ "$status" != "online" ]; then
    say "ERROR: $BACKEND_SERVICE is $status after restart"
    pm2 logs "$BACKEND_SERVICE" --lines 40 --nostream || true
    exit 1
  fi
  # Remember the process list so a reboot resurrects the current setup.
  pm2 save --force >/dev/null

  # Only if sudo won't stop and ask for a password -- a deploy that hangs
  # on a prompt is worse than one that skips a reload nginx doesn't need
  # for static assets anyway.
  if systemctl is-active --quiet nginx && sudo -n true 2>/dev/null; then
    say "reloading nginx"
    sudo -n systemctl reload nginx
  fi
  say "deployed $current"
  exit 0
fi

# Otherwise, find the backend's systemd unit rather than assuming its name. Boxes get
# set up by hand and the unit ends up called whatever made sense that day.
if ! systemctl list-unit-files "$BACKEND_SERVICE.service" >/dev/null 2>&1 ||
   ! systemctl cat "$BACKEND_SERVICE" >/dev/null 2>&1; then
  say "no unit named $BACKEND_SERVICE; looking for the one that runs this app"
  discovered=$(grep -rlE "uvicorn|$DEPLOY_PATH" /etc/systemd/system/*.service 2>/dev/null |
    head -1 | xargs -r basename | sed 's/\.service$//' || true)
  if [ -n "$discovered" ]; then
    say "found $discovered"
    BACKEND_SERVICE="$discovered"
  else
    say "ERROR: could not find a systemd unit running this app."
    say "Units that look related:"
    systemctl list-units --type=service --all --no-legend 2>/dev/null |
      grep -iE 'music|uvicorn|fastapi|backend' || say "  (none)"
    say "What is actually serving port 8000:"
    sudo ss -lptnH 'sport = :8000' 2>/dev/null || say "  (could not inspect; is ss installed?)"
    say "Set BACKEND_SERVICE in deploy/remote-deploy.sh to the right unit name."
    exit 1
  fi
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
