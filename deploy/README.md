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
| `DEPLOY_HOST` | `3.149.2.249` |
| `DEPLOY_PATH` | `~/music` for whoever `DEPLOY_USER` is — only set this if the checkout lives somewhere else |

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
ssh-keyscan -H 3.149.2.249
```

and paste the output into the `SSH_KNOWN_HOSTS` secret.

## How the backend gets restarted

The deploy handles two setups and picks whichever the box actually uses:

**PM2** (what `3.149.2.249` uses). The backend runs as a PM2 app named
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
ssh -i your-key.pem USER@3.149.2.249 'bash -s' < deploy/install-service.sh
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

## Trying it without pushing

The workflow has `workflow_dispatch`, so you can run it from the Actions tab
against the current `main` — useful when a deploy failed halfway and you
just want to re-run it. The script is re-runnable: running it twice does the
same thing as running it once.
