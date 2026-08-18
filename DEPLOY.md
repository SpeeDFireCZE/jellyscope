# Deploying Jellyscope on Linux

Getting the app onto a server and keeping it running. What the app *does*
is in [README.md](README.md).

Needs Python 3.10+, ~200 MB of disk and a reachable Jellyfin. No database
server — SQLite is the default. `ffmpeg` is only used by the ffprobe data
source, and the installer takes care of it.

---

## Install

```bash
sudo mkdir -p /opt/jellyscope
sudo chown "$USER" /opt/jellyscope
git clone https://github.com/SpeeDFireCZE/jellyscope.git /opt/jellyscope
cd /opt/jellyscope
bash deploy/install.sh
```

`/opt/jellyscope` is what the rest of this document and the service
templates assume. Another folder works — the installer writes the real
paths into the `.ready` configs — but avoid `/home`: the systemd unit
ships with `ProtectHome=true`, and a service that cannot see its own
folder starts and dies with a puzzling `ModuleNotFoundError`.

What the script installs — and nothing else, in particular never
`apt upgrade`:

| Package | When | If it fails |
|---|---|---|
| `python3`, `python3-venv` | only when missing | The script stops — without them there is nothing to run. |
| `ffmpeg` | only when `ffprobe` is missing | Just a warning; the app then takes technical data from Jellyfin. Skip it up front with `SKIP_FFMPEG=1 bash deploy/install.sh` — it is a large package and only the ffprobe data source uses it. |
| `requirements.txt` | into `.venv`, always | The script stops. |
| `psycopg[binary,pool]` | into `.venv`, always | Just a warning. Some platforms (Alpine/musl) have no prebuilt package; SQLite works without it, PostgreSQL does not. |

It also creates `.venv`, writes `.env` with a generated `SECRET_KEY` and
prepares configs for systemd and supervisord.

