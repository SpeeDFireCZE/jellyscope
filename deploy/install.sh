#!/bin/bash
#
# Installing Jellyscope on Linux.
#
# Usage (the usual place is /opt/jellyscope):
#     sudo mkdir -p /opt/jellyscope
#     sudo chown -R $USER:$USER /opt/jellyscope
#     git clone https://github.com/YOUR-NAME/jellyscope.git /opt/jellyscope
#     /opt/jellyscope/deploy/install.sh
#
# The folder can be anywhere - all that matters is that it belongs to
# whoever runs the script. /opt is where software outside the package
# manager belongs; a home directory works just as well.
#
# The script can be run repeatedly - whatever already exists is left
# alone. That is why it is also used after `git pull`, when a new
# dependency has appeared.
#
# Optional tweaks:
#     PORT=8097 /opt/jellyscope/deploy/install.sh
#
# ---------------------------------------------------------------------------
# IT RUNS AS WHOEVER RUNS IT
#
# The script **creates no user** and changes no file ownership. The app
# will run under the account you run the installation from, with exactly
# its permissions - no more, no less.
#
# Want a dedicated system account? Create it and run the script as it:
#
#     sudo useradd --system --home /opt/jellyscope --shell /usr/sbin/nologin jellyscope
#     sudo chown -R jellyscope:jellyscope /opt/jellyscope
#     sudo -u jellyscope /opt/jellyscope/deploy/install.sh
#
# It is safer (an attacker who gets into the app gets only that
# account's permissions), but it is not required. Under your own account
# the app can reach everything you can.
#
# THE SCRIPT DOES NOT NEED ROOT - except for one thing: installing system
# packages when they are missing. It reaches for sudo on its own there
# and asks for the password.
#
# What the script does NOT do: it does not keep the app running and it
# does not install a process manager. Every system has a different one -
# systemd, supervisord, OpenRC, runit - and installing a foreign process
# manager onto a server that already has one is the last thing an
# installer should do. At the end it only detects what the machine has
# and prints a ready-made config for it.

# -e  stop at the first error
# -u  an unknown variable is an error, not an empty string
# -o pipefail  an error in the middle of a pipe must not be swallowed
#
# Without these three the script would happily carry on after an error
# and leave a half-finished installation behind - which is worse than a
# clean failure.
set -euo pipefail

# Remember whether the user EXPLICITLY passed a port and a host.
#
# It makes a difference: when they do, it should be written into an
# existing .env as well. When they do not, the default 8097 must not
# overwrite a port somebody set there earlier - that would be worse
# than doing nothing.
PORT_GIVEN="${PORT+yes}"
HOST_GIVEN="${HOST+yes}"

PORT="${PORT:-8097}"

# Which address the app listens on.
#
# 127.0.0.1 means "this machine only" and is the right default: there is
# usually a reverse proxy in front. When you need to reach it directly
# from another computer on the network, you want 0.0.0.0:
#
#     HOST=0.0.0.0 PORT=38283 ./deploy/install.sh
HOST="${HOST:-127.0.0.1}"

# The project root comes from where the script lives, not from the
# current directory - so it works no matter where you run it from.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$APP_DIR/.venv"

# Who will own and run the app.
#
# When you run the script through sudo we want that person's name, not
# "root" - otherwise the whole installation would belong to root and you
# could not touch it afterwards. SUDO_USER is exactly for this.
APP_USER="${SUDO_USER:-$(id -un)}"
APP_GROUP="$(id -gn "$APP_USER")"

# Logs stay in the project folder. Only root could write to /var/log and
# we would have to deal with permissions - exactly what we are avoiding.
# systemd writes to the journal anyway, so this is only for supervisord.
LOG_DIR="${LOG_DIR:-$APP_DIR/data/logs}"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }

# A helper for the one thing that may need root: installing packages.
# When we already are root, sudo is not called at all.
as_root() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null; then
        sudo "$@"
    else
        return 1
    fi
}

# ---------------------------------------------------------------------------

