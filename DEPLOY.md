# Deploying Jellyscope on Linux

Getting the app onto a server and keeping it running. What the app *does*
is in [README.md](README.md).

Needs Python 3.10+, ~200 MB of disk and a reachable Jellyfin. No database
server — SQLite is the default. `ffmpeg` is only used by the ffprobe data
source, and the installer takes care of it.

Prefer containers? Jump to [Docker](#docker) — one `.env`, one
`docker compose up -d`.

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
Jellyfin address and API key in **Settings → Jellyfin**.

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

## Docker

Everything is configured in one place — the same `.env` the app uses
without Docker. `docker compose` reads that file itself, so changing the
port is changing one line and starting again.

```bash
git clone https://github.com/SpeeDFireCZE/jellyscope.git
cd jellyscope
cp .env.example .env

# SECRET_KEY is the only value the container refuses to start without.
# Rewrite the line, do not append a second one: compose takes the last
# SECRET_KEY in the file, the application the first.
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')|" .env

docker compose up -d
```

Then open <http://localhost:8097> and create the first account.

**Where to change what** — all of it in `.env`:

| | |
|---|---|
| `PORT` | the port, inside the container and out. One value, both places. |
| `BIND` | which address on the host publishes it. `127.0.0.1` behind a reverse proxy (the default), `0.0.0.0` without one. |
| `DATA_DIR` | where the data folder lives on the host. Default `./data`. |
| `SECRET_KEY` | signs the login cookie. **Required** — an empty one is a hole, not a default, so the container will not start without it. |
| `TZ` | the container's time zone. Without it it runs in UTC and the evening peak in the charts moves by a couple of hours. |
| `FFPROBE` | `0` builds an image without ffmpeg — about 250 MB smaller, and the technical data is limited to what Jellyfin reports. |
| `PGDUMP` | `0` builds an image without `pg_dump` — about 30 MB smaller. Only affects PostgreSQL backups, which then use the app's own export. |
| `SECURE_COOKIES`, `FORWARDED_ALLOW_IPS` | behind HTTPS, same meaning as anywhere else — see [Reverse proxy](#reverse-proxy). |
| `JELLYSCOPE_DEMO` | `1` runs the container as a public demo: everything can be looked at, nothing can be saved. Off unless set. Not a substitute for the login on a normal installation. |

**Data.** Everything worth keeping — the database, the log, the image
cache, backups — is in the folder mounted at `/app/data`. `docker compose
down` does not touch it; a backup is a copy of that folder.

**Who owns that folder.** The application runs as UID 10001, while the
folder on the host belongs to whoever created it — on a first `docker
compose up` that is Docker itself, as root, and the application could not
write a single byte into it. The container therefore starts as root, and
drops the privileges before the application starts; the application itself
never runs as root.

While it is still root, it corrects the owner of that folder — **but only
when the folder is empty**, which is exactly the one Docker created a
moment earlier. There is nothing in it to overwrite, so a first start
needs nothing from you.

A folder that already has something in it is left alone, even when the
owner is wrong: those are your files, and changing them behind your back
is not the container's business. The application starts anyway and every
page says which folder it cannot write to and what to run —

```bash
sudo chown -R 10001:10001 ./data
docker compose restart
```

Setting your own `user:` in `docker-compose.yml` skips all of this: the
ownership is then yours to manage, and the same page appears if the
application cannot reach the folder.

**Only `data/` — never the project folder.** `sudo chown -R 10001:10001 .`
looks like the same command and breaks the next `git pull` and the next
`scp`: the files then belong to a user that does not exist on the host, so
your own account can no longer write them, and git refuses a repository it
sees as somebody else's (*detected dubious ownership*). Keep the two apart:

```bash
sudo chown -R $USER:$USER /opt/jellyscope        # the code is yours
sudo chown -R 10001:10001 /opt/jellyscope/data   # the data is the container's
```

`data/` is in `.gitignore`, so git never touches it and the split holds.

**Or hand the container your own account instead.** Add `user:` to
`docker-compose.yml` and the application runs as you, so `data/` can stay
yours and no `chown` is needed at all:

```yaml
services:
  jellyscope:
    user: "1000:1000"       # id -u : id -g
```

The container then changes no ownership whatsoever; a folder it cannot
write to is reported on the page as usual.

**Reading files with ffprobe.** The container has to see the media. Mount
the library **read-only** and map the paths in *Settings → Data
collection* — the path Jellyfin reports is not the path inside the
container:

```yaml
    volumes:
      - ./data:/app/data
      - /srv/media:/media:ro     # :ro on purpose - Jellyscope never writes there
```

**Backups.** The app notices it is in a container and sets the folder
itself, to `/app/data/backups` — on the host that is `./data/backups`, so
a backup is a file you can pick up without going into the container. It
only fills it in when nothing is set; a path you chose yourself is never
overwritten.

Why it bothers: anything outside a mounted folder is written *into* the
container and disappears with the next build, while the task keeps
reporting success for months. That is worse than no backup at all,
because you think you have one. To keep them somewhere else entirely,
mount that somewhere as a second volume and point the setting at it.

The backup task itself is still off by default — turn it on in *Settings
→ Tasks and backups*.

PostgreSQL is dumped with `pg_dump`, which the image carries
(`PGDUMP=0` leaves it out and the app falls back to its own export —
it works, `pg_dump` is just better at it). SQLite backs itself up and
needs nothing.

**Updates.** `git pull && docker compose up -d --build`. Missing database
columns are added at startup, the same as anywhere else. The **Update and
restart** button in the app is for installations from git — in a container
it would update the code inside a layer that disappears with the next
build, so it stays off.

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

Only when the data source is **ffprobe** (**Settings → Data collection**);
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

**Settings → Tasks and backups**: pick a folder and a time. Runs daily,
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
# The example already has an empty SECRET_KEY= line - rewrite it rather
# than adding a second one, or the empty one wins.
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')|" .env

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
| `stopasgroup=true` / `KillMode=control-group` | Stops `ffprobe` and `pg_dump` children too. |
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
