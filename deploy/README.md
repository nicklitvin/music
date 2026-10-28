# Deploying

Pushing to `main` runs `.github/workflows/deploy.yml`, which SSHes to the
server, resets the checkout to the pushed commit, rebuilds whichever side
changed, restarts the backend, and then checks the site actually answers.

## What you have to set up

### Repository secrets

Settings → Secrets and variables → Actions → **Secrets**:

| Secret | What it is |
|---|---|
| `SSH_PRIVATE_KEY` | The **contents** of your `.pem` file, whole thing including the `-----BEGIN ... KEY-----` and `-----END ... KEY-----` lines. Paste the file, not the path. |
| `DEPLOY_USER` | The login on the box: `ubuntu` for Ubuntu AMIs, `ec2-user` for Amazon Linux. |
| `SSH_KNOWN_HOSTS` | Optional but recommended — see "Pin the host key" below. |

The `.pem` file itself must never be committed. `.gitignore` covers `*.pem`
as a backstop, but the real rule is that it only ever exists on your machine
and in that secret.

### Repository variables

Settings → Secrets and variables → Actions → **Variables** (these are not
secret, they just avoid hardcoding):

| Variable | Default if unset |
|---|---|
| `DEPLOY_HOST` | `18.219.22.51` |
| `DEPLOY_PATH` | `~/music` for whoever `DEPLOY_USER` is — only set this if the checkout lives somewhere else |
| `SITE_URL` | `https://scrollsheetmusic.com` — what the post-deploy check fetches. Must be the real hostname: nginx 404s requests to the bare IP. |

The box's public IP is not an Elastic IP, so **it changes whenever the instance
is stopped and started** (a resize, say) — it went from `3.149.2.249` to
`3.145.73.36` to `18.219.22.51` that way. When it does, update the DNS A records for
`scrollsheetmusic.com` and `www`, and `DEPLOY_HOST`. Associating an Elastic IP
with the instance stops it changing at all.

`DEPLOY_USER` also works as a variable rather than a secret, and reads
better in logs if you do: as a secret it gets masked, so a path like
`/home/ec2-user/music` prints as `/home/***/music`, which is confusing
exactly when you're debugging a path.

### Pin the host key

Without `SSH_KNOWN_HOSTS` the workflow falls back to `ssh-keyscan`, which
accepts whatever key answers on the day — fine in practice, but it means a
machine-in-the-middle on that connection would be trusted silently. To close
that off, run locally:

```bash
ssh-keyscan -H 18.219.22.51
```

and paste the output into the `SSH_KNOWN_HOSTS` secret.

## How the backend gets restarted

The deploy handles two setups and picks whichever the box actually uses:

**PM2** (what `18.219.22.51` uses). The backend runs as a PM2 app named
`music-backend`, and PM2 brings it back at boot via its own
`pm2-<user>.service`. The deploy restarts it with `pm2 restart`, checks it
came back `online`, and runs `pm2 save` so the boot-time list stays
current. No sudo involved — PM2 runs as the deploy user.

**systemd**, if PM2 isn't managing it. The deploy looks for a unit named
`music-backend`, or failing that one whose definition mentions `uvicorn` or
the deploy path.

If a box has neither — the backend is just a process someone started by
hand, which does not survive a reboot — `deploy/install-service.sh` sets up
the systemd option once:

```bash
ssh -i your-key.pem USER@18.219.22.51 'bash -s' < deploy/install-service.sh
```

It derives everything from the account it runs as, grants passwordless sudo
for only that service plus an nginx reload, and refuses to run if PM2
already manages the app — two supervisors on one port means a crash loop.

## What the server is assumed to look like

The deploy script assumes:

1. The repo is already cloned at `DEPLOY_PATH` with `origin` pointing at
   GitHub, and the deploy user can `git fetch` it (public repo, or a deploy
   key on the box).
2. `python3`, `npm` and `git` are installed.
3. The backend runs under systemd as **`music-backend`**. Override with
   `BACKEND_SERVICE` in `remote-deploy.sh` if yours is named differently.
4. The deploy user can `sudo systemctl restart music-backend` **without a
   password** — otherwise the deploy hangs waiting for one. Grant just that:

   ```bash
   echo 'ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl restart music-backend, /bin/systemctl reload nginx' \
     | sudo tee /etc/sudoers.d/deploy
   sudo chmod 440 /etc/sudoers.d/deploy
   ```