# We will not set the app up as root.
#
# This is not squeamishness. Jellyscope talks over the network, reads
# other people's video files and runs ffprobe over files whose contents
# it does not control. If somebody got in while it ran as root, they
# would own the whole machine. Under a normal account they get only that
# account's permissions - a big difference.
#
# `sudo ./install.sh` is fine: sudo sets SUDO_USER, so we know who is
# behind it and set things up for them. You only end up here when you
# are logged in as root directly (common on home servers over SSH).
if [[ "$APP_USER" == "root" && "${ALLOW_ROOT:-}" != "1" ]]; then
    die "You are logged in as root, so the app would run as root too.
     You do not want that: if somebody got in, they would own the
     whole machine.

     Pick one of two ways:

     a) Run the installation under your normal account:
            su - your_account
            $0

     b) Create a dedicated system account (safer, a few more steps):
            useradd --system --home $APP_DIR --shell /usr/sbin/nologin jellyscope
            chown -R jellyscope:jellyscope $APP_DIR
            su -s /bin/bash -c '$0' jellyscope

     In a container, where isolation comes from elsewhere, the check
     can be turned off:
            ALLOW_ROOT=1 $0"
fi

[[ -f "$APP_DIR/run.py" ]] || die "No run.py in $APP_DIR - is this really the Jellyscope project?"

# The folder must belong to whoever the app will run as.
#
# Typically: you clone into /opt/jellyscope with sudo, so it belongs to
# root while the app should run as you. Instead of a "fix your
# permissions" message we simply fix it - it is a well-defined operation
# on a folder we know is Jellyscope (run.py was checked just above).
OWNER="$(stat -c '%U' "$APP_DIR")"
if [[ "$OWNER" != "$APP_USER" || ! -w "$APP_DIR" ]]; then
    say "Folder ownership"
    echo "  $APP_DIR belongs to '$OWNER', the app will run as '$APP_USER'"

    if as_root chown -R "$APP_USER:$APP_GROUP" "$APP_DIR" 2>/dev/null; then
        ok "folder handed over to $APP_USER:$APP_GROUP"
    else
        die "Could not change the owner. Do it by hand and run the script again:
         sudo chown -R $APP_USER:$APP_GROUP $APP_DIR"
    fi
fi

# File permissions are left as they are (typically 755 for folders, 644
# for files - that is the default umask). No blanket `chmod -R 755` on
# purpose: it would also make .env readable, and that holds the cookie
# signing key. It stays owner-only; install.sh gives it 600.

say "Jellyscope - installation"
echo "  folder:  $APP_DIR"
echo "  user:    $APP_USER  (the app will run as this account)"
echo "  address: $HOST:$PORT"

# --- 1. system packages ----------------------------------------------------

say "System packages"
# We install only what the app cannot start without. A process manager
# is not part of that - the server already has one.
MISSING=()
command -v python3 >/dev/null || MISSING+=(python3)

# We test ensurepip, not venv - and that difference is what broke this
# on Ubuntu.
#
# The `venv` module is part of the base installation on Debian and
# Ubuntu, so `import venv` always succeeds. But without the python3-venv
# package it is missing **ensurepip**, the part that installs pip into a
# new environment. `python3 -m venv` then gets as far as creating the
# directories and the python symlink before it fails - leaving a
# half-made environment with no pip behind. Next time the "bin/python
# exists" check passes and the installation dies a few lines later on
# "pip: command not found".
python3 -c "import ensurepip" 2>/dev/null || MISSING+=(python3-venv)

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "  missing: ${MISSING[*]}"
    [[ $EUID -eq 0 ]] || echo "  (I will reach for sudo to install packages)"

    # Package names differ between distributions, so we do not try to
    # translate everything - only what we need. When we cannot install,
    # it is better to say exactly what to run than to keep guessing.
    if command -v apt-get >/dev/null; then
        echo "  installing with apt-get"
        as_root apt-get update -qq || warn "apt-get update failed, trying to install anyway"
        as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}" \
            || warn "apt-get install ended with an error"

        # The python3-venv metapackage may not match the Python version
        # you have (common on newer Ubuntu) - try the numbered one too.
        if ! python3 -c "import ensurepip" 2>/dev/null && command -v python3 >/dev/null; then
            PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "")"
            if [[ -n "$PYVER" ]]; then
                echo "  also trying: python${PYVER}-venv"
                as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "python${PYVER}-venv" || true
            fi
        fi

    elif command -v dnf >/dev/null; then
        echo "  installing with dnf"
        as_root dnf install -y -q python3 python3-pip || warn "dnf install ended with an error"
    elif command -v yum >/dev/null; then
        echo "  installing with yum"
        as_root yum install -y -q python3 python3-pip || warn "yum install ended with an error"
    elif command -v pacman >/dev/null; then
        echo "  installing with pacman"
        as_root pacman -Sy --noconfirm --needed python python-pip || warn "pacman ended with an error"
    elif command -v zypper >/dev/null; then
        echo "  installing with zypper"
        as_root zypper --non-interactive install python3 python3-pip || warn "zypper ended with an error"
    elif command -v apk >/dev/null; then
        echo "  installing with apk"
        as_root apk add --quiet python3 py3-pip || warn "apk ended with an error"
    else
        warn "no known package manager found"
    fi
