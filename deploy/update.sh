#!/bin/bash
#
# Updating Jellyscope from git.
#
# Usage:
#     /opt/jellyscope/deploy/update.sh
#
# Run it as the account that owns the installation - not as root.
#
# What it does:
#     1. backs the database up
#     2. pulls the new version (git pull)
#     3. installs any new dependencies
#     4. restarts and checks that the app came up
#
# Your data and settings are untouched - `data/` and `.env` are not in git.

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$APP_DIR/.venv"
APP_USER="$(id -un)"
OWNER="$(stat -c '%U' "$APP_DIR/run.py")"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }

[[ -d "$APP_DIR/.git" ]] || die "$APP_DIR is not a git repository - update it by hand."

# No updating as root. Besides security there is a practical reason:
# root would leave files in the repository and the virtual environment
# that the app, running under its own account, could no longer touch.
if [[ $EUID -eq 0 && "${ALLOW_ROOT:-}" != "1" ]]; then
    die "Do not run the update as root - it would leave behind files the
     app cannot touch under its own account.

     Run it as the owner of the installation:
         sudo -u $OWNER $0"
fi

# The update runs as whoever owns the installation - **not as root**.
#
# Since version 2.35.2 git refuses to work in a repository owned by
# somebody else than the person running it - it reports a dubious
# ownership error. This script used to run as root and work around that
# with `sudo -u`, which was a source of trouble. Now it is simple: run
# it as the owner of the app and there is nothing to complain about.
if [[ "$APP_USER" != "$OWNER" ]]; then
    die "This installation belongs to '$OWNER', but you are running it as '$APP_USER'.

     Run the update as the owner:
         sudo -u $OWNER $0

     (If you installed Jellyscope under your own account, just run the
     script without sudo:  $0)"
fi

git_() { git -C "$APP_DIR" "$@"; }

# Check right at the start, so a problem shows up here and not in the
# middle of the work.
git_ rev-parse --git-dir >/dev/null 2>&1 || die "Git cannot work with $APP_DIR.

     Most common cause: the folder belongs to somebody else. Unify the
     ownership:
         sudo chown -R $APP_USER $APP_DIR

     If that does not help, allow git to use this repository:
         git config --global --add safe.directory $APP_DIR"

say "Jellyscope - update"
echo "  folder: $APP_DIR"
echo "  user:   $APP_USER"

# --- 1. backup -------------------------------------------------------------

say "Database backup"
# We back up before changing anything. If the update goes wrong, there is
# somewhere to go back to - and it costs a few seconds.
BACKUP_DIR="$APP_DIR/data/before-update"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"

# Which database the app uses is in data/database.json. We read it with
# the same python the app runs on, so nobody has to decipher it with
# sed. The path is relative to the current directory, hence the `cd`
# just below. (When python reads a script from stdin, sys.argv[0] is
# only "-", not a path - deriving the folder from it would not work.)
db_field() {
    "$VENV/bin/python" - "$1" <<'PY' 2>/dev/null || true
import json, pathlib, sys
try:
    data = json.loads(pathlib.Path("data/database.json").read_text(encoding="utf-8"))
    print(data.get(sys.argv[1], ""))
except Exception:
    print("")
PY
}

cd "$APP_DIR"
DB_KIND="$(db_field kind)"
[[ -n "$DB_KIND" ]] || DB_KIND="sqlite"   # no file = the default SQLite

