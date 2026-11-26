# -*- coding: utf-8 -*-
r"""deploy/update.sh really run, not only read.

The static checks in test_deploy.py guard that something IS in the script.
Here the script is run over its own small "server" (a bare git repo) and a
clone of an installation, to show what it really DOES:

* a plain update finishes even without a database and without old backups;
* an update that brings a new update.sh goes on with the new script, which
  does not pull again and knows which version it came from;
* a new version that wants a newer Python is not pulled at all;
* nonsense in JELLYSCOPE_UPDATE_PRED is not used.

Instead of .venv/bin/python there is a small shim: pip only pretends, the
rest goes to the Python running this test. No restart takes place (no
service) - the script reports it and ends with zero.

Without bash and git the test is skipped.

Run:
    .venv/bin/python tests/test_update_sh.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


BASH = shutil.which("bash")
GIT = shutil.which("git")
if not BASH or not GIT:
    print("SKIPPED  bash or git is missing")
    print()
    print("DONE - failures: 0")
    sys.exit(0)

# An own identity and LF: the test must not take a name or an e-mail from the
# global configuration and core.autocrlf from Windows would add CR to the ends
# of the script's lines.
GIT_C = ["-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main"]

root = Path(tempfile.mkdtemp())
server = root / "server.git"
author = root / "autor"
app = root / "app"


def git(*args: str, where: Path | None = None) -> str:
    command = [GIT, *GIT_C] + (["-C", str(where)] if where else []) + list(args)
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()


def publish(message: str) -> None:
    git("add", "-A", where=author)
    git("commit", "--quiet", "-m", message, where=author)
    git("push", "--quiet", where=author)


def head() -> str:
    return git("rev-parse", "--short", "HEAD", where=app)


def run_update(**environment: str) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("JELLYSCOPE_UPDATE_")}
    env.update(environment)
    result = subprocess.run([BASH, str(app / "deploy" / "update.sh")], env=env,
                            capture_output=True, timeout=120)
    return result.returncode, result.stdout.decode("utf-8", "replace") + \
        result.stderr.decode("utf-8", "replace")


git("init", "--quiet", "--bare", str(server))
git("clone", "--quiet", str(server), str(author))
(author / "deploy").mkdir()
for name in ("update.sh", "python-minimum"):
    (author / "deploy" / name).write_bytes((PROJECT / "deploy" / name).read_bytes())
(author / "run.py").write_bytes(b"print('app')\n")
(author / "requirements.txt").write_bytes(b"")
(author / "README").write_bytes(b"v1\n")
git("add", "-A", where=author)
git("commit", "--quiet", "-m", "v1", where=author)
git("push", "--quiet", "-u", "origin", "main", where=author)

# `clone -c` writes the option into the clone's configuration - it holds for
# the git update.sh itself runs later too.
git("clone", "--quiet", "-c", "core.autocrlf=false", str(server), str(app))
shim = app / ".venv" / "bin" / "python"
shim.parent.mkdir(parents=True)
# pip writes down what it was asked into .venv/bin/pip.log and fails when the call has FAKE_PIP_FAILS in it;
# `import psycopg` (is the PostgreSQL driver installed?) answers FAKE_PSYCOPG.
shim.write_bytes(
    ("#!/bin/bash\n"
     'if [[ "$1" == "-m" && "$2" == "pip" ]]; then\n'
     '    printf "%s\\n" "$*" >> "$(dirname "$0")/pip.log"\n'
     '    [[ -z "${FAKE_PIP_FAILS:-}" || "$*" != *"$FAKE_PIP_FAILS"* ]]; exit $?\n'
     'fi\n'
     'if [[ "$1" == "-c" && "$2" == "import psycopg" ]]; then [[ "${FAKE_PSYCOPG:-0}" == "1" ]]; exit $?; fi\n'
     f'exec "{Path(sys.executable).as_posix()}" "$@"\n').encode("utf-8"))
shim.chmod(0o755)

print("--- a plain update, without a database and without old backups ---")
(author / "README").write_bytes(b"v2\n")
publish("v2")
before = head()
code, output = run_update()
after = head()
check(code == 0 and "Dependencies" in output,
      f"it finishes to the end (code {code}, the last lines: {output.strip().splitlines()[-2:]})")
check(before != after and f"{before} → {after}" in output, f"it pulls the new version ({before} → {after})")

print("--- an update brings a new update.sh ---")
with (author / "deploy" / "update.sh").open("ab") as file:
    file.write(b"\n# changed in v3\n")
(author / "README").write_bytes(b"v3\n")
publish("v3")
before = head()
code, output = run_update()
after = head()
second_part = output.split("continuing with the new version", 1)[-1]
check(code == 0 and "continuing with the new version" in output,
      f"it goes on with the new version of the script (code {code})")
check(f"pulled by the previous version of this script: {before} → {after}" in second_part,
      f"the new script knows which version it came from ({before} → {after})")
check("Fast-forward" not in second_part and output.count("Database backup") == 2
      and "done a moment ago" in second_part,
      "the new script does not pull again and does not back up a second time")
check((app / "README").read_bytes() == b"v3\n", "the installation has the new version")

print("--- a new version wants a newer Python ---")
(author / "deploy" / "python-minimum").write_bytes(b"3.99\n")
(author / "README").write_bytes(b"v4\n")
publish("v4")
before = head()
code, output = run_update()
check(code != 0 and "needs 3.99+" in output and "Nothing has been pulled" in output,
      f"it ends with an explanation (code {code})")
check(head() == before and (app / "README").read_bytes() == b"v3\n",
      "and pulls nothing - the application goes on running on what it has")

print("--- nonsense in JELLYSCOPE_UPDATE_PRED ---")
(author / "deploy" / "python-minimum").write_bytes(b"3.11\n")
publish("v5")
code, output = run_update(JELLYSCOPE_UPDATE_ZNOVU="1", JELLYSCOPE_UPDATE_PRED="x; touch pwned")
check(code == 0 and f"pulled by the previous version of this script: {head()} → {head()}" in output
      and not (app / "pwned").exists() and not Path("pwned").exists(),
      "it is not used, the version in the installation is taken as the starting one")

print("--- cleaning up old backups ---")
backups = app / "data" / "before-update"
backups.mkdir(parents=True, exist_ok=True)
for k in range(12):
    file = backups / f"jellyscope-{k:02d}.db"
    file.write_bytes(b"x")
    os.utime(file, (1_700_000_000 + k * 60,) * 2)
code, output = run_update()
kept = sorted(p.name for p in backups.iterdir())
check(code == 0 and len(kept) == 10 and "jellyscope-00.db" not in kept
      and "jellyscope-01.db" not in kept,
      f"it keeps the ten newest (code {code}, kept {len(kept)})")

print("--- the PostgreSQL driver at its pinned version ---")
# The driver is not in requirements.txt: a platform without its packages must not fail every update. It is pinned
# in requirements-postgres.txt, and update.sh brings it to that version apart - only where it is installed, and a
# failure is only a warning (the installed driver keeps working).
pip_log = shim.parent / "pip.log"
for present, fails, label in ((True, False, "where it is installed, it is brought to the pinned version"),
                              (False, False, "where it is not, it is not installed by an update"),
                              (True, True, "a failure is a warning, the update goes on")):
    pip_log.unlink(missing_ok=True)
    code, output = run_update(FAKE_PSYCOPG="1" if present else "0",
                              **({"FAKE_PIP_FAILS": "requirements-postgres.txt"} if fails else {}))
    calls = pip_log.read_text(encoding="utf-8").splitlines() if pip_log.exists() else []
    driver = [i for i, call in enumerate(calls)
              if call.startswith("-m pip install") and call.endswith(f"-r {app.as_posix()}/requirements-postgres.txt")]
    packages = [i for i, call in enumerate(calls) if call.endswith(f"-r {app.as_posix()}/requirements.txt")]
    if not present:
        ok = code == 0 and packages and not driver
    elif not fails:
        ok = code == 0 and packages and driver and driver[0] > packages[0] and "could not be updated" not in output
    else:
        ok = code == 0 and driver and "PostgreSQL driver could not be updated" in output
    check(bool(ok), f"{label} (code {code}, pip: {calls})")

print("--- the app runs, but shows the outage page ---")
# A failed conversion (or a database that cannot be opened) leaves the service "active" with every address
# answering 503 - the outage page. That is no "Done".
stubs = root / "stubs"
stubs.mkdir()
for name, body in (("systemctl", 'case "$*" in *"is-active"*) echo active;; esac\nexit 0\n'),
                   ("journalctl", 'echo "the database could not be converted to English names"\n'),
                   ("curl", 'printf 503\n'), ("sleep", "exit 0\n")):
    (stubs / name).write_bytes(("#!/bin/bash\n" + body).encode("utf-8"))
    (stubs / name).chmod(0o755)
code, output = run_update(PATH=f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}")
check(code != 0 and "outage page" in output and "before-conversion" in output and "Done." not in output,
      f"the update says the app shows the outage page and how to go back ({code})")

print("--- the new script goes on and the app did not come up ---")
# Started again by its previous version, the script skips the backup step - the way back must still be
# printed whole, down to the git reset and the restart command (set -u: no unbound variable).
failed = root / "stubs-failed"
failed.mkdir()
for name, body in (("systemctl", 'case "$*" in *"is-active"*) echo failed;; esac\nexit 0\n'),
                   ("journalctl", 'echo "the app stopped"\n'), ("sleep", "exit 0\n")):
    (failed / name).write_bytes(("#!/bin/bash\n" + body).encode("utf-8"))
    (failed / name).chmod(0o755)
code, output = run_update(JELLYSCOPE_UPDATE_ZNOVU="1", JELLYSCOPE_UPDATE_PRED=head(),
                          PATH=f"{failed}{os.pathsep}{os.environ.get('PATH', '')}")
check(code != 0 and "unbound variable" not in output and f"reset --hard {head()}" in output
      and "systemctl --user restart jellyscope" in output and str(backups) in output
      and "The update failed." in output,
      f"the way back is printed whole, with the backup folder ({code}, the last lines: "
      f"{output.strip().splitlines()[-3:]})")

print("--- the app did not come up: the way back after the database's conversion ---")
# 1.9.0 renames the database to English names at its first start; the previous version cannot run on a
# converted database, so a bare `git reset` to it is no way back - the backup from before the update is
# (README, "Updating to 1.9.0"). The branch needs a failed restart, so it is checked in the script's text.
script = (PROJECT / "deploy" / "update.sh").read_text(encoding="utf-8")
advice = script[script.index("way_back() {"):script.index('die "The update failed."')]
failed_start = script[script.index("if [[ $RUNNING -eq 0 ]]; then"):script.index("if [[ $RUNNING -eq 1 ]]; then")]
check("before-conversion" in advice and "$BACKUP_DIR" in advice and "Updating to 1.9.0" in advice
      and advice.index("before-conversion") < advice.index("reset --hard") and "way_back" in failed_start,
      "a failed start names the backups from before the conversion before it offers git reset")

shutil.rmtree(root, ignore_errors=True)
print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
