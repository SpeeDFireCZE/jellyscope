r"""A check of the settings for deploying to a server.

It does not test the running app but the **configuration** — and that makes sense: a mistake
in it shows only in production, where it is hardest to look for. Typically in that
nobody signs in and it is not seen why.

Run:
    .venv/bin/python tests/test_deploy.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from jellyscope import config as cfg  # noqa: E402

failures = 0


def check(ok: bool, message: str) -> None:
    global failures
    print(f"{'OK     ' if ok else 'FAIL   '}{message}")
    if not ok:
        failures += 1


print("--- the default state (without variables) ---")
# The defaults must be the safe ones for running locally. Were they switched on
# by themselves, the app could not be opened after installation.
for key in ("SECURE_COOKIES", "FORWARDED_ALLOW_IPS"):
    os.environ.pop(key, None)
os.environ.setdefault("SECRET_KEY", "testovaci-klic")

config = cfg.load_config(reload=True)
check(config.secure_cookies is False, "SECURE_COOKIES default = off")
check(config.forwarded_allow_ips == "",
      "FORWARDED_ALLOW_IPS default = empty (trust nobody)")

print("--- different shapes of a truth value ---")
# People write "1", "true", "yes"... Arguing with the user about the value's shape
# makes no sense, but nonsense must behave as off.
for value, want in [("1", True), ("true", True), ("TRUE", True), ("yes", True),
                    ("on", True), ("0", False), ("false", False), ("", False),
                    ("nesmysl", False)]:
    os.environ["SECURE_COOKIES"] = value
    check(cfg.load_config(reload=True).secure_cookies is want,
          f"SECURE_COOKIES={value!r} -> {want}")

os.environ["FORWARDED_ALLOW_IPS"] = " 127.0.0.1 "
check(cfg.load_config(reload=True).forwarded_allow_ips == "127.0.0.1",
      "spaces around the proxy's address are trimmed")

print("--- starting without Jellyfin set up ---")
# Jellyfin's address and the API key are set in the app, not in .env. The launcher
# therefore must NOT refuse to start when they are not filled in - the user needs to
# get into the interface to fill them in. Exactly this was here once,
# and the app would not start on a fresh server at all.
launcher = (PROJECT / "run.py").read_text(encoding="utf-8")
check("config.jellyfin_api_key" not in launcher,
      "the launcher does not read the API key from .env")
# Not even an unavailable database ends the start any more: the app comes up and answers
# every address with a page that says why. Ending would mean
# "unable to connect" in the browser and the reason lying in the log.
check(re.search(r"except db\.DatabaseUnavailable[\s\S]{0,700}?_outage_mode",
                launcher) is not None,
      "an unavailable database leads to a stand-in page, not to the end")
check("outage.app" in launcher,
      "and that page is served by a module of its own")

print("--- the files for deploying ---")
for name in ("DEPLOY.md", "README.md", "deploy/jellyscope.conf", "deploy/jellyscope.service"):
    check((PROJECT / name).is_file(), f"{name} exists")

supervisor = (PROJECT / "deploy" / "jellyscope.conf").read_text(encoding="utf-8")
for needle, why in [
    ("/opt/jellyscope/.venv/bin/python", "the full path to python (supervisord does not know PATH)"),
    ("user=jellyscope", "does not run as root"),
    ("stopasgroup=true", "stops ffprobe and pg_dump too"),
    ("TZ=", "the time zone for the daily sums"),
    ("autorestart=true", "a restart after a crash"),
    ("directory=/opt/jellyscope", "the working directory"),
]:
    check(needle in supervisor, f"supervisord: {why}")

# The systemd unit must be able to do the same as the configuration for supervisord - otherwise
# the choice of a process manager would change the app's behaviour, not only how it is started.
systemd = (PROJECT / "deploy" / "jellyscope.service").read_text(encoding="utf-8")
for needle, why in [
    ("/opt/jellyscope/.venv/bin/python", "the full path to python (systemd does not know PATH)"),
    ("User=jellyscope", "does not run as root"),
    ("WorkingDirectory=/opt/jellyscope", "the working directory"),
    ("Environment=TZ=", "the time zone for the daily sums"),
    ("Restart=always", "a restart after a crash"),
    ("KillMode=control-group", "stops ffprobe and pg_dump too"),
    ("TimeoutStopSec=30", "gives the app time for a decent shutdown"),
    ("WantedBy=multi-user.target", "starts after a reboot"),
    ("ReadWritePaths=/opt/jellyscope/data", "may write only into its data/ folder"),
]:
    check(needle in systemd, f"systemd: {why}")

print("--- the installation scripts ---")
# The services' configuration must have LF too - systemd and supervisord cope with a CR
# at the end of a line differently well, and it is miserable to look for.
for name in ("deploy/jellyscope.service", "deploy/jellyscope.conf"):
    raw = (PROJECT / name).read_bytes()
    check(b"\r" not in raw, f"{name} has Unix line ends (LF)")

for name in ("deploy/install.sh", "deploy/update.sh"):
    path = PROJECT / name
    check(path.is_file(), f"{name} exists")
    if not path.is_file():
        continue

    raw = path.read_bytes()
    # Windows line ends would bring bash down on Linux right at the shebang
    # with the message "bad interpreter: /bin/bash^M". .gitattributes deals with it,
    # but the test watches the file in the working copy too.
    check(b"\r\n" not in raw, f"{name} has Unix line ends (LF)")
    check(raw.startswith(b"#!/bin/bash"), f"{name} has a shebang")

    # The executable flag must be in git too, not only on disk.
    #
    # Windows does not know it on files, so the scripts are easily committed
    # as 100644. After `git clone` on the server they are not executable then,
    # and sudo answers with a confusing "command not found" - as if the file
    # did not exist.
    mode = subprocess.run(
        ["git", "ls-files", "-s", name],
        cwd=str(PROJECT), capture_output=True, text=True,
    ).stdout.strip()
    if not mode:
        print(f"SKIPPED  {name} is not in git")
    else:
        check(mode.startswith("100755"),
              f"{name} is executable in git ({mode.split()[0]})")

    text = raw.decode("utf-8")
    check("set -euo pipefail" in text,
          f"{name}: stops at the first error instead of going on")

install = (PROJECT / "deploy" / "install.sh").read_text(encoding="utf-8")
update = (PROJECT / "deploy" / "update.sh").read_text(encoding="utf-8")
dockerfile = (PROJECT / "Dockerfile").read_text(encoding="utf-8")
for needle, why in [
    ("EUID -eq 0", "recognises whether it runs as root"),
    ("secrets.token_hex", "generates its own SECRET_KEY"),
    ("useradd --system", "advises how to create a dedicated account"),
    ("/setup", "checks by a trial that the app comes up"),
    ("REVERSE PROXY", "writes out where to send the traffic"),
    ("X-Forwarded-Proto", "writes out the headers needed"),
    ("SECURE_COOKIES=1", "writes out what to switch on after HTTPS works"),
    ("/run/systemd/system", "recognises whether systemd really runs as init"),
    ("command -v supervisorctl", "recognises supervisord"),
    ("systemctl enable --now jellyscope", "advises how to switch on the start after a reboot"),
    ("supervisorctl reread", "advises how to load supervisord's configuration"),
    ("jellyscope.service.ready", "prepares a filled-in configuration for systemd"),
    ("jellyscope.conf.ready", "prepares a filled-in configuration for supervisord"),
]:
    check(needle in install, f"install.sh: {why}")

# Everybody deals with the web server and the process manager in their own way - the script
# must not touch them. Installing supervisord on a server that has systemd is
# exactly the kind of "help" that leaves a mess behind.
for needle in ("apt-get install -y -qq nginx", "systemctl reload nginx", "certbot"):
    check(needle not in install, f"install.sh does not set up a web server ({needle})")

check("MISSING+=(supervisor)" not in install,
      "install.sh does not install supervisord")

# update.sh refuses to run as root; the closing summary must not advise `sudo .../update.sh`. The
# advice is worked out by the summary's own lines, run here in bash for who ran the installation.
_update_advice = [line.strip() for line in install.splitlines() if "Updates:" in line]
check(len(_update_advice) == 1 and re.search(r"\bsudo\s+(?!-u\b)\S*update\.sh", install) is None,
      f"install.sh: the advice for updates does not run update.sh as root ({_update_advice})")
_advice_code = re.search(r'^UPDATE_CMD=.*?^fi$', install, re.M | re.S)
_bash = shutil.which("bash")
if not _bash:
    print("SKIPPED  bash is missing")
# sudo sets SUDO_USER for `sudo -u <account>` too, not only for root: only a run as root (EUID 0) is "through sudo
# into root". EUID cannot be set in bash, so the snippets read TEST_EUID in its place.
for sudo_user, euid, app_user, want in [
    ("", "1000", "anna", "/opt/js/deploy/update.sh"),
    ("anna", "0", "anna", "sudo -u anna /opt/js/deploy/update.sh"),
    ("", "0", "root", "ALLOW_ROOT=1 /opt/js/deploy/update.sh"),
    ("anna", "1001", "jellyscope", "sudo -u jellyscope /opt/js/deploy/update.sh"),
]:
    if not _bash:
        break
    _env = {k: v for k, v in os.environ.items() if k != "SUDO_USER"}
    _env.update({"APP_DIR": "/opt/js", "APP_USER": app_user, "TEST_EUID": euid},
                **({"SUDO_USER": sudo_user} if sudo_user else {}))
    _got = subprocess.run(
        [_bash, "-c", "set -euo pipefail\n"
         + (_advice_code.group(0).replace("$EUID", "$TEST_EUID") if _advice_code else "")
         + '\nprintf "  Updates:  %s" "$UPDATE_CMD"'],
        env=_env, capture_output=True, text=True).stdout
    check(_advice_code is not None and _update_advice == ["Updates:  $UPDATE_CMD"]
          and _got == f"  Updates:  {want}",
          f"install.sh: after an installation by {app_user}"
          f"{f' through sudo by {sudo_user}' if sudo_user else ''} (EUID {euid}) it advises {want!r} ({_got.strip()!r})")

# Who the app is installed for. `sudo -u jellyscope install.sh` (the dedicated account of the script's own header)
# run by anna sets SUDO_USER=anna: the installation is jellyscope's, not anna's. Only root reached through sudo
# means the person behind it.
_who_code = re.search(r'^APP_USER=.*?(?=^APP_GROUP=)', install, re.M | re.S)
_me = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
for sudo_user, euid, want in [("", "1000", _me), ("anna", "1001", _me), ("anna", "0", "anna")]:
    if not _bash:
        break
    _env = {k: v for k, v in os.environ.items() if k != "SUDO_USER"}
    _env.update({"TEST_EUID": euid}, **({"SUDO_USER": sudo_user} if sudo_user else {}))
    _got = subprocess.run(
        [_bash, "-c", "set -euo pipefail\n"
         + (_who_code.group(0).replace("$EUID", "$TEST_EUID") if _who_code else "")
         + '\nprintf "%s" "$APP_USER"'],
        env=_env, capture_output=True, text=True).stdout
    check(_who_code is not None and _got == want,
          f"install.sh: run as {_me if euid != '0' else 'root'}"
          f"{f' through sudo by {sudo_user}' if sudo_user else ''} installs for {want!r} ({_got!r})")
# The two snippets chained as the script runs them - the advice from the account the script itself worked out, not
# from an APP_USER handed in from outside: `sudo -u <account>` by anna installs for the account and advises
# `sudo -u <account> update.sh` (the dedicated account has no shell anna could switch into), `sudo` into root
# advises it as anna.
for sudo_user, euid, want in [("anna", "1001", f"sudo -u {_me} /opt/js/deploy/update.sh"),
                              ("anna", "0", "sudo -u anna /opt/js/deploy/update.sh"),
                              ("", "1000", "/opt/js/deploy/update.sh")]:
    if not _bash:
        break
    _env = {k: v for k, v in os.environ.items() if k not in ("SUDO_USER", "APP_USER")}
    _env.update({"APP_DIR": "/opt/js", "TEST_EUID": euid}, **({"SUDO_USER": sudo_user} if sudo_user else {}))
    _got = subprocess.run(
        [_bash, "-c", "set -euo pipefail\n"
         + (_who_code.group(0) + "\n" + _advice_code.group(0) if _who_code and _advice_code else "").replace("$EUID", "$TEST_EUID")
         + '\nprintf "%s|%s" "$APP_USER" "$UPDATE_CMD"'],
        env=_env, capture_output=True, text=True).stdout
    check(_got == f"{sudo_user if euid == '0' else _me}|{want}",
          f"install.sh from start to the advice: run as {_me if euid != '0' else 'root'}"
          f"{f' through sudo by {sudo_user}' if sudo_user else ''} advises {want!r} ({_got!r})")


def command_lines_of(text: str) -> list[str]:
    """The lines of a shell script that are not comments."""
    return [line for line in text.splitlines() if not line.strip().startswith("#")]


def runnable_code(text: str) -> str:
    """Returns from a shell script only what really runs.

    It throws away three things that look like commands, but are not:
      * comments,
      * the content of double quotes (multi-line die/warn texts with instructions),
      * heredocs (the closing output with the commands the user is to run).

    Without that the test would report an error where there is only help - and exactly
    that is the difference between "the script does it" and "the script advises it".
    """
    # 1. heredocs away (cat <<EOF ... EOF)
    lines = text.split("\n")
    without_heredocs: list[str] = []
    in_heredoc = False
    for line in lines:
        if in_heredoc:
            without_heredocs.append("")
            if line.strip() == "EOF":
                in_heredoc = False
            continue
        if re.search(r"<<-?'?EOF'?", line):
            in_heredoc = True
            without_heredocs.append("")
            continue
        without_heredocs.append(line)

    # 2. the content of double quotes away, keep the lines
    out: list[str] = []
    in_string = False
    joined = "\n".join(without_heredocs)
    index = 0
    while index < len(joined):
        char = joined[index]
        if char == "\\" and index + 1 < len(joined):
            index += 2
            continue
        if char == '"':
            in_string = not in_string
        elif not in_string or char == "\n":
            out.append(char)
        index += 1

    # 3. comments away
    return "\n".join(
        "" if line.lstrip().startswith("#") else line
        for line in "".join(out).split("\n")
    )


# The installation runs as whoever started it. No creating a system
# account, no taking over ownership - that removes a whole category of trouble
# with rights, the one run into most often when deploying.
install_code = runnable_code(install)
update_code = runnable_code(update)

check("useradd" not in install_code, "install.sh creates no system user")
check("sudo -u" not in install_code, "install.sh runs nothing through sudo -u")
check("sudo -u" not in update_code, "update.sh runs nothing through sudo -u")
check('APP_USER="$(id -un)"' in install and 'APP_USER="$SUDO_USER"' in install,
      "install.sh takes the user it runs as (SUDO_USER after sudo into root), not a hard-coded 'jellyscope'")

# The app must not end up started as root. It talks over the network, reads other people's
# files and starts ffprobe - whoever got around it would have the whole machine.
check('"$APP_USER" == "root"' in install,
      "install.sh refuses to set the app up as root")

# The checks may call die() only after it is defined - otherwise the user would
# get "die: command not found" instead of an explanation. This happened here
# once, because the safeguard came about at the top by the variables.
install_lines = install.splitlines()
die_at = next(i for i, r in enumerate(install_lines) if r.startswith("die()"))
checks_at = [i for i, r in enumerate(install_lines)
             if "die " in r and not r.strip().startswith("#")
             and not r.startswith("die()")]
check(all(i > die_at for i in checks_at),
      f"install.sh calls die() only after its definition (line {die_at + 1})")
check("ALLOW_ROOT" in install and "ALLOW_ROOT" in update,
      "both scripts have the deliberate exception ALLOW_ROOT (containers)")

# The logs belong in the project's folder, not in /var/log - only root could write
# there and we would be back at solving rights.
check("$APP_DIR/data/logs" in install, "install.sh puts the logs into the project's folder")
service_conf = (PROJECT / "deploy" / "jellyscope.conf").read_text(encoding="utf-8")
check("/var/log/jellyscope" not in service_conf,
      "the supervisord template does not write into /var/log")

# A user systemd service - an autostart entirely without root.
check("jellyscope.user.service.ready" in install,
      "install.sh prepares a user systemd service too")
check("loginctl enable-linger" in install,
      "install.sh advises lingering, so the service runs after signing out too")
check("systemctl --user cat jellyscope.service" in update,
      "update.sh recognises a user service and restarts it without root")

# ProtectHome=true hides the whole /home. While the app lived in /opt, it was
# a clean win. As soon as installing into the home directory became the recommended
# way, it would mean a service that does not get to its own
# code - and systemd would not start it at all.
check('"$APP_DIR" == /home/*' in install,
      "install.sh recognises an installation in a home directory")
check("PROTECT_HOME" in install,
      "install.sh can switch ProtectHome off when it would block the app")

# The folder can be anywhere (typically /opt/jellyscope) - but it must belong
# to whoever the app will run as. The script sets it right itself, so the user
# need not fiddle with rights.
check('OWNER="$(stat -c' in install,
      "install.sh finds out whom the folder belongs to")
check('as_root chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"' in install,
      "install.sh hands the folder over itself to the user it installs as")

# A blanket chmod 755 would make .env with the cookies' signing key accessible too.
check("chmod -R" not in install_code, "install.sh does no blanket chmod")

# The PostgreSQL driver is installed straight away, so nobody has to hunt for it
# through the command line when they switch the database in Settings - at the version
# of requirements-postgres.txt, the one place it is pinned, not whatever is newest that day.
check('pip install --quiet -r "$APP_DIR/requirements-postgres.txt"' in install,
      "install.sh installs the PostgreSQL driver as well, from requirements-postgres.txt")
check(not re.search(r'pip install[^\n]*"psycopg\[', "\n".join(command_lines_of(install))),
      "and nowhere without a version")

# Its failure must not bring the installation down though: psycopg-binary has no packages
# for every platform, and `set -e` would take along the installation for whoever
# wants only SQLite.
# We search the original text, not the filtered code: the filter throws away
# the content of quotes, so exactly the file's name would vanish from the line.
psycopg_line = next(
    (r for r in install.splitlines()
     if "requirements-postgres.txt" in r and "pip install" in r), ""
)
check(psycopg_line.strip().startswith("if "),
      f"a failure of psycopg does not bring the installation down (line: {psycopg_line.strip()!r})")
check("psycopg" not in (PROJECT / "requirements.txt").read_text(encoding="utf-8")
      .split("# ---")[0],
      "psycopg is not among the required dependencies")

# ffprobe (the ffmpeg package) is installed as well - otherwise the choice
# "ffprobe + Jellyfin" in Settings would be unclickable.
# Ubuntu 20.04 has Python 3.8, and installing on it ended with the message
# "Could not find a version that satisfies the requirement uvicorn",
# which does not name the cause at all. The script must recognise it earlier.
check('MIN_MINOR="${MIN_PY#3.}"' in install, "install.sh knows the minimum Python version (from deploy/python-minimum)")
check("python3.11" in install, "looks for a newer Python installed beside")
check("deadsnakes" in install, "advises how to add a newer Python")
# The minimum holds together with what FastAPI from requirements.txt asks for.
# Were the number changed in one place only, the installation would pass
# and fall over only at pip install - with a message that does not name the cause.
minimum = PROJECT / "deploy" / "python-minimum"
check(minimum.is_file() and minimum.read_text(encoding="utf-8").strip() == "3.11",
      "the Python minimum in one machine-readable place: deploy/python-minimum")
check("deploy/python-minimum" in install and "deploy/python-minimum" in update,
      "install.sh and update.sh take the minimum from it")
check("JELLYSCOPE_UPDATE_ZNOVU" in update
      and re.search(r'exec env JELLYSCOPE_UPDATE_ZNOVU=1 JELLYSCOPE_UPDATE_PRED="\$BEFORE"'
                    r' bash "\$APP_DIR/deploy/update.sh"', update),
      "update.sh after the download runs its new version, when it changed, and hands it the original version")
# The new version of the script does not download again: otherwise it would take the one just
# downloaded for "the original version" and the advice "git reset --hard $BEFORE" would lead nowhere.
step2 = update[update.find("# --- 2."):update.find("# --- 3.")]
again = re.search(r'if \[\[ "\$\{JELLYSCOPE_UPDATE_ZNOVU:-\}" == "1" \]\]; then\n(.*?)\nelse\n',
                  step2, re.S)
check(bool(again) and not re.search(r"git_ (pull|merge|fetch)", again.group(1))
      and "JELLYSCOPE_UPDATE_PRED" in again.group(1),
      "update.sh run again does not download and takes the original version from JELLYSCOPE_UPDATE_PRED")
# Python is checked BEFORE the download, with the minimum of what is coming. After
# the download new templates would already run over old code - and the message "nothing
# changed" would not be true.
check('git_ show "$UPSTREAM:deploy/python-minimum"' in step2
      and step2.find('git_ show "$UPSTREAM:deploy/python-minimum"') < step2.find("git_ merge --ff-only"),
      "update.sh checks Python before the download, with the new version's minimum")
check("Python 3.11 or newer" in (PROJECT / "requirements.txt").read_text(encoding="utf-8"),
      "and requirements.txt says the same")

# Deadsnakes is only the second choice: it builds no packages for ARM and it is a foreign
# source. Ubuntu 22.04 and newer has python3.11 right at home (universe).
check("python3.11-venv" in install, "offers python3.11 from Ubuntu's repository")
check(install.index("python3.11-venv") < install.index("ppa:deadsnakes"),
      "and offers it BEFORE the deadsnakes PPA")
check("dpkg --print-architecture" in install,
      "when the PPA does not work, it advises finding out the architecture (deadsnakes cannot do ARM)")
# It is checked that the script does NOT run apt upgrade. In the message's text
# it may appear - there on the contrary it warns the user not to do it.
check("apt upgrade" not in runnable_code(install),
      "install.sh does not run apt upgrade")
check("apt upgrade" in install, "but warns the user not to do it")
check('"$PYTHON_BIN" -m venv' in install,
      "the environment is built with the chosen interpreter, not a hard-coded python3")

# The most frequent trap on Ubuntu 20.04: the first attempt builds .venv with Python 3.8,
# falls over on "Could not find a version that satisfies the requirement
# fastapi", the user installs python3.11 - and the error repeats, because
# .venv still stands on 3.8. From outside it looks as if installing Python
# did not help. The script must recognise the old environment and throw it away.
# We search the raw text, not runnable_code(): that throws away the content of
# quotes, so only "version_ok" would be left of the command.
command_lines = [r for r in install.splitlines() if not r.strip().startswith("#")]
check(any('version_ok "$VENV/bin/python"' in r for r in command_lines),
      "the script checks the Python version of an ALREADY EXISTING environment too")
piece = install[install.find('version_ok "$VENV/bin/python"'):]
check('rm -rf "$VENV"' in piece[:600],
      "and throws the old environment away instead of using it")
check("Could not find a version" in install,
      "the message on a failure mentions exactly what pip writes out")

# The port and the address must be written into an ALREADY EXISTING .env too - otherwise a second
# installation with PORT=... ends with the app coming up on the old port
# and nobody knowing why.
check("PORT_GIVEN" in install and "HOST_GIVEN" in install,
      "the script recognises whether the port and the address were given explicitly")
check("set_env PORT" in runnable_code(install),
      "and puts a given port into an existing .env")
check("set_env HOST" in runnable_code(install),
      "the same for the address")
# Rewriting the whole .env would mean a new SECRET_KEY and signing everybody out -
# so only one line is changed through sed.
check('sed -i "s|^${key}=' in install,
      "only one line is changed, not the whole file")

# After writing, the real values must be read from .env - otherwise the trial
# start would knock on another port than the one the app listens on.
# We search the raw text: runnable_code() throws away the content of quotes,
# so only "PORT=" would be left of the assignment.
check(any('from_env PORT' in r for r in command_lines),
      "the next steps take the port from .env, not from the variable")
check("TEST_HOST" in install and "0.0.0.0" in install,
      "0.0.0.0 is not knocked on - 127.0.0.1 is used for the trial")
check("HOST=0.0.0.0" in install,
      "the output advises how to make the app reachable from another computer")
check("--upgrade pip" in runnable_code(install),
      "pip is updated (the one from Ubuntu 20.04 is 20.0.2)")

check("SKIP_FFMPEG" in install, "installing ffmpeg can be skipped (SKIP_FFMPEG=1)")
check("apt-get install -y -qq ffmpeg" in install, "install.sh can install ffmpeg")
check("ffmpeg-free" in install, "it knows the package's name in Fedora too")

# Just as with psycopg, a failure must not bring the installation down - the app without
# ffprobe works, it only takes the technical details from Jellyfin.
ffmpeg_lines = [r.strip() for r in install_code.splitlines()
                if "install" in r and "ffmpeg" in r]
check(ffmpeg_lines and all(r.endswith("|| true") for r in ffmpeg_lines),
      f"a failure of ffmpeg does not bring the installation down {ffmpeg_lines}")
check('chmod 600 "$APP_DIR/.env"' in install, "install.sh protects .env with the rights 600")

# A trap the installation on Ubuntu fell into: the `venv` module is always in the base
# installation, but without the python3-venv package ensurepip is missing in it.
# `python3 -m venv` then manages to make bin/python and only then fails -
# and the next run of the script ended at "pip: command not found".
check('python3 -c "import ensurepip"' in install,
      "install.sh tests ensurepip, not just importing venv")
check('python3 -c "import venv"' not in install,
      "install.sh no longer relies on 'import venv' (that check lied)")

# pip is called as `python -m pip`. The .venv/bin/pip launcher need not
# exist and carries a fixed path inside it that breaks after the folder is moved.
for name, text in (("install.sh", install),
                   ("update.sh", (PROJECT / "deploy" / "update.sh").read_text(encoding="utf-8"))):
    code = [line for line in text.splitlines() if not line.strip().startswith("#")]
    check(not any("bin/pip" in line for line in code),
          f"{name} does not call .venv/bin/pip directly")

check('-x "$VENV/bin/python"' in install,
      "install.sh recognises an unfinished environment by the missing python")
check("-m ensurepip --upgrade" in install,
      "install.sh can add a missing pip")
check("apt install python3-venv" in install,
      "install.sh advises which package to install")

# The difference between "the script does it" and "the script advises it" is whether
# the command is run or only written out. Written-out advice is inside heredocs,
# indented, and starts with "sudo " - a real call would stand at the start of the line.
started = [
    line.strip() for line in install_code.splitlines()
    if line.strip().startswith(("supervisorctl ", "systemctl "))
]
check(not started,
      f"install.sh does not start the service itself, it only advises how {started}")

for needle, why in [
    ("merge --ff-only", "does not overwrite the local history"),
    ("diff --quiet", "refuses an update with unsaved changes"),
    ("reset --hard", "advises the way back to the previous version"),
    ("pg_dump", "can back up PostgreSQL too"),
    ("sqlite3", "can back up SQLite"),
    ("PGPASSWORD=", "passes the password through the environment, not the command line"),
    ("SKIP_BACKUP", "the backup can be skipped, but only knowingly"),
    ('DB_KIND="sqlite"', "without a configuration it assumes SQLite"),
    ("systemctl restart jellyscope", "can restart the service under systemd"),
    ("supervisorctl restart jellyscope", "can restart the service under supervisord"),
    ("systemctl cat jellyscope.service", "finds out who really holds the service"),
    ("safe.directory", "advises what to do with 'dubious ownership'"),
    # The templates are read from the files on every request, but the Python code
    # stays in memory. After `git pull` without a restart new templates thus run
    # over old code and the pages fall over with "Internal Server Error".
    # When update cannot restart by itself, it must say so loudly.
    ("RUNS THE OLD VERSION", "warns when it did not restart the app"),
    ("/etc/supervisor/conf.d/jellyscope.conf",
     "recognises supervisord by the configuration too (sudo -n fails when it wants a password)"),
    # An environment built on Python 3.9 is refused by pip only at fastapi, with the message
    # "Could not find a version that satisfies the requirement" - that sounds
    # as if the package did not exist, not as "you have an old Python". Update
    # must recognise it earlier and advise what to do.
    ("sys.version_info >= (3, ${MIN_PY#3.})", "recognises an environment on an old Python"),
    ("bash deploy/install.sh", "and advises what to build it again with"),
]:
    check(needle in update, f"update.sh: {why}")

# Git from 2.35.2 on refuses to work in a repository that belongs to somebody else
# ("detected dubious ownership"). The script runs as root, but the folder belongs
# to the app's user - every call of git must therefore go through them.
check('git_() { git -C "$APP_DIR" "$@"; }' in update,
      "update.sh has the helper git_(), through which all calls of git go")

outside_helper = []
for number, line in enumerate(update_code.splitlines(), 1):
    bare = line.strip()
    if "git_()" in bare:
        continue
    if re.search(r"(^|[^_\w])git ", bare):
        outside_helper.append(f"{number}: {bare}")
check(not outside_helper,
      f"update.sh calls git only through git_() {outside_helper}")

print("--- every dependency at one tested version ---")
# requirements.txt pins what the app uses. The packages those pull in had only lower bounds ("h11>=0.8"): a new
# installation got whatever was newest that day - a combination nobody tested - and no update touched them again,
# because an installed package satisfies such a bound for ever. constraints.txt pins them, and requirements.txt
# refers to it, so every `pip install -r requirements.txt` honours it: install.sh, update.sh, the Update button
# (its dry run too) and the Docker image. The versions are those of the environment these tests run in.
import importlib.metadata as _metadata  # noqa: E402

try:
    from pip._vendor.packaging.requirements import Requirement as _Requirement
except ImportError:  # pragma: no cover - a Python without pip
    _Requirement = None


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(path: Path) -> dict[str, str]:
    """name -> version of the `name==version` lines of a requirements or constraints file."""
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;]+)\s*(?:;.*)?",
                             line.split("#", 1)[0].strip())
        if match:
            found[_canonical(match.group(1))] = match.group(2)
    return found


def _roots(path: Path) -> list[str]:
    return [line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.split("#", 1)[0].strip() and not line.strip().startswith("-")]


def _closure(roots: list[str]) -> tuple[dict[str, str], set[str]]:
    """Everything the requirements pull in on this machine (name -> installed version), and what is missing."""
    found: dict[str, tuple[str, set[str]]] = {}
    missing: set[str] = set()
    todo = [_Requirement(root) for root in roots]
    while todo:
        requirement = todo.pop()
        name = _canonical(requirement.name)
        try:
            distribution = _metadata.distribution(requirement.name)
        except _metadata.PackageNotFoundError:
            missing.add(name)
            continue
        extras = found.get(name, ("", set()))[1]
        if name in found and set(requirement.extras) <= extras:
            continue
        extras = extras | set(requirement.extras)
        found[name] = (distribution.version, extras)
        for line in distribution.requires or []:
            sub = _Requirement(line)
            if sub.marker is None or any(sub.marker.evaluate({"extra": extra}) for extra in extras | {""}):
                todo.append(sub)
    return {name: version for name, (version, _) in found.items()}, missing


_requirements_file = PROJECT / "requirements.txt"
_constraints_file = PROJECT / "constraints.txt"
# The PostgreSQL driver is optional and so apart (see requirements.txt), but pinned the same way: one file for
# install.sh, update.sh, the Update button and the image.
_postgres_file = PROJECT / "requirements-postgres.txt"
check(re.search(r"^-c constraints\.txt\s*$", _requirements_file.read_text(encoding="utf-8"), re.M) is not None,
      "requirements.txt refers to constraints.txt, so every install from it honours the pins")
check(_postgres_file.is_file()
      and re.search(r"^-c constraints\.txt\s*$", _postgres_file.read_text(encoding="utf-8"), re.M) is not None
      and re.search(r"^psycopg\[binary,pool\]==\d", _postgres_file.read_text(encoding="utf-8"), re.M) is not None,
      "requirements-postgres.txt pins the driver (psycopg[binary,pool]==...) and honours the same constraints")
_required = {**_pins(_requirements_file), **_pins(_postgres_file)}
_constrained = _pins(_constraints_file)
check(not set(_required) & set(_constrained),
      f"a package is pinned in one place only ({sorted(set(_required) & set(_constrained))})")
if _Requirement is None:
    print("SKIPPED  pip's packaging is missing - the pins are not compared with the environment")
else:
    _pulled, _absent = _closure(_roots(_requirements_file))
    _pinned = {**_required, **_constrained}
    check(not _absent, f"the environment has everything requirements.txt pulls in (missing: {sorted(_absent)})")
    check(len(_pulled) > len(_required) and not [n for n in _pulled if n not in _pinned],
          f"every package requirements.txt pulls in has a version ({len(_pulled)} packages, without one: "
          f"{sorted(n for n in _pulled if n not in _pinned)})")
    check(not [n for n in _pulled if n in _pinned and _pinned[n] != _pulled[n]],
          "and it is the version the tests run with "
          f"({sorted(f'{n} {_pinned[n]} != {_pulled[n]}' for n in _pulled if n in _pinned and _pinned[n] != _pulled[n])})")
    _driver, _no_driver = _closure(_roots(_postgres_file)) if _postgres_file.is_file() else ({}, {"psycopg"})
    if _no_driver:
        print(f"SKIPPED  the PostgreSQL driver is not installed here ({sorted(_no_driver)}) - its pins are not compared")
    else:
        check(not [n for n in _driver if n not in _pinned],
              f"so has every package of the PostgreSQL driver ({sorted(n for n in _driver if n not in _pinned)})")
        check(not [n for n in _driver if n in _pinned and _pinned[n] != _driver[n]],
              "at the version installed "
              f"({sorted(f'{n} {_pinned[n]} != {_driver[n]}' for n in _driver if n in _pinned and _pinned[n] != _driver[n])})")
        check(not [n for n in _constrained if n not in _pulled and n not in _driver],
              f"constraints.txt holds only what the app's requirements pull in, no development tools "
              f"({sorted(n for n in _constrained if n not in _pulled and n not in _driver)})")
for _file in (_requirements_file, _postgres_file):
    if _file == _postgres_file and (_Requirement is None or _no_driver):
        continue
    _dry = subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--no-index", "--quiet",
                           "-r", str(_file)], cwd=tempfile.gettempdir(), capture_output=True, text=True)
    if "no such option: --dry-run" in _dry.stderr:
        print("SKIPPED  this pip cannot do a dry run")
        break
    check(_dry.returncode == 0, f"pip accepts {_file.name} with its constraints, from any folder "
                                f"and without the network ({_dry.stderr.strip()[-300:]!r})")

# The image installs from requirements.txt too; the file it refers to has to be in the image by then.
_copy_before = dockerfile[:dockerfile.find("-r requirements.txt")]
check(re.search(r"^COPY [^\n]*\bconstraints\.txt\b", _copy_before, re.M) is not None,
      "the Dockerfile copies constraints.txt before it installs requirements.txt")

print("--- examples for a reverse proxy ---")
for name in ("nginx.conf.example", "Caddyfile.example", "apache.conf.example"):
    path = PROJECT / "deploy" / name
    check(path.is_file(), f"deploy/{name} exists")
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        check("127.0.0.1:8097" in text, f"{name}: points at the right port")
        check("SECURE_COOKIES=1" in text, f"{name}: reminds to switch on the secure cookie")

print("--- versioning the static files ---")
# Without ?v=number in the address the browser (and with nginx the proxy too) would keep the old
# style and a change of the look would be invisible for days. Exactly this is what I
# once fell for while debugging the sign-in page.
templates_dir = PROJECT / "jellyscope" / "templates"
# Signing in, the first start and the error page have their head from the shared
# macro `head()` - it is enough for that one to carry the version. base.html keeps its own
# <head>, because it needs {% block title %}.
for name in ("base.html", "_macros.html"):
    text = (templates_dir / name).read_text(encoding="utf-8")
    check("style.css?v={{ asset_version }}" in text,
          f"{name}: the style has the version in its address")

for name in ("base.html", "login.html", "setup.html", "error.html", "_macros.html"):
    text = (templates_dir / name).read_text(encoding="utf-8")
    check("/static/logo.svg\"" not in text,
          f"{name}: no link to the logo without a version")
    check("style.css" not in text or "style.css?v={{ asset_version }}" in text,
          f"{name}: no link to the style without a version")

web = "\n".join(p.read_text(encoding="utf-8")
         for p in sorted((PROJECT / "jellyscope" / "web").rglob("*.py")))
check('globals["asset_version"]' in web, "web/__init__.py counts the version and hands it to the templates")

print("--- .gitignore and .gitattributes ---")
ignore = (PROJECT / ".gitignore").read_text(encoding="utf-8")
for needle in (".env", "data/", ".venv"):
    check(needle in ignore, f".gitignore leaves out {needle}")

attributes = (PROJECT / ".gitattributes").read_text(encoding="utf-8")
# We compare lines with the spaces squeezed. The test is not to fail because
# somebody lined up the columns - it is to watch the rule, not the indentation.
rules = {" ".join(line.split()) for line in attributes.splitlines()}
for pattern in ("*.sh text eol=lf", "*.conf text eol=lf", "*.service text eol=lf"):
    check(pattern in rules, f".gitattributes enforces LF: {pattern}")

print("--- the upload limit matches the proxy ---")
# A reverse proxy refuses before the app. When it has a lower ceiling, a person
# gets a message from nginx instead of an understandable sentence from Jellyscope - and looks
# into the app's log, where there is nothing.
import re as _re

web_code = "\n".join(p.read_text(encoding="utf-8")
         for p in sorted((PROJECT / "jellyscope" / "web").rglob("*.py")))
ceiling = int(_re.search(r"MAX_UPLOAD_MB = (\d+)", web_code).group(1))
check(ceiling > 0, f"the app has one ceiling for uploads ({ceiling} MB)")
check(web_code.count("* 1024 * 1024") == web_code.count("MAX_UPLOAD_MB * 1024 * 1024"),
      "and it is used for all uploads, not a number written by hand")

nginx = (PROJECT / "deploy" / "nginx.conf.example").read_text(encoding="utf-8")
match = _re.search(r"client_max_body_size\s+(\d+)M", nginx)
check(match and int(match.group(1)) >= ceiling,
      f"nginx lets at least that much through ({match.group(1) if match else '?'}M >= {ceiling}M)")

apache = (PROJECT / "deploy" / "apache.conf.example").read_text(encoding="utf-8")
match = _re.search(r"LimitRequestBody\s+(\d+)", apache)
check(match and int(match.group(1)) >= ceiling * 1024 * 1024,
      f"apache too ({int(match.group(1)) // 1024 // 1024 if match else '?'} MB)")

installer = (PROJECT / "deploy" / "install.sh").read_text(encoding="utf-8")
check(f"{ceiling} MB" in installer and f"{ceiling}M" in installer,
      f"and the installer advises the same value ({ceiling} MB)")


print("--- the container's launcher ---")
# A mounted data folder belongs on the host to whoever made it - at the
# first `docker compose up` to root. The app runs as UID 10001, so it
# would not reach it. The container therefore starts as root, sets the owner right
# and drops the rights at once. Were the last part to drop out, the whole app would run
# as root and nobody would notice.
entry = PROJECT / "deploy" / "docker-entrypoint.sh"
check(entry.is_file(), "deploy/docker-entrypoint.sh exists")
entry_text = entry.read_text(encoding="utf-8")
dockerfile = (PROJECT / "Dockerfile").read_text(encoding="utf-8")

check('ENTRYPOINT ["/bin/sh", "/app/deploy/docker-entrypoint.sh"]' in dockerfile,
      "the image runs it (and through sh, so the executable flag does not matter)")
check("deploy/" not in (PROJECT / ".dockerignore").read_text(encoding="utf-8"),
      "and does not leave it out of the image")
check("command -v setpriv" in dockerfile,
      "the build checks that the tool for dropping the rights is in the image")

# The entrypoint runs as root at every start. Were it (or any code beside it) the app user's, whoever got code
# running as UID 10001 would rewrite it and be root at the next start. The app writes only into /app/data.
_dockerfile_code = "\n".join(line for line in dockerfile.replace("\\\n", " ").splitlines()
                             if not line.lstrip().startswith("#"))
_chowns = re.findall(r"chown\s+(?:-\w+\s+)*\S+\s+(\S+)", _dockerfile_code)
check(_chowns == ["/app/data"], f"the image hands the app user only /app/data, not its code ({_chowns})")
check(re.search(r"chmod -R go-w /app(\s|$)", _dockerfile_code) is not None,
      "and nobody but root may write the code, whatever rights the files had in the build context")

# The app must never run with root's rights.
check(re.search(r'id -u.*=.*"0"', entry_text) is not None,
      "the launcher recognises that it runs as root")
check(re.search(r'exec setpriv --reuid="?\$?\{?APP_UID', entry_text) is not None,
      "and in that branch hands over control only after dropping the rights")
root_branch = entry_text.split('if [ "$(id -u)" = "0" ]', 1)[1].split("fi", 1)[0]
check('exec "$@"' not in root_branch,
      "the app is not started directly from the root branch")

# CRLF line ends in the launcher mean a message about a command not found when the
# container starts - the line end becomes a part of the name. The repository
# was developed on Windows, so it is within reach.
check(bytes([13, 10]) not in entry.read_bytes(),
      "the script has Unix line ends")

check("stat -c" in entry_text, "it deals with the owner only when it does not match")

# And even then only for an empty folder - that one Docker just made and there is
# nothing in it that would belong to a person. A folder with content is somebody's data, and
# rewriting its owner behind their back is more than the launcher may allow itself.
check("ls -A" in entry_text, "and only for an empty folder")
check("chown -R" not in entry_text.split("else", 1)[0],
      "an empty folder needs one chown, not a recursion")
advice = entry_text.split("else", 1)[1] if "else" in entry_text else ""
check("chown -R" in advice,
      "for a non-empty folder the recursive chown is only advised, not run")

print("--- the container's health check ---")
# Inside the container the app listens on PORT from compose (.env). A health check with a fixed port
# asks an address nobody listens on and the container stays "unhealthy". The check's own code is run
# here with urlopen replaced, so nothing is contacted.
import shlex  # noqa: E402
import urllib.request  # noqa: E402

_health = re.search(r"^HEALTHCHECK [^\n]*\\\n\s+CMD (.+)$", dockerfile, re.M)
_argv = shlex.split(_health.group(1)) if _health else []


def _health_asks(port: str | None) -> list[str]:
    asked: list[str] = []
    real, saved = urllib.request.urlopen, os.environ.get("PORT")
    urllib.request.urlopen = lambda url, timeout=None: asked.append(url)
    if port is None:
        os.environ.pop("PORT", None)
    else:
        os.environ["PORT"] = port
    try:
        if _argv[:2] == ["python", "-c"] and len(_argv) == 3:
            exec(compile(_argv[2], "HEALTHCHECK", "exec"), {})
    finally:
        urllib.request.urlopen = real
        if saved is None:
            os.environ.pop("PORT", None)
        else:
            os.environ["PORT"] = saved
    return asked


_asked = _health_asks("9000")
check(_asked == ["http://127.0.0.1:9000/setup"], f"the health check asks the port the app listens on ({_asked})")
_asked = _health_asks(None)
check(_asked == ["http://127.0.0.1:8097/setup"], f"and 8097 without PORT ({_asked})")

print("--- a database that cannot be opened ---")
# In a container this is the most frequent error of the first start: the mounted
# folder belongs to root, inside runs UID 10001. SQLite has a single
# sentence for it - "unable to open database file" - without the path, the reason or advice.
import tempfile as _tempfile  # noqa: E402

from jellyscope import config as _config  # noqa: E402
from jellyscope import db as _db  # noqa: E402
from jellyscope import dialect as _dialect  # noqa: E402

_where = Path(_tempfile.mkdtemp())
_obstacle = _where / "tohle-je-soubor"
_obstacle.write_text("neni to slozka", encoding="utf-8")
_settings = _dialect.DatabaseConfig(path=str(_obstacle / "jellyscope.db"))

# The first attempt is "outside a container". But the test itself can run in a container
# (development in code-server), so it is pretended - just as in test_container.
_real_container = _config._in_container
_config._in_container = lambda: False
try:
    with _db.connect(_settings):
        pass
    message = ""
    check(False, "opening an impossible database should have ended with an error")
except _db.DatabaseUnavailable as error:
    message = str(error)
    message_cs = error.translated("cs")
    check(True, "an unavailable database has an exception of its own")
except Exception as error:  # noqa: BLE001
    message = ""
    check(False, f"a bare {type(error).__name__} flew out: {error}")
finally:
    _config._in_container = _real_container

check(str(_obstacle) in message, "the message says which path it is about")
check("cannot be opened" in message, f"and what happened, in English for the console ({message[:60]!r})")
check("nejde otevřít" in message_cs and str(_obstacle) in message_cs,
      "and in Czech for a Czech browser (the interface language is in the database)")

# In a container, on top, what to do about it - otherwise a person looks for a fault in the app
# that is on the host.
os.environ["JELLYSCOPE_DOCKER"] = "1"
try:
    with _db.connect(_settings):
        pass
    in_docker = ""
except _db.DatabaseUnavailable as error:
    in_docker = str(error)
finally:
    os.environ.pop("JELLYSCOPE_DOCKER", None)

check("chown -R 10001:10001" in in_docker,
      "in a container it advises whom to hand the folder over to")
_lines = _db._why_unopenable(_obstacle, OSError("x"))
_english = _db.DatabaseUnavailable(_lines)
check(str(_english).startswith("The database") and str(_obstacle) in str(_english),
      "the message is made of keys and values: English with the path")
check(_english.translated("cs") != str(_english) and str(_obstacle) in _english.translated("cs"),
      "and the same path in another language")
check("10001" not in message,
      "and outside a container it does not confuse with that advice (there is no UID 10001 there)")

# The second way it happens: the database file exists and can be opened,
# but it cannot be written to. So the connection comes about and falls over only at the first write
# ("attempt to write a readonly database") - from outside it is the same fault
# and the same answer belongs to it.
import os as _os  # noqa: E402
import sqlite3 as _sqlite3  # noqa: E402
import stat as _stat  # noqa: E402

_where2 = Path(_tempfile.mkdtemp())
_file = _where2 / "jenprocteni.db"
_connection = _sqlite3.connect(_file)
_connection.execute("CREATE TABLE zkouska (a INTEGER)")
_connection.commit()
_connection.close()
_os.chmod(_file, _stat.S_IREAD)

try:
    with _db.connect(_dialect.DatabaseConfig(path=str(_file))):
        pass
    check(False, "a read-only database should have ended with an error")
    _message2 = ""
except _db.DatabaseUnavailable as error:
    _message2 = str(error)
    check(True, "a read-only database has an exception of its own too")
except Exception as error:  # noqa: BLE001
    _message2 = ""
    check(False, f"a bare {type(error).__name__} flew out: {error}")
check("read-only" in _message2,
      "and the message says the file is read-only")
_os.chmod(_file, _stat.S_IWRITE | _stat.S_IREAD)

# PostgreSQL configured, the driver missing (an image built with PSYCOPG=0, say): the start used to end in a
# traceback, and with `restart: unless-stopped` the container went round in circles, the page never shown. It is
# an unavailable database like the others - the launcher answers it with the stand-in page.
_saved_modules = {name: sys.modules.get(name) for name in ("psycopg", "psycopg_pool")}
sys.modules.update(psycopg=None, psycopg_pool=None)
try:
    _db.init_db(_dialect.DatabaseConfig(kind=_dialect.POSTGRES, host="127.0.0.1", port=1, database="none",
                                        user="none", password=""))
    _no_driver = None
except Exception as error:  # noqa: BLE001
    _no_driver = error
finally:
    for _name, _module in _saved_modules.items():
        if _module is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _module
check(isinstance(_no_driver, _db.DatabaseUnavailable) and "psycopg" in str(_no_driver),
      f"PostgreSQL without its driver is an unavailable database, not a crash of the start "
      f"({type(_no_driver).__name__}: {str(_no_driver)[:60]!r})")

print()
print("--- the stand-in page without a database ---")
# The container is to come up even so and say what is going on - otherwise the browser
# has only "unable to connect" and the answer lies in the log nobody sent.
from starlette.testclient import TestClient as _TestClient  # noqa: E402

from jellyscope import outage  # noqa: E402

_error = _db.DatabaseUnavailable([("The database {path} cannot be opened: the folder {folder} cannot be written to.",
                                   {"path": "/app/data/jellyscope.db", "folder": "/app/data"})])
_message_en = "The database /app/data/jellyscope.db cannot be opened: the folder /app/data cannot be written to."
_message_cs = "Databázi /app/data/jellyscope.db nejde otevřít: do složky /app/data se nesmí zapisovat."
with _TestClient(outage.app(_error)) as _client:
    for _path in ("/", "/login", "/settings?section=data", "/cokoliv"):
        _response = _client.get(_path)
        check(_response.status_code == 503,
              f"{_path} answers 503 ({_response.status_code})")
    _body = _client.get("/").text
    _body_cs = _client.get("/", headers={"Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8"}).text
    _body_es = _client.get("/", headers={"Accept-Language": "fr;q=1, es;q=0.7"}).text
    _body_none = _client.get("/", headers={"Accept-Language": "fr, ja;q=0.5"}).text

check(_message_en in _body and "cannot open its database" in _body,
      "without a browser language the page is English (the interface language is in the database)")
check("Databázi" not in _body, "and only English")
check(_message_cs in _body_cs and "nemůže otevřít databázi" in _body_cs and 'lang="cs"' in _body_cs,
      "a Czech browser gets the page in Czech")
check(_message_en in _body_cs and _body_cs.index(_message_cs) < _body_cs.index(_message_en),
      "and English below it")
check("no puede abrir su base de datos" in _body_es,
      "the first wanted language with a translation is taken (French has none, Spanish has)")
check(_body_none == _body, "a browser with no known language gets the English page")
check("docker compose restart" in _body and "docker compose restart" in _body_cs, "it also says what to do afterwards")
check(outage.browser_language("de;q=0, cs;q=0.5") == "cs" and outage.browser_language("") == "en"
      and outage.browser_language("ES-es") == "es", "a weight of zero, an empty header and a region are understood")

# The message carries paths from the system. They belong in the page as text - otherwise
# a folder's name could smuggle tags into the page.
_attack = outage.page('<script>alert(1)</script>')
check("<script>alert(1)</script>" not in _attack, "the message's text is escaped")
check("&lt;script&gt;" in _attack, "and passes as text")

print("--- docker compose ---")
# Compose passes the container only what is listed in the file. What is
# missing there does not get into the container - and it shows only on the server, where
# the app behaves differently than at home.
compose = (PROJECT / "docker-compose.yml").read_text(encoding="utf-8")
for needle, why in [
    ("SECRET_KEY: \"${SECRET_KEY:?", "without a signing key the container does not start"),
    ("JELLYSCOPE_DEMO: \"${JELLYSCOPE_DEMO:-0}\"",
     "the demo mode can be switched on, but does not switch on by itself"),
    ("HOST: \"0.0.0.0\"", "inside the container it listens on all addresses"),
    (":/app/data", "the data lies on the host, not in the container"),
    ("no-new-privileges:true", "the container does not add rights to itself"),
]:
    check(needle in compose, f"compose: {why}")

# PostgreSQL in the image: the driver comes with the build, at the version of requirements-postgres.txt. Installed
# by hand into a running container it was gone with the next `docker compose up -d --build` - the documented
# update - while data/database.json still pointed at PostgreSQL.
_driver_run = re.search(r'^RUN if \[ "\$PSYCOPG" = "1" \]; then(?:[^\n]*\\\n)*[^\n]*', dockerfile, re.M)
check(re.search(r"^ARG PSYCOPG=1$", dockerfile, re.M) is not None and _driver_run is not None
      and "-r requirements-postgres.txt" in _driver_run.group(0),
      "the image carries the PostgreSQL driver from requirements-postgres.txt (build argument PSYCOPG, on by default)")
check(_driver_run is not None and "||" in _driver_run.group(0),
      "a platform without its packages still builds (SQLite only), as with install.sh")
check(re.search(r"^COPY [^\n]*\brequirements-postgres\.txt\b", dockerfile[:_driver_run.start() if _driver_run else 0],
                re.M) is not None,
      "and the file is in the image by then")
check('PSYCOPG: "${PSYCOPG:-1}"' in compose, "compose passes PSYCOPG from .env to the build")
check("| `PSYCOPG` |" in (PROJECT / "DEPLOY.md").read_text(encoding="utf-8"),
      "DEPLOY.md lists PSYCOPG with the other build switches")

# The update in a container is `git pull && docker compose up -d --build` (DEPLOY.md, updates.py). A base
# image already on the machine is fetched again only with build.pull - without it Python, OpenSSL, ffmpeg
# and libpq stay at the versions of the first build, and the apt layer comes from the cache for ever.
_build = re.search(r"^    build:\n((?:(?:      .*| *#.*)?\n)+)", compose, re.M)
check(_build is not None and re.search(r"^      pull: true\s*$", _build.group(1), re.M) is not None,
      "compose: a rebuild fetches the newest base image (build.pull)")

# The demo mode must not switch on by itself - a public demo is another thing than
# a home installation, and mixing them up means either settings that do not work, or a
# publicly reachable configuration.
check(cfg.load_config(reload=True).demo_mode is False,
      "without the variable the demo mode is off")
os.environ["JELLYSCOPE_DEMO"] = "1"
check(cfg.load_config(reload=True).demo_mode is True,
      "and with JELLYSCOPE_DEMO=1 on")
os.environ.pop("JELLYSCOPE_DEMO", None)

print("--- .env.example ---")
example = (PROJECT / ".env.example").read_text(encoding="utf-8")
check("SECURE_COOKIES=" in example, "SECURE_COOKIES is mentioned in the example")
check("FORWARDED_ALLOW_IPS=" in example, "FORWARDED_ALLOW_IPS is mentioned in the example")
# Were it switched on in the example, a person would copy it, run it on
# http://localhost and try in vain to sign in.
check("SECURE_COOKIES=1" not in example,
      "SECURE_COOKIES is NOT switched on in the example")
check("JELLYSCOPE_PASSWORD" not in example,
      "the example no longer has the old password (it moved into the database)")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