case "$DB_KIND" in

    postgres)
        if ! command -v pg_dump >/dev/null; then
            warn "The database is PostgreSQL, but pg_dump is not installed."
            warn "Install it:  sudo apt install postgresql-client"
            warn "Or update without a backup:  SKIP_BACKUP=1 $0"
            [[ "${SKIP_BACKUP:-}" == "1" ]] \
                || die "I will not update without a backup."
            warn "SKIP_BACKUP=1 - continuing without a backup"
        else
            DUMP="$BACKUP_DIR/jellyscope-$STAMP.sql"
            # The password goes in through an environment variable, not
            # the command line - process arguments are visible to
            # everyone on the machine.
            PGPASSWORD="$(db_field password)" \
            pg_dump -h "$(db_field host)" \
                    -p "$(db_field port)" \
                    -U "$(db_field user)" \
                    -d "$(db_field database)" \
                    --no-password > "$DUMP"
            ok "PostgreSQL backed up to $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1))"
        fi
        ;;

    *)
        shopt -s nullglob
        DB_FILES=("$APP_DIR"/data/*.db)
        shopt -u nullglob

        if [[ ${#DB_FILES[@]} -eq 0 ]]; then
            ok "no database to back up (first run?)"
        else
            for db in "${DB_FILES[@]}"; do
                # sqlite3 .backup makes a consistent snapshot even while
                # the app runs; a plain copy can catch a half-written
                # transaction.
                if command -v sqlite3 >/dev/null; then
                    sqlite3 "$db" ".backup '$BACKUP_DIR/$(basename "$db" .db)-$STAMP.db'"
                else
                    cp "$db" "$BACKUP_DIR/$(basename "$db" .db)-$STAMP.db"
                fi
            done
            ok "SQLite backed up to $BACKUP_DIR"
        fi
        ;;
esac

# We keep the last ten backups, otherwise the folder would grow forever.
ls -1t "$BACKUP_DIR"/* 2>/dev/null | tail -n +11 | xargs -r rm --

# --- 2. pulling the new version --------------------------------------------

say "Pulling the new version"
BEFORE="$(git_ rev-parse --short HEAD)"

if ! git_ diff --quiet; then
    warn "There are local changes in the folder. Show them with:"
    warn "  git -C $APP_DIR diff"
    die "Not updating, so you do not lose them."
fi

git_ pull --ff-only
AFTER="$(git_ rev-parse --short HEAD)"

if [[ "$BEFORE" == "$AFTER" ]]; then
    ok "you already have the newest version ($AFTER)"
else
    ok "$BEFORE → $AFTER"
    git_ log --oneline "$BEFORE..$AFTER" | sed 's/^/    /'
fi

# --- 3. dependencies -------------------------------------------------------

say "Dependencies"

# Is the environment built on a new enough Python? The app needs 3.10 or
# newer since FastAPI dropped 3.9. Without this check pip would be the
# one to complain, with a message that never names the cause:
#
#     ERROR: Could not find a version that satisfies the requirement fastapi
#
# It reads as "the package does not exist", not as "your Python is too
# old" - and the update would stop there with no idea what to do next.
if ! "$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    OLD="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
    die "The virtual environment is built on Python $OLD, the app needs 3.10+.

     Nothing has been installed, the app keeps running on what it has.

     Add a newer Python NEXT TO the current one - do not upgrade the
     whole system over this and do not remove the old one, other things
     on the machine may depend on it:

         sudo apt install python3.11 python3.11-venv

     Then let install.sh rebuild the environment - it notices an old
     .venv, throws it away and builds a new one. Your database and .env
     stay where they are:

         bash deploy/install.sh"
fi

# `python -m pip`, not the .venv/bin/pip launcher - it may not exist and
# it carries a hard-coded path that breaks when the folder moves.
"$VENV/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"
ok "up to date"

# --- 4. restart and check --------------------------------------------------

# Which process manager actually holds the service? We do not ask what is
# installed on the machine but who knows about Jellyscope - a server can
# have both systemd and supervisord, and restarting the wrong half would
# do nothing.
say "Restart"

# The user service comes first: it runs as us, so we can restart it
# without root. Only when there is none do we look at the system service
# and supervisord - those need sudo.
MANAGER=""
if command -v systemctl >/dev/null && systemctl --user cat jellyscope.service &>/dev/null; then
    MANAGER="systemd-user"
elif command -v systemctl >/dev/null && systemctl cat jellyscope.service &>/dev/null; then
    MANAGER="systemd"
elif command -v supervisorctl >/dev/null \
     && { [[ -f /etc/supervisor/conf.d/jellyscope.conf ]] \
          || sudo -n supervisorctl status jellyscope &>/dev/null; }; then
    # We check whether the config exists first - `sudo -n` always fails
    # when sudo wants a password, which is normal when running as an
    # ordinary user. Without this first condition the script would not
    # find supervisord, could not restart, and would leave the old
    # version running.
    MANAGER="supervisor"
fi

case "$MANAGER" in
    systemd-user)
        systemctl --user restart jellyscope
        sleep 5
        STATUS="$(systemctl --user is-active jellyscope || true)"
        echo "  systemd (user service): $STATUS"
        RUNNING=$([[ "$STATUS" == "active" ]] && echo 1 || echo 0)
        LOG_CMD="journalctl --user -u jellyscope -n 25 --no-pager"
        RESTART_CMD="systemctl --user restart jellyscope"
        ;;
    systemd)
        sudo systemctl restart jellyscope
        sleep 5
        STATUS="$(systemctl is-active jellyscope || true)"
        echo "  systemd: $STATUS"
        RUNNING=$([[ "$STATUS" == "active" ]] && echo 1 || echo 0)
        LOG_CMD="sudo journalctl -u jellyscope -n 25 --no-pager"
        RESTART_CMD="sudo systemctl restart jellyscope"
        ;;
    supervisor)
        sudo supervisorctl restart jellyscope >/dev/null
        sleep 5
        STATUS="$(sudo supervisorctl status jellyscope || true)"
        echo "  supervisord: $STATUS"
        RUNNING=$(grep -q RUNNING <<<"$STATUS" && echo 1 || echo 0)
        LOG_CMD="tail -n 25 $APP_DIR/data/logs/err.log"
        RESTART_CMD="sudo supervisorctl restart jellyscope"
        ;;
    *)
        warn "The jellyscope service is managed by neither systemd nor"
        warn "supervisord (or I cannot reach it without a password)."
        warn "The code is updated, but you have to restart it yourself."
        RUNNING=-1
        ;;
esac

if [[ $RUNNING -eq 0 ]]; then
    echo
    warn "The app did not come up. Last 25 lines of the log:"
    eval "$LOG_CMD" 2>/dev/null || true
    echo
    warn "Going back to the previous version:"
    warn "  git -C $APP_DIR reset --hard $BEFORE"
    warn "  $RESTART_CMD"
    die "The update failed."
fi

if [[ $RUNNING -eq 1 ]]; then
    PORT="$(grep -E '^PORT=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2 || echo 8097)"
    if command -v curl >/dev/null && curl -fsS -o /dev/null "http://127.0.0.1:${PORT:-8097}/setup"; then
        ok "the app answers"
    fi
    printf '\n  Done. Missing database columns were added automatically at startup.\n\n'
    exit 0
fi

# The restart did not happen. This state is nastier than it looks and is
# worth spelling out:
#
# Templates are read from files on every request, but the Python code
# stays in the process's memory. After a `git pull` without a restart
# you get NEW templates over OLD code - and a page that asks for a
# variable the old code does not send dies with "Internal Server
# Error". It looks like a bug in the app when all it needs is a restart.
cat <<EOF

════════════════════════════════════════════════════════════
  NOTE: the code is updated, but the app RUNS THE OLD VERSION

  I did not find what manages it, so I did not restart it.
  Until you do, some pages may report "Internal Server Error" -
  new templates over old code.

  Restart it the way you run it:

      sudo systemctl restart jellyscope       # system service
      systemctl --user restart jellyscope     # user service
      sudo supervisorctl restart jellyscope   # supervisord
════════════════════════════════════════════════════════════

EOF
