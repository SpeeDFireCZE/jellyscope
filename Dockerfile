# ---------------------------------------------------------------------------
# Jellyscope in a container.
#
#     docker compose up -d
#     http://localhost:8097
#
# Worth knowing up front:
#
#   * Data (database, log, image cache, backups) lives in a volume mounted
#     at /app/data. Without it `docker compose down` would wipe the whole
#     history - it is the one thing in the container you can lose.
#   * When technical data comes from ffprobe, the container has to see the
#     files: mount the library read-only and in Settings map the paths as
#     Jellyfin sees them to the paths in the container.
#   * Keep backups in /app/data (for example /app/data/backups). Anything
#     outside the mounted folders is stored inside the container and is
#     gone with the next build.
#   * Jellyfin is only ever read from. The container needs no extra rights.
# ---------------------------------------------------------------------------

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffprobe (part of ffmpeg) reads technical data straight from the files -
# without it the app knows only what Jellyfin reports. It is a build-time
# choice:
#
#     docker compose build --build-arg FFPROBE=0
#
# saves ~250 MB of image and leaves the data source to Jellyfin.
ARG FFPROBE=1

# pg_dump (postgresql-client) is needed only for PostgreSQL backups.
# Without it the app backs up with its own export - that works, but
# pg_dump takes a consistent snapshot and gets dependency order and indexes
# right, so ~30 MB of image is worth it. SQLite does not use it at all.
ARG PGDUMP=1

RUN if [ "$FFPROBE" = "1" ] || [ "$PGDUMP" = "1" ]; then \
        apt-get update \
        && if [ "$FFPROBE" = "1" ]; then \
               apt-get install --no-install-recommends -y ffmpeg; \
           fi \
        && if [ "$PGDUMP" = "1" ]; then \
               apt-get install --no-install-recommends -y postgresql-client; \
           fi \
        && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

# Dependencies on their own, as the first layer: until requirements.txt
# changes, the next build does not install them again.
COPY requirements.txt .
# pip itself first: the base image tends to carry the pip it was released
# with, and pip has security fixes of its own (unpacking archives).
RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir -r requirements.txt

COPY . .

# The app runs as its own user with a fixed UID - never as root. A fixed
# number so that the owner of the mounted folder can be set on the host
# too.
RUN useradd --create-home --uid 10001 jellyscope \
    && mkdir -p /app/data \
    && chown -R jellyscope:jellyscope /app

# setpriv drops the root privileges in the entrypoint (see below). The
# Debian base usually has it, but we do not rely on that: a missing tool
# would show only on somebody else's server, as the app running as root.
RUN command -v setpriv >/dev/null 2>&1 \
    || (apt-get update \
        && apt-get install --no-install-recommends -y util-linux \
        && rm -rf /var/lib/apt/lists/*)

# Nobody outside can reach 127.0.0.1 inside the container.
# JELLYSCOPE_DOCKER tells the app it runs in this image. The app then sets
# its backup folder to /app/data/backups - inside the mounted folder,
# which can be reached from the host as well.
ENV HOST=0.0.0.0 \
    PORT=8097 \
    DATABASE_PATH=data/jellyscope.db \
    JELLYSCOPE_DOCKER=1

EXPOSE 8097

# A container that runs but does not answer looks just like a healthy one
# from outside. /setup answers even without signing in, so it suits better
# than the home page.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8097/setup', timeout=4)"

# The container starts as root for ONE thing only: to set the owner of the
# mounted data folder. On the host that folder belongs to whoever created
# it - on the first `docker compose up` that is root - and the app could
# not get into it. The entrypoint fixes that and drops the privileges
# right away; the app itself runs as jellyscope (UID 10001).
#
# Through `sh` on purpose: the repository is also developed on Windows,
# where git does not keep the executable bit, and a missing "x" would
# bring the container down.
ENTRYPOINT ["/bin/sh", "/app/deploy/docker-entrypoint.sh"]
CMD ["python", "run.py"]