fi

# That was our best effort. Now we only check the result, and when it
# did not work, say exactly what to install - instead of failing a few
# steps later on something unrelated to the cause.
if ! command -v python3 >/dev/null; then
    die "Python 3 is not in the system and could not be installed.
     Debian/Ubuntu:  sudo apt install python3 python3-venv
     Fedora/RHEL:    sudo dnf install python3 python3-pip
     Arch:           sudo pacman -S python python-pip
     Alpine:         sudo apk add python3 py3-pip
     Then run the script again."
fi

if ! python3 -c "import ensurepip" 2>/dev/null; then
    PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "3")"
    die "Python is here but has no ensurepip - without it a virtual
     environment cannot be created. Install:
         sudo apt install python3-venv
     or, if that does not help:
         sudo apt install python${PYVER}-venv
     Then run the script again."
fi

ok "python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'), venv, ensurepip"

# --- new enough Python? ----------------------------------------------------
#
# The app needs Python 3.10 or newer. Not a whim of ours: FastAPI stopped
# supporting 3.9 with version 0.116, and the version we pin is newer than
# that (see requirements.txt - the older pins had 21 known security holes).
#
# Ubuntu 20.04 ships Python 3.8 by default and the installation there
# ends with a message that never names the cause:
#
#   ERROR: Could not find a version that satisfies the requirement fastapi==0.141.1
#
# pip simply finds no version installable on 3.8. So we look for a newer
# Python installed next to the default one - it is often already there -
# and use that.

MIN_MINOR=10
PYTHON_BIN="python3"

version_ok() {
    "$1" -c "import sys; sys.exit(0 if sys.version_info >= (3, $MIN_MINOR) else 1)" 2>/dev/null
}

if ! version_ok python3; then
    OLD="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
    warn "the default python3 is $OLD, we need 3.$MIN_MINOR or newer"

    for candidate in python3.13 python3.12 python3.11 python3.10; do
        if command -v "$candidate" >/dev/null && version_ok "$candidate"; then
            PYTHON_BIN="$candidate"
            ok "found a newer one next to it: $candidate"
            break
        fi
    done
fi

if ! version_ok "$PYTHON_BIN"; then
    die "Python 3.$MIN_MINOR+ is not in the system and I did not find one
     next to the default either.

     Typical for Ubuntu 20.04, which ships Python 3.8.
     Do NOT upgrade the whole system over this - adding a newer Python
     next to the current one breaks nothing.

     1) TRY THIS FIRST - it needs no third-party package source:

            sudo apt install python3.10 python3.10-venv

        Ubuntu 22.04 and newer have python3.10 right in their own
        repository, so this is usually all it takes. If apt says it does
        not know the package, universe is probably missing:

            sudo add-apt-repository universe && sudo apt update

        On Ubuntu 20.04 apt will not find it whatever you do - its own
        repository stops at python3.9, which is no longer enough. There,
        go straight to point 2.

     2) When that fails, reach for the deadsnakes PPA:

            sudo add-apt-repository ppa:deadsnakes/ppa
            sudo apt update
            sudo apt install python3.11 python3.11-venv

        If apt still cannot find python3.11, do NOT retry - find out why:

            dpkg --print-architecture     # deadsnakes does amd64/i386 only
            lsb_release -cs               # should say 'focal'
            apt-cache policy python3.11

        Deadsnakes builds no packages for ARM (arm64/armhf). On an ARM
        server take path 1, or use pyenv.

     (apt update after adding a source only refreshes the package list;
     it upgrades nothing. Do not run apt upgrade.)

     On Debian 11 and older the same through backports, or pyenv.

     Then run the script again - it will find the newer Python itself."
