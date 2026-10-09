#!/usr/bin/env bash
# One-time (re-runnable) setup for HTTPS certificate renewal. Runs ON the
# server:
#
#   ssh -i your-key.pem USER@HOST 'bash -s' < deploy/setup-cert-renewal.sh
#
# Certbot's own systemd timer (certbot-renew.timer) does the renewing --
# twice a day, renewing once a certificate is within 30 days of expiry. This
# makes sure that timer is on, adds a deploy hook so nginx picks up a renewed
# certificate (it otherwise keeps serving the old one from memory until
# something reloads it), and proves the whole thing works with a dry run
# against Let's Encrypt's staging server.
set -euo pipefail

say() { echo "[certs] $*"; }

command -v certbot >/dev/null || { say "ERROR: certbot is not installed"; exit 1; }

say "enabling certbot-renew.timer"
sudo systemctl enable --now certbot-renew.timer

hook=/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
say "installing $hook"
sudo install -d -m 755 "$(dirname "$hook")"
sudo tee "$hook" >/dev/null <<'HOOK'
#!/bin/sh
# Runs after each successful renewal, so nginx serves the new certificate.
nginx -t && systemctl reload nginx
HOOK
sudo chmod 755 "$hook"

say "dry run (can sit quietly for a few minutes first -- certbot adds a random delay)"
sudo certbot renew --dry-run --no-random-sleep-on-renew

say "next run:"
systemctl list-timers certbot-renew.timer --no-pager | sed -n 2p
sudo certbot certificates 2>/dev/null | grep -E 'Domains|Expiry'
