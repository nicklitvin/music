#!/usr/bin/env bash
# ONE-TIME server setup. Run it once, by hand, then deploys just work:
#
#   ssh -i your-key.pem USER@3.149.2.249 'bash -s' < deploy/install-service.sh
#
# The backend is currently a bare python process someone started by hand.
# That works right up until the box reboots or the process dies, at which
# point the site is down until somebody notices. This puts it under systemd
# so it starts at boot, restarts if it crashes, and can be restarted by the
# deploy without a password.
#
# Everything is derived from where this account actually has things, so
# there is nothing to edit before running it.
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-music-backend}"
DEPLOY_PATH="${DEPLOY_PATH:-$HOME/music}"
RUN_USER="$(id -un)"
SYSTEMCTL="$(command -v systemctl)"
PORT="${PORT:-8000}"

say() { echo "[setup] $*"; }

[ -d "$DEPLOY_PATH/backend" ] || { say "ERROR: no backend at $DEPLOY_PATH/backend"; exit 1; }

PYTHON="$DEPLOY_PATH/backend/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  say "no virtualenv at $PYTHON, creating one"
  python3 -m venv "$DEPLOY_PATH/backend/.venv"
  "$PYTHON" -m pip install --quiet --upgrade pip
  "$PYTHON" -m pip install --quiet -r "$DEPLOY_PATH/backend/requirements.txt"
fi

say "installing $SERVICE_NAME.service (user $RUN_USER, path $DEPLOY_PATH)"
sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<UNIT
[Unit]
Description=Sheet Music Tracker backend
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$DEPLOY_PATH/backend
ExecStart=$PYTHON -m uvicorn app.main:app --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

# Let the deploy restart this one service without a password, and nothing
# else. Deploys hang forever on a sudo password prompt.
say "granting passwordless restart for just this service"
sudo tee /etc/sudoers.d/music-deploy >/dev/null <<SUDOERS
$RUN_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL restart $SERVICE_NAME, $SYSTEMCTL reload nginx, $SYSTEMCTL is-active $SERVICE_NAME
SUDOERS
sudo chmod 440 /etc/sudoers.d/music-deploy
# A malformed sudoers file can lock you out of sudo entirely, so check it
# and remove it again if it doesn't parse.
sudo visudo -c -f /etc/sudoers.d/music-deploy >/dev/null || {
  say "ERROR: sudoers snippet invalid, removing it"
  sudo rm -f /etc/sudoers.d/music-deploy
  exit 1
}

# Stop whatever is on the port now, found by port rather than by name so a
# look-alike python process elsewhere is left alone.
existing=$(sudo ss -lptnH "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1 || true)
if [ -n "$existing" ]; then
  say "stopping the hand-started backend (pid $existing)"
  sudo kill "$existing" 2>/dev/null || true
  sleep 2
  sudo kill -9 "$existing" 2>/dev/null || true
fi

say "starting $SERVICE_NAME"
sudo "$SYSTEMCTL" daemon-reload
sudo "$SYSTEMCTL" enable --now "$SERVICE_NAME"

sleep 3
if "$SYSTEMCTL" is-active --quiet "$SERVICE_NAME"; then
  say "$SERVICE_NAME is running, and will come back on boot"
  say "done -- deploys will restart it from now on"
else
  say "ERROR: $SERVICE_NAME did not start"
  sudo journalctl -u "$SERVICE_NAME" -n 40 --no-pager || true
  exit 1
fi