fi

"$PYTHON_BIN" -c "import ensurepip" 2>/dev/null \
    || die "The chosen $PYTHON_BIN has no ensurepip - a virtual
     environment cannot be created without it. Install the -venv package
     for this
     version, for example:
         sudo apt install ${PYTHON_BIN}-venv"

ok "the app will use $PYTHON_BIN ($("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))"


# --- ffprobe (file analysis) -----------------------------------------------
#
# ffprobe comes with the ffmpeg package. We install it so the "ffprobe +
# Jellyfin" option can be switched on right away in Settings - without it
# the option would sit there unclickable and you would be back at the
# command line.
#
# Unlike psycopg this is a **system** package: it wants root and drags in
# a pile of multimedia libraries. That is why it can be turned off:
#
#     SKIP_FFMPEG=1 ./deploy/install.sh
#
# As with psycopg, a failed install must not bring the script down - the
# app works without ffprobe, it just takes technical data from Jellyfin.

if command -v ffprobe >/dev/null; then
    ok "ffprobe found: $(command -v ffprobe)"
elif [[ "${SKIP_FFMPEG:-}" == "1" ]]; then
    warn "ffprobe is missing, SKIP_FFMPEG=1 - skipping"
    warn "without it only the 'Jellyfin API only' data source works"
else
    say "ffmpeg (for ffprobe)"
    echo "  ffprobe is not in the system - trying to install the ffmpeg package"
    echo "  (a large system package; skip it with SKIP_FFMPEG=1)"

    if command -v apt-get >/dev/null; then
        as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg || true
    elif command -v dnf >/dev/null; then
        # Fedora has ffmpeg-free in its base repositories; full ffmpeg
        # tends to live in RPM Fusion, which we will not add for you.
        as_root dnf install -y -q ffmpeg || as_root dnf install -y -q ffmpeg-free || true
    elif command -v yum >/dev/null; then
        as_root yum install -y -q ffmpeg || true
    elif command -v pacman >/dev/null; then
        as_root pacman -Sy --noconfirm --needed ffmpeg || true
    elif command -v zypper >/dev/null; then
        as_root zypper --non-interactive install ffmpeg || true
    elif command -v apk >/dev/null; then
        as_root apk add --quiet ffmpeg || true
    else
        warn "unknown package manager - install ffmpeg yourself"
    fi

    if command -v ffprobe >/dev/null; then
        ok "ffprobe installed: $(command -v ffprobe)"
    else
        warn "ffprobe could not be installed"
        warn "the app still works, it just takes technical data from Jellyfin"
        warn "by hand:  sudo apt install ffmpeg"
    fi
fi

# --- 2. folders ------------------------------------------------------------

say "Folders"
# No user creation and no chown. The folders are made by whoever ran the
# script, so they belong to them right away - and the app, running as
# that same account, can write into them. A whole category of permission
# problems disappears.
mkdir -p "$APP_DIR/data" "$LOG_DIR"
ok "data: $APP_DIR/data"
ok "logs: $LOG_DIR"

# When the script runs through sudo, the folders it made would belong to
# root - and the app running as SUDO_USER could not write into them.
# Hand them back to whoever they should belong to.
if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
    chown -R "$APP_USER:$APP_GROUP" "$APP_DIR" "$LOG_DIR"
    ok "ownership handed back to $APP_USER (the script ran through sudo)"
fi

# --- 3. virtual environment ------------------------------------------------

say "Python environment"

# We consider an environment finished when it has python **and** pip.
# If a previous run stopped halfway (see the ensurepip note above), it
# leaves a directory with a python symlink and no pip. We throw such a
# leftover away and build the environment again - repairing it piece by
# piece is more work than making a clean one.
if [[ -e "$VENV" && ! -x "$VENV/bin/python" ]]; then
    warn "no python in the .venv folder - discarding and creating it again"
    rm -rf "$VENV"
fi