5. nginx serves `frontend/dist` and proxies `/api` and `/ws` to the backend.
   If nginx isn't running the script skips the reload rather than failing.

If any of that doesn't match your box, adjust `remote-deploy.sh` — it is
meant to be edited, not treated as fixed.

### A systemd unit, if you don't have one yet

`/etc/systemd/system/music-backend.service`:

```ini
[Unit]
Description=Sheet Music Tracker backend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/music/backend
ExecStart=/home/ubuntu/music/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now music-backend
```

### Out-of-memory kills must not stop the service

PM2's systemd unit defaults to `OOMPolicy=stop`: when the kernel OOM-kills
*any* process in it -- e.g. one OMR worker -- systemd stops the whole unit,
taking PM2 and the backend down with it. The box has a drop-in that makes it
kill only that process:

```bash
sudo mkdir -p /etc/systemd/system/pm2-ec2-user.service.d
printf '[Service]
OOMPolicy=continue
' | sudo tee /etc/systemd/system/pm2-ec2-user.service.d/oom.conf
sudo systemctl daemon-reload
```

Redo this if the box is ever rebuilt. (The backend also stops a worker
itself before free memory runs out -- see `MIN_FREE_MEMORY_GB` in
`backend/app/services/jobs.py` -- so this is the backstop.)

## Why is a sheet slow?

Every OMR job logs what it spent its time on (`backend/app/services/omr_log.py`).
PM2 keeps stderr in `~/.pm2/logs/music-backend-error.log`:

```bash
ssh -i your-key.pem ec2-user@18.219.22.51 'grep -a " omr " ~/.pm2/logs/music-backend-error.log | tail -40'
```

- `job … queued / started / done in … a page / failed after …` — one job's life.
- `page N: 190s (staff net 50s, symbol net 136s, …)` — where a page's time went.
- `staff net: … tiles … on N thread(s)` — the neural nets, which are most of it.
- `job … running …` — a heartbeat every minute with the cores the worker
  is getting, its memory, and the machine's free memory, swap, load and
  **steal**. Steal well above 0% means AWS is withholding CPU — a t3 out of
  CPU credits — and everything slows to a crawl until they build back up.

For reference, a page took ~3¼ minutes on the t3.medium (2 vCPUs, but
both are hyperthreads of one core), against ~2 minutes on two real cores.

## Why did tracking go wrong?

Every live-tracking session is logged to its own file on the server
(`backend/app/services/session_log.py` explains every field): one line per
75 ms of audio with what was heard (`rms`, `fft`, the learned model's
`heard`/`struck` pitches) next to what was sent back (`belief`, `onset`,
`confidence`, and the score's `expected` pitches there), plus the reader's
clicks (`hint`) and the window the model was held to (`viewport`). The
audio itself is never stored.

They live in `~/music-logs/sessions/<date>/`, gzipped once a session ends,
and are kept until deleted by hand (about 1 MB an hour of playing; the
disk is small, so check `df -h /` now and then). The PM2 log has a line
per session saying where its file went.

```bash
ssh -i your-key.pem ec2-user@18.219.22.51 'ls -lt ~/music-logs/sessions/*/ | head'
# A session, one frame a line:
ssh -i your-key.pem ec2-user@18.219.22.51 'zcat ~/music-logs/sessions/2026-10-27/201500-ab12cd34.jsonl.gz' | head -50
```

## HTTPS certificate renewal

The site's Let's Encrypt certificate (scrollsheetmusic.com + www) renews
itself: certbot's `certbot-renew.timer` runs twice a day and renews once the
certificate is within 30 days of expiry, and a deploy hook
(`/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`) reloads nginx so
the new certificate is actually served. Set up, or re-checked, with:

```bash
ssh -i your-key.pem ec2-user@18.219.22.51 'bash -s' < deploy/setup-cert-renewal.sh
```

It's re-runnable and ends with a dry-run renewal against Let's Encrypt's
staging server, so it doubles as a "will renewal actually work" check.

## Trying it without pushing

The workflow has `workflow_dispatch`, so you can run it from the Actions tab
against the current `main` — useful when a deploy failed halfway and you
just want to re-run it. The script is re-runnable: running it twice does the
same thing as running it once.
