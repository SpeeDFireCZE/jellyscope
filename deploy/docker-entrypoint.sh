#!/bin/sh
#
# Starting Jellyscope inside the container.
#
# WHY THIS FILE EXISTS
#
# The data folder is mounted from the host, and on the host it belongs to
# whoever created it - which on a first `docker compose up` is Docker
# itself, as root. The application runs as UID 10001, so SQLite fails on
# "unable to open database file" before anything else happens. Left to
# the user, the very first start of a fresh installation ends in an error
# that has to be fixed on the host, in a place the person was never told
# to look.
#
# So the container starts as root, and then drops the privileges: the
# application itself never runs as root.
#
# THE ONE THING IT CHANGES, AND WHY ONLY THAT ONE
#
# The ownership is corrected **only when the folder is empty** - which is
# exactly the case Docker itself created a moment ago. There is nothing in
# it to overwrite, so nobody's files change and nobody has to be asked.
#
# A folder with something in it is left alone, even when the owner is
# wrong. Those are somebody's files, put there for reasons this script
# cannot see - a rewrite behind their back is not ours to make. The
# application then starts anyway and says on its own page which folder it
# cannot write to and what to run; see jellyscope/porucha.py.
#
# Anyone who sets their own `user:` in docker-compose.yml skips all of
# this - then their own ownership applies from the start.
#
set -e

APP_UID=10001
APP_GID=10001
DATA=/app/data

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA"

    vlastnik="$(stat -c '%u' "$DATA")"
    if [ "$vlastnik" != "$APP_UID" ]; then
        if [ -z "$(ls -A "$DATA" 2>/dev/null)" ]; then
            # Empty: Docker made it seconds ago and there is nothing in it
            # that could be somebody's. One directory, not -R.
            echo "Jellyscope: $DATA is empty and belongs to UID $vlastnik - it is now the application's ($APP_UID)."
            chown "$APP_UID:$APP_GID" "$DATA" || echo "Jellyscope: the owner could not be changed." >&2
        else
            # Not empty: somebody's files. We do not touch them - the
            # application will say what to run, on its own page.
            echo "Jellyscope: $DATA belongs to UID $vlastnik, the application runs as $APP_UID." >&2
            echo "Jellyscope: not touching a folder that has something in it." >&2
            echo "Jellyscope: if it cannot be written to, on the host run:" >&2
            echo "Jellyscope:     sudo chown -R $APP_UID:$APP_GID ./data" >&2
        fi
    fi

    # And from here on as the application. setpriv drops the root
    # privileges and hands over; `exec` keeps the process as PID 1, so
    # the signals from `docker stop` still arrive where they should.
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups "$@"
fi

# Not root (an explicit `user:` in compose). Ownership is not ours to
# change any more - and if the application cannot write to the folder,
# it says which folder and why, on a page of its own.
exec "$@"