# An environment from an earlier run may be built on an OLDER Python.
#
# This is the trap people get stuck in most often: the first attempt
# runs on Ubuntu 20.04 with Python 3.8, fails with
#
#     ERROR: Could not find a version that satisfies the requirement uvicorn
#
# the user installs python3.11 and runs the script again - but .venv
# already exists, is still built on 3.8, and the error repeats word for
# word. From the outside it looks as if installing Python did not help.
#
# So we throw the environment away and rebuild it with the Python chosen
# above. Nothing is lost - it only holds libraries.
if [[ -x "$VENV/bin/python" ]] && ! version_ok "$VENV/bin/python"; then
    OLD_VENV="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo '?')"
    warn ".venv is built on Python $OLD_VENV, we need 3.$MIN_MINOR+"
    warn "discarding it and rebuilding with $PYTHON_BIN"
    rm -rf "$VENV"
fi

if [[ ! -x "$VENV/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$VENV" \
        || die "Creating the virtual environment failed.
     On Debian/Ubuntu the cause is usually a missing package:
         sudo apt install python3-venv
     Then delete the leftover and run the script again:
         rm -rf $VENV && $0"
    ok "virtual environment created"
fi

PY="$VENV/bin/python"
# We print the version out loud - when something goes wrong it is the
# first thing anyone asks about.
ok "the environment runs on Python $("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo '?')"

# pip is called as `python -m pip`, not through the .venv/bin/pip
# launcher. Two reasons: the launcher may not exist (half-made
# environment) and it carries a hard-coded path that breaks when the
# folder moves. The module works as long as python itself does.
if ! "$PY" -m pip --version >/dev/null 2>&1; then
    warn "the virtual environment has no pip - adding it"
    "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

"$PY" -m pip --version >/dev/null 2>&1 \
    || die "The virtual environment has no pip and it cannot be added.
     Try building the environment again:
         sudo apt install python3-venv
         rm -rf $VENV
         $0"

# A current pip resolves dependencies far better than the one shipped
# with Ubuntu 20.04 (20.0.2). It costs a few seconds and saves guessing.
"$PY" -m pip install --quiet --upgrade pip

if ! "$PY" -m pip install --quiet -r "$APP_DIR/requirements.txt"; then
    die "Installing the dependencies failed.
     The environment runs on: $("$PY" -V 2>&1)
     pip: $("$PY" -m pip --version 2>&1)

     When pip says 'Could not find a version that satisfies', it means
     no package exists for this Python version - that is, the
     environment is built on an older Python than we need (3.$MIN_MINOR+).

     Discard the environment and run the script again for a clean build:
         rm -rf $VENV
         $0"
fi
ok "dependencies installed"

# The PostgreSQL driver is installed right away, even though most people
# will not use it. It costs ~4 MB downloaded once, and saves the
# situation where somebody switches the database in Settings and gets an
# error only fixable from the command line, with a restart.
#
# A failure here **must not** bring the installation down: psycopg-binary
# has no prebuilt packages for every platform (typically Alpine/musl).
# There pip would try to build from source, hit a missing libpq-dev, and
# with `set -e` take down the installation of somebody who only ever
# wanted SQLite. Hence `|| warn` - PostgreSQL simply will not be
# available, and that is fine.
say "PostgreSQL driver (optional)"
if "$PY" -m pip install --quiet "psycopg[binary,pool]" 2>/dev/null; then
    ok "psycopg installed - PostgreSQL can be switched on in Settings"
else
    warn "psycopg could not be installed (no prebuilt packages for this"
    warn "platform). SQLite works without it, PostgreSQL does not."
    warn "If you want it, try:  $PY -m pip install \"psycopg[binary]\""
fi

# --- 4. configuration ------------------------------------------------------

say "Configuration"
set_env() {
    # Rewrites one value in .env and leaves the rest alone. We are this
    # careful because of SECRET_KEY: rewriting the whole file would sign
    # everybody out.
    local key="$1" value="$2" file="$APP_DIR/.env"
    if grep -q "^${key}=" "$file"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
        printf '%s=%s
' "$key" "$value" >> "$file"
    fi
}

if [[ -f "$APP_DIR/.env" ]]; then
    ok ".env already exists, leaving it alone"

    # ...except for what you explicitly passed this time. This is an easy
    # trap: a second installation with PORT=... used to change nothing
    # and the app kept coming up on the old port.
    if [[ -n "$PORT_GIVEN" ]]; then
        set_env PORT "$PORT"
        ok "PORT in .env set to $PORT"
    fi
    if [[ -n "$HOST_GIVEN" ]]; then
        set_env HOST "$HOST"
        ok "HOST in .env set to $HOST"
    fi
else
    # We generate the key. Leaving the example value in place would let
    # anyone who knows the project forge a login cookie.
    SECRET="$("$VENV/bin/python" \
              -c 'import secrets; print(secrets.token_hex(32))')"

    cat > "$APP_DIR/.env" <<EOF
# Generated by deploy/install.sh
#
# The Jellyfin address, API key, passwords and the database choice are
# all configured in the app (Settings), not here. .env keeps only what
# the app needs before it can open the database.

SECRET_KEY=$SECRET

HOST=$HOST
PORT=$PORT
DATABASE_PATH=data/jellyscope.db

# Turn on once you have nginx with HTTPS in front of the app.
# CAREFUL: without working HTTPS nobody can sign in with this.
SECURE_COOKIES=
FORWARDED_ALLOW_IPS=
EOF
    chmod 600 "$APP_DIR/.env"
    ok ".env created with a generated SECRET_KEY"
fi

# From here on .env is the truth - the app reads it, not our variables.
#
# Without this step the smoke test would knock on a different port than
# the app actually listens on, and the closing summary would print the
# wrong address. All it takes is somebody having edited the port in .env.
from_env() {
    local value
    value="$(grep -m1 "^$1=" "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d ' 
')"
    printf '%s' "${value:-$2}"
}
PORT="$(from_env PORT "$PORT")"
HOST="$(from_env HOST "$HOST")"

# You cannot knock on 0.0.0.0, it means "all interfaces". For the test
# and for the links we need an address that means something.
TEST_HOST="$HOST"
[[ "$TEST_HOST" == "0.0.0.0" || -z "$TEST_HOST" ]] && TEST_HOST=127.0.0.1

# --- 5. smoke test ---------------------------------------------------------

# Before we get to autostart, check that the app comes up at all. If
# something is wrong with the environment or .env, let it show now - not
# later, when you are wondering why the service keeps dying.

say "Smoke test"
if command -v curl >/dev/null; then
    SMOKE_LOG="$(mktemp)"
    "$VENV/bin/python" "$APP_DIR/run.py" >"$SMOKE_LOG" 2>&1 &
    SMOKE_PID=$!

    SMOKE_OK=0
    for _ in $(seq 1 15); do
        sleep 1
        if curl -fsS -o /dev/null "http://$TEST_HOST:$PORT/setup" 2>/dev/null; then
            SMOKE_OK=1
            break
        fi
        # If the process died meanwhile, there is no point waiting.
        kill -0 "$SMOKE_PID" 2>/dev/null || break
    done

    kill -TERM "$SMOKE_PID" 2>/dev/null || true
    wait "$SMOKE_PID" 2>/dev/null || true

    if [[ $SMOKE_OK -eq 1 ]]; then
        ok "the app started and answered on http://$TEST_HOST:$PORT"
    else
        warn "the app did not answer. Output:"
        sed 's/^/      /' "$SMOKE_LOG" | tail -n 20
        warn "the installation continues, but sort this out before autostart"
    fi
    rm -f "$SMOKE_LOG"
else
    warn "curl is not available - smoke test skipped"
fi

# --- 6. autostart ----------------------------------------------------------

say "Autostart"

# Time zone. The app groups "when do people watch" and daily totals by
# it - without it the server would count in UTC and the chart would not
# match the clock on the wall.
TZ_NOW="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo 'Europe/Prague')"
ok "time zone: $TZ_NOW"

# What does this machine actually have? /run/systemd/system exists only
# when systemd runs as init - the presence of systemctl is not enough,
# it is often installed where systemd runs nothing (a container, say).
HAS_SYSTEMD=0
HAS_SUPERVISOR=0
[[ -d /run/systemd/system ]] && command -v systemctl >/dev/null && HAS_SYSTEMD=1
command -v supervisorctl >/dev/null && HAS_SUPERVISOR=1

# Prepare configs with the paths, user and time zone already filled in.
# The templates stay in the repository as the single source of truth;
# these files are only their filled-in copy, ready to be put in place.
SERVICE_READY="$APP_DIR/deploy/jellyscope.service.ready"
USER_READY="$APP_DIR/deploy/jellyscope.user.service.ready"
CONF_READY="$APP_DIR/deploy/jellyscope.conf.ready"

# ProtectHome=true hides all of /home - fine while the app lives in
# /opt. When you install it into a home directory (which is now the
# recommended path), the service could not reach its own code and
# systemd would refuse to start it. In that case we turn it off.
if [[ "$APP_DIR" == /home/* || "$APP_DIR" == /root/* ]]; then
    PROTECT_HOME="s|^ProtectHome=true|# ProtectHome disabled: the app lives in a home directory and with\n# it enabled it could not reach its own files.\n#ProtectHome=true|"
    ok "the app is in a home directory - ProtectHome will be disabled"
else
    PROTECT_HOME="s|^ProtectHome=true|ProtectHome=true|"
fi

sed -e "s|/opt/jellyscope|$APP_DIR|g" \
    -e "s|^User=jellyscope|User=$APP_USER|" \
    -e "s|^Group=jellyscope|Group=$APP_GROUP|" \
    -e "s|^Environment=TZ=.*|Environment=TZ=$TZ_NOW|" \
    -e "$PROTECT_HOME" \
    "$APP_DIR/deploy/jellyscope.service" > "$SERVICE_READY"

# The user variant of the same service - runs under your account without
# root. It must contain no User= and Group= lines: a user service runs as
# you by definition and systemd would refuse to load it with them.
# WantedBy must be default.target, not multi-user.target - that one does
# not exist in a user session.
sed -e "s|^User=.*||" \
    -e "s|^Group=.*||" \
    -e "s|^WantedBy=multi-user.target|WantedBy=default.target|" \
    "$SERVICE_READY" > "$USER_READY"

sed -e "s|/opt/jellyscope|$APP_DIR|g" \
    -e "s|^user=jellyscope|user=$APP_USER|" \
    -e "s|/var/log/jellyscope|$LOG_DIR|g" \
    -e "s|TZ=\"[^\"]*\"|TZ=\"$TZ_NOW\"|" \
    "$APP_DIR/deploy/jellyscope.conf" > "$CONF_READY"

ok "configs ready for systemd (system and user) and supervisord"

if [[ $HAS_SYSTEMD -eq 1 && $HAS_SUPERVISOR -eq 1 ]]; then
    echo "  found: systemd and supervisord"
elif [[ $HAS_SYSTEMD -eq 1 ]]; then
    echo "  found: systemd"
elif [[ $HAS_SUPERVISOR -eq 1 ]]; then
    echo "  found: supervisord"
else
    echo "  neither systemd nor supervisord found"
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

# --- closing summary -------------------------------------------------------

cat <<EOF

════════════════════════════════════════════════════════════
  Installation done. The app is NOT running permanently yet -
  it still needs a process manager. Instructions below.
════════════════════════════════════════════════════════════
EOF

if [[ $HAS_SYSTEMD -eq 1 ]]; then
cat <<EOF

────────────────────────────────────────────────────────────
  AUTOSTART WITH SYSTEMD - A) NO ROOT (user service)

  The service runs as $APP_USER and you manage it without sudo.
  That matches how the app is installed.

      mkdir -p ~/.config/systemd/user
      cp $USER_READY \\
         ~/.config/systemd/user/jellyscope.service
      systemctl --user daemon-reload
      systemctl --user enable --now jellyscope

  One extra thing: user services normally end when you log out.
  To keep it running and start it after a reboot, enable
  "lingering" (the only command here that wants root):

      sudo loginctl enable-linger $APP_USER

  Check:

      systemctl --user status jellyscope
      journalctl --user -u jellyscope -f     # live log

  Restart after a settings change:

      systemctl --user restart jellyscope

────────────────────────────────────────────────────────────
  AUTOSTART WITH SYSTEMD - B) SYSTEM SERVICE$([[ $HAS_SUPERVISOR -eq 1 ]] && echo " (recommended)")

  The classic variant. Starts at boot without anyone logging in
  and needs no lingering, but is set up as root.

      sudo cp $SERVICE_READY \\
              /etc/systemd/system/jellyscope.service
      sudo systemctl daemon-reload
      sudo systemctl enable --now jellyscope

  \`enable\` handles the start after a reboot, \`--now\` starts it
  right away.

  Check:

      sudo systemctl status jellyscope
      sudo journalctl -u jellyscope -f       # live log

  Restart after a settings change:

      sudo systemctl restart jellyscope

  Use A) or B), not both - they would fight over the port.
EOF
fi

if [[ $HAS_SUPERVISOR -eq 1 ]]; then
cat <<EOF

────────────────────────────────────────────────────────────
  AUTOSTART WITH SUPERVISORD$([[ $HAS_SYSTEMD -eq 1 ]] && echo " (an alternative - use only one of them)")

  The config is ready, just copy it into place:

      sudo cp $CONF_READY \\
              /etc/supervisor/conf.d/jellyscope.conf
      sudo supervisorctl reread
      sudo supervisorctl update

  \`autostart=true\` in the config handles the start after a
  reboot - assuming supervisord itself starts after a reboot.

  Check:

      sudo supervisorctl status jellyscope
      sudo supervisorctl tail -f jellyscope stderr

  Restart after a settings change:

      sudo supervisorctl restart jellyscope
EOF
fi

if [[ $HAS_SYSTEMD -eq 0 && $HAS_SUPERVISOR -eq 0 ]]; then
cat <<EOF

────────────────────────────────────────────────────────────
  AUTOSTART

  I found neither systemd nor supervisord. Something has to keep
  an eye on the app - otherwise it stops when you log out or
  reboot.

  Run it by hand (just to try it; it ends when you close the
  terminal):

      $VENV/bin/python $APP_DIR/run.py

  Whatever you use (OpenRC, runit, s6, Docker), it needs:

      command:     $VENV/bin/python run.py
      working dir: $APP_DIR
      user:        $APP_USER
      variable:    TZ=$TZ_NOW
      restart:     always, and start after a reboot

  Ready-made templates for systemd and supervisord are in:

      $APP_DIR/deploy/
EOF
fi

# The most common misunderstanding after installing: the app runs but
# cannot be reached from another computer. The reason is HOST=127.0.0.1,
# which means "this machine only". Not a bug - a safe default - but
# anyone without a proxy in front needs to know.
if [[ "$HOST" == "127.0.0.1" || "$HOST" == "localhost" ]]; then
cat <<EOF

────────────────────────────────────────────────────────────
  NOTE: THE APP LISTENS ON THIS MACHINE ONLY

  .env has HOST=$HOST, so you cannot reach it from another
  computer on the network. That is deliberate - there is usually
  a reverse proxy in front.

  To reach it directly (http://${LAN_IP:-SERVER-IP}:$PORT), change
  the line in $APP_DIR/.env to:

      HOST=0.0.0.0

  and restart the service. After that everyone on the network can
  see the app - do not leave it on a public address.
────────────────────────────────────────────────────────────
EOF
fi

cat <<EOF

────────────────────────────────────────────────────────────
  ONCE IT IS RUNNING

  Open:  http://${LAN_IP:-$TEST_HOST}:$PORT

  1. Create an administrator account
  2. Settings -> Jellyfin -> Test connection
  3. Synchronise library

────────────────────────────────────────────────────────────
  REVERSE PROXY

  Set the web server up yourself - nginx, Caddy, Apache, Traefik,
  whatever you use. The script does not touch it.

  Point the traffic at:

      http://$TEST_HOST:$PORT

  Pass these headers:

      Host               the original hostname
      X-Real-IP          the client address
      X-Forwarded-For    the chain of addresses
      X-Forwarded-Proto  http or https

  Two settings that are easy to forget:

      - upload limit of at least 200 MB
        (importing a Jellystat backup)
        nginx: client_max_body_size 200M
        Apache: LimitRequestBody 209715200
        Caddy: handles it, nothing to set

      - timeout of at least 600 s
        (syncing a large library takes minutes)
        nginx: proxy_read_timeout 600s
        Apache: ProxyTimeout 600
        Caddy: handles it

  Ready-made examples:  $APP_DIR/deploy/

────────────────────────────────────────────────────────────
  ONCE HTTPS WORKS

  Add to $APP_DIR/.env:

      SECURE_COOKIES=1
      FORWARDED_ALLOW_IPS=127.0.0.1

  and restart the service (commands above).

  Then, not before. Without working HTTPS the browser would not
  send the login cookie and nobody could sign in.

────────────────────────────────────────────────────────────
  Updates:  sudo $APP_DIR/deploy/update.sh
════════════════════════════════════════════════════════════

EOF