It does **not** install a process manager or a web server. It detects
which of the two process managers you already have and prints the right
commands; a web server is only needed for HTTPS — see [Reverse
proxy](#reverse-proxy).

Then pick one process manager. The `.ready` files already carry the real
paths of wherever you installed it, so `/opt/jellyscope` below is only
what the plain templates assume:

```bash
# systemd, user service (no root)
mkdir -p ~/.config/systemd/user
cp deploy/jellyscope.user.service.ready ~/.config/systemd/user/jellyscope.service
systemctl --user daemon-reload && systemctl --user enable --now jellyscope
sudo loginctl enable-linger "$USER"      # survives logout and reboot

# systemd, system service
sudo cp deploy/jellyscope.service.ready /etc/systemd/system/jellyscope.service
sudo systemctl daemon-reload && sudo systemctl enable --now jellyscope

# supervisord
sudo cp deploy/jellyscope.conf.ready /etc/supervisor/conf.d/jellyscope.conf
sudo supervisorctl reread && sudo supervisorctl update
```

Open `http://<server>:8097`, create the administrator account, fill in the
Jellyfin address and API key in **Settings → Jellyfin connection**.

---

## `.env`

The only file you edit by hand. Everything else is configured in the app.

| Key | Default | Meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | `0.0.0.0` makes it reachable from the network. |
| `PORT` | `8097` | The port to listen on. |
| `SECRET_KEY` | *(empty)* | Signs login cookies. Empty = generated into `data/secret_key`. |
| `DATABASE_PATH` | `data/jellyscope.db` | SQLite file. Ignored once you switch to PostgreSQL. |
| `SECURE_COOKIES` | *(off)* | Set to `1` behind HTTPS. Without working HTTPS nobody can sign in. |
| `FORWARDED_ALLOW_IPS` | *(empty)* | The proxy address, e.g. `127.0.0.1`. |
| `JELLYFIN_URL`, `JELLYFIN_API_KEY` | | Only pre-fill the form on the first start; afterwards the values come from the database. |

Restart after every change (`systemctl --user restart jellyscope`, or the
**Restart** button in Settings).

---

## Updates

```bash
cd /opt/jellyscope
bash deploy/update.sh
```

Pulls the new version, reinstalls the dependencies from
`requirements.txt` and restarts the service — it installs no system
packages. `.env` and `data/` are untouched. Back the database up first — see
[Backups](#backups).

---

## Reverse proxy

Only for HTTPS or a hostname instead of a port. Examples in `deploy/`:
`nginx.conf.example`, `Caddyfile.example`, `apache.conf.example`.

Set `SECURE_COOKIES=1` and `FORWARDED_ALLOW_IPS=127.0.0.1` in `.env`.

The second one matters beyond logs: without it every request looks like it
comes from the proxy, so one failed-login block locks out everybody.

On the proxy side, two things are easy to miss — both are already in the
example configs:

- **Upload limit at least 200 MB** — that is what the app accepts for a
  history import. A lower limit means the proxy rejects the file and the
  error comes from nginx, not from Jellyscope.
  (`client_max_body_size 200M`, `LimitRequestBody 209715200`; Caddy needs
  nothing.)
- **Pass the headers** `Host`, `X-Real-IP`, `X-Forwarded-For` and
  `X-Forwarded-Proto` — without the last one the app builds `http://`
  links on an HTTPS site.

---

## Access to media files

Only when the data source is **ffprobe** (**Settings → Technical data source**);
`install.sh` has already put `ffmpeg` in place for you. Jellyscope then
reads the files itself and has to see the same paths Jellyfin does. When
they differ — typically Jellyfin in Docker — fill in **Path mapping** in
the same section:

```json
[{"from": "/media", "to": "/mnt/media"}]
```

Read access is enough; the app never writes to media files.

---

## PostgreSQL

Optional. **Settings → Database**: fill in the connection → *Test
connection* → *Transfer data* → restart.

```sql
CREATE USER jellyscope WITH PASSWORD '...';
CREATE DATABASE jellyscope OWNER jellyscope;
```

`install.sh` installs the `psycopg` driver for you. When it could not
(it says so, and SQLite keeps working), add it by hand into the same
virtualenv the app runs in:

```bash
/opt/jellyscope/.venv/bin/python -m pip install "psycopg[binary,pool]"
```

Backups use `pg_dump`, which is **not** part of the installation — it
comes with the PostgreSQL client tools (`sudo apt install
postgresql-client`). It can only dump a server of the same or older
version, so the client must be at least as new as the server. Without a
usable `pg_dump` the app falls back to its own export; that is plain SQL
and restores the same way.

---

## Backups

**Settings → Scheduled tasks**: pick a folder and a time. Runs daily,
keeps as many copies as you set. Each one can be downloaded, deleted or
restored from the same page; restoring saves the current state first.

Two files belong in your own backups next to the database:

- `.env`
- `data/secret_key` (when `.env` has no `SECRET_KEY`)

Without them everyone has to sign in again after a restore.

---

## When it will not start

```bash
systemctl --user status jellyscope       # or: sudo systemctl status jellyscope
journalctl --user -u jellyscope -n 50
# supervisord:
sudo supervisorctl status jellyscope && sudo tail -n 50 /var/log/supervisor/jellyscope.log
```

The app's own log is `data/logs/jellyscope.log`, readable from
**Settings → Application log** with secrets masked.

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError` | The process manager runs the system Python. Use the full path to `.venv/bin/python`. |
| Nothing on `:8097` | `HOST=127.0.0.1` and you are connecting from elsewhere. |
| Signed out after every restart | No `SECRET_KEY` and `data/` is not writable. |
| Nobody can sign in | `SECURE_COOKIES=1` without working HTTPS. |
| Daily totals off by hours | Wrong `TZ` in the service config. |

---

## Manual installation

Supervisord is not installed by the script either way; add
`supervisor` to the line below only if you have no systemd.

```bash
sudo apt update && sudo apt install python3 python3-venv ffmpeg
sudo mkdir -p /opt/jellyscope && sudo chown "$USER" /opt/jellyscope
git clone https://github.com/SpeeDFireCZE/jellyscope.git /opt/jellyscope && cd /opt/jellyscope

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install "psycopg[binary,pool]"   # only for PostgreSQL
cp .env.example .env
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))" >> .env

# one of the two, not both
sudo cp deploy/jellyscope.service /etc/systemd/system/
sudo cp deploy/jellyscope.conf /etc/supervisor/conf.d/
```

Both templates assume `/opt/jellyscope` and a `jellyscope` user — adjust
the paths and `user=` if you installed elsewhere. Four settings in them
are not obvious:

| Setting | Why |
|---|---|
| full path to `.venv/bin/python` | Process managers do not know your `PATH`. |
| `TZ=Europe/Prague` | Daily totals are grouped by local time. |
| `stopasgroup=true` / `KillMode=mixed` | Stops `ffprobe` and `pg_dump` children too. |
| `autorestart=true` / `Restart=always` | Comes back after a crash. |

---

## Three notes

**The Restart button** in Settings replaces the process (`execv`), so it
works without a process manager. It restarts Jellyscope, never Jellyfin.

**One process only.** The scheduler, the collector and the "a task is
running" flag live in memory, so two workers would run every task twice.
`run.py` starts one worker on purpose.

**No SQLite on a network drive** (NFS, SMB) — file locking is unreliable
there and the database can get corrupted. Local disk, or PostgreSQL.
