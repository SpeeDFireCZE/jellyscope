"""Watching for a new version by the releases on GitHub.

Three things worth knowing about it:

**By default it is off.** It is the only outgoing connection besides
Jellyfin (and downloading GeoLite2), and a person who hosts their own server
has the right to know where their application calls - and to decide whether
at all. It is turned on in Settings.

**It installs nothing.** It only says "1.2.0 is out, you have 1.1.0" and
links to the release page. Updating stays with `deploy/update.sh`, which can
download the new version and restart the service.

**It asks once a day**, not on every page load. The result is stored in the
settings, so the pages only read it from the database. GitHub's public API
has a limit of 60 queries an hour per address; one a day fits into it even
if more applications were running.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import re
import signal
import sys
import sysconfig
import tempfile
from datetime import datetime, timedelta, UTC
from typing import Any

from . import db, i18n

# The application's folder - it is updated from here and requirements.txt lies here.
ROOT = pathlib.Path(__file__).resolve().parent.parent

log = logging.getLogger("jellyscope.updates")

# The list of releases, newest first: whoever skipped a few is to see what all of them bring (one query).
RELEASES_API = "https://api.github.com/repos/SpeeDFireCZE/jellyscope/releases?per_page=30"
PAGE = "https://github.com/SpeeDFireCZE/jellyscope/releases/latest"
# The list of every release - where the window sends a person for the releases it leaves out.
RELEASES_PAGE = "https://github.com/SpeeDFireCZE/jellyscope/releases"

# Keys in the settings. The result is stored, so the network need not be
# asked on every page load.
# Turning on and the schedule are held by the task "Kontrola aktualizaci"
# (see tasks.py), so the key is its. Two places where the same is turned on
# are a trap: a person turns off one and the other keeps running.
ENABLED_KEY = "task_updates_enabled"
LAST_CHECK_KEY = "update_last_check"
FOUND_VERSION_KEY = "update_latest_version"
FOUND_URL_KEY = "update_latest_url"
# The releases newer than the running version, with their notes from GitHub (markdown), as JSON
# {"releases": [{"version", "url", "published_at", "body"}], "more": the newest version left out or ""} - see
# skipped_releases. They are
# shown in the window that opens by clicking the new version's marker: a person is to see what they install
# before they click it. A version before 1.9.0 stored the markdown of the newest release here.
FOUND_NOTES_KEY = "update_latest_notes"
# How many translation files changed on `main` since the commit that runs.
# Translations from Weblate come by merging, not by releases, so the check by
# the version number never catches them - it is watched apart.
NEW_TRANSLATIONS_KEY = "update_translations_pending"

# Comparing commits on GitHub: what is on `main` and not with us yet.
COMPARE_URL = ("https://api.github.com/repos/SpeeDFireCZE/jellyscope"
               "/compare/{base}...main")
TRANSLATIONS_FOLDER = "jellyscope/translations/"

# A longer description nobody reads anyway, and a value belongs in the
# settings, not an article. We cut it.
MAX_NOTES = 20000
# More releases than that are on the release page; the window says so.
MAX_RELEASES = 20

INTERVAL_HOURS = 24


def is_enabled() -> bool:
    return db.get_setting(ENABLED_KEY, "0") == "1"


def _version_number(text: Any) -> tuple[int, ...]:
    """A version as numbers, so it can be compared.

    "v1.2.10" -> (1, 2, 10). Versions cannot be compared as strings:
    "1.10.0" is less than "1.9.0" when taken character by character.
    Anything that is not a number (rc, beta) is dropped - it has no effect
    on the question "is anything newer out".
    """
    numbers = re.findall(r"\d+", str(text or ""))
    return tuple(int(c) for c in numbers[:4]) or (0,)


def is_newer(found: Any, own: Any) -> bool:
    """Is the found version newer than ours?"""
    return _version_number(found) > _version_number(own)


def _now() -> datetime:
    return datetime.now(UTC)


def _due() -> bool:
    """Has a day passed since the last check?"""
    last = db.get_setting(LAST_CHECK_KEY, "")
    if not last:
        return True
    try:
        when = datetime.strptime(last, db.TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return True
    return _now() - when >= timedelta(hours=INTERVAL_HOURS)


async def check(forced: bool = False) -> dict[str, Any]:
    """Asks GitHub for the releases: the newest one, and the notes of every one newer than the running version.

    `forced` goes around the daily interval - that is the button
    "Zkontrolovat teď". Without it the network is asked only when a day has
    passed since last time.
    """
    from . import __version__

    # When to ask is decided by the task's schedule - from here only a check
    # that nobody sends queries more often than is decent.
    if not forced and (not is_enabled() or not _due()):
        return state()

    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                RELEASES_API, headers={"Accept": "application/vnd.github+json"})
            response.raise_for_status()
            listed = response.json()
        newest, _ = skipped_releases(listed, "0")
        if not newest:
            raise i18n.TranslatableError("GitHub lists no release.")
    except Exception as error:  # noqa: BLE001 - the network fails in many ways
        log.warning("the version check could not be made: %s", error)
        return {**state(), "status": "error", "message": i18n.error_text(error)}

    version = newest[0]["version"]
    skipped, left_out = skipped_releases(listed, __version__)
    db.set_setting(LAST_CHECK_KEY, _now().strftime(db.TIME_FORMAT))
    db.set_setting(FOUND_VERSION_KEY, version)
    db.set_setting(FOUND_URL_KEY, newest[0]["url"])
    db.set_setting(FOUND_NOTES_KEY, json.dumps({"releases": skipped, "more": left_out}, ensure_ascii=False))

    if is_newer(version, __version__):
        log.info("a new version %s is available (running %s)", version, __version__)

    # Translations apart: they have no version number to tell them by.
    # When it fails, what was there stays - better than throwing away the
    # last known state because of one network outage.
    count = await _new_translations(client_factory=httpx.AsyncClient)
    if count is not None:
        db.set_setting(NEW_TRANSLATIONS_KEY, str(count) if count else "")
        if count:
            log.info("new translations on GitHub: %s files", count)
    return {**state(), "status": "ok"}


async def _own_commit() -> str:
    """The commit the application runs from. Empty when it is not git."""
    if not (ROOT / ".git").is_dir():
        return ""
    result = await _git("rev-parse", "HEAD")
    if result["code"] != 0:
        return ""
    return result["output"].strip()


async def _new_translations(client_factory: Any) -> int | None:
    """How many translation files changed on `main` since our commit.

    None means "I do not know" - it is not git, GitHub does not know the
    commit (an own branch, say) or the network does not work. That differs
    from zero: zero says "nothing new", None says "do not ask me".

    Only files in the folder with translations count. A commit to the
    documentation or to the code has nothing to report here - a release is
    for that.
    """
    if why_no_update():
        return None
    commit = await _own_commit()
    if not commit:
        return None
    try:
        async with client_factory(timeout=20) as client:
            response = await client.get(
                COMPARE_URL.format(base=commit),
                headers={"Accept": "application/vnd.github+json"})
            if response.status_code == 404:
                # GitHub does not know our commit - an own branch, local work.
                return None
            response.raise_for_status()
            data = response.json()
    except Exception as error:  # noqa: BLE001 - the network fails in many ways
        log.warning("the comparison with GitHub failed: %s", error)
        return None
    return _translation_only_count(
        [str(entry.get("filename") or "") for entry in data.get("files") or []])


def _translation_only_count(files: list[str]) -> int:
    """How many of them are translations - but only when nothing else is there.

    The update is a `git pull` of the whole `main`. Were code lying there
    next to the translations (a release that has not come out yet - the tag
    is there, CI failed, the Release was not created), we would report "new
    translations" and a click would download that code too. So: anything
    outside the folder with translations means "wait for the release" and
    the translations are not reported at all.
    """
    translations = [s for s in files if s.startswith(TRANSLATIONS_FOLDER)]
    if len(translations) != len(files):
        if translations:
            log.info("main carries code alongside the translations; not reporting them, "
                     "they come with the release")
        return 0
    return len(translations)


def state() -> dict[str, Any]:
    """What we know about versions - without touching the network. This is what the pages read."""
    from . import __version__

    found = db.get_setting(FOUND_VERSION_KEY, "")
    releases, left_out = stored_releases()
    return {
        "enabled": is_enabled(),
        "version": __version__,
        "found_one": found,
        "url": db.get_setting(FOUND_URL_KEY, "") or PAGE,
        "checked_at": db.get_setting(LAST_CHECK_KEY, ""),
        "is_newer": bool(found) and is_newer(found, __version__),
        # Translations that were added on `main` since our commit. They are
        # shown only when no new release is available - that includes them.
        "new_translations": int(db.get_setting(NEW_TRANSLATIONS_KEY, "") or 0),
        # The releases still newer than the running one (after an update the stored list holds older ones
        # until the next check), each with its notes as HTML; the date as stored (ISO), the template formats it.
        "releases": [{"version": r["version"], "url": r["url"] or PAGE, "date": r["published_at"][:10],
                      "html": notes_html(r["body"])} for r in releases if is_newer(r["version"], __version__)],
        # Some were left out - and they are still newer than the running version (not after an update past them).
        "more_releases": bool(left_out) and is_newer(left_out, __version__),
        "releases_page": RELEASES_PAGE,
        # Updating from the browser works only where there is something to
        # update from and where it makes sense. When not, instead of the
        # button it is said why - see why_no_update().
        "can_update": can_update(),
        "no_update_reason": why_no_update(),
    }


def can_update() -> bool:
    """Can it be updated right from the application?"""
    return why_no_update() == ""


def why_no_update() -> str:
    """Why updating from the browser does not work. Empty = it works.

    It returns a finished sentence, because "it does not work" without a
    reason sends a person looking for the fault on their side. Each of these
    cases has a different right answer, and it is to be said straight away.
    """
    from .config import load_config

    config = load_config()
    if config.demo_mode:
        return "This is a demo, so it cannot be updated."

    # We refuse only OUR image, not every container.
    #
    # In our image the application is part of a layer: `git pull` could pass
    # (when somebody built the image with .git too), but the change would
    # live until the next rebuild and then the old version would quietly
    # come back. That is worse than a button that does not work.
    #
    # In somebody else's container (LXC, a foreign image, Podman) the
    # application can be installed from git quite normally - and there
    # `git pull` works as anywhere else. There used to sit the message
    # "rebuild the image", which made no sense: that person has no such image.
    if config.our_image:
        return ("In a container the update is a rebuild: "
                "git pull && docker compose up -d --build")

    if not (ROOT / ".git").is_dir():
        return ("Updating from the browser only works where the app came "
                "from git. Otherwise deploy/update.sh is the way.")

    # An update writes the code (git) and the packages (pip). The systemd service of deploy/ may write only
    # into data/ (ProtectSystem=strict) - there git could not even fetch, and every click ended in "try again
    # in a moment" with the real cause nowhere in sight.
    packages = pathlib.Path(sysconfig.get_path("purelib"))
    if not all(os.access(folder, os.W_OK) for folder in (ROOT, ROOT / ".git", packages)):
        return ("The app may not write into its own folder here (a service allowed to write only into data/, "
                "say), so it cannot update itself. Update it with deploy/update.sh.")
    return ""


def skipped_releases(found: Any, running: str) -> tuple[list[dict[str, Any]], str]:
    """The releases of GitHub's list newer than `running`, newest first (by version, not by the list's order: a
    fix of an older line can come out later), and the newest version left out ("" when none was).

    Not a release: a draft, a pre-release, a tag without a number ("latest"), anything that is no object. At
    most MAX_RELEASES; the notes together at most MAX_NOTES - the newest is always there (its notes cut, as
    before), an older one only whole and only while the ones before it fit, so the list has no gaps.
    """
    candidates: dict[str, dict[str, Any]] = {}
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or item.get("draft") or item.get("prerelease"):
            continue
        version = str(item.get("tag_name") or "").lstrip("vV")
        if not re.match(r"\d", version) or not is_newer(version, running) or version in candidates:
            continue
        url = str(item.get("html_url") or "")
        candidates[version] = {"version": version, "url": url if url.startswith("https://github.com/") else PAGE,
                               "published_at": str(item.get("published_at") or ""), "body": str(item.get("body") or "")}
    ordered = sorted(candidates.values(), key=lambda r: _version_number(r["version"]), reverse=True)
    kept: list[dict[str, Any]] = []
    total = 0
    for release in ordered[:MAX_RELEASES]:
        if kept and total + len(release["body"]) > MAX_NOTES:
            break
        kept.append(release if kept else {**release, "body": release["body"][:MAX_NOTES]})
        total += len(kept[-1]["body"])
    return kept, ordered[len(kept)]["version"] if len(kept) < len(ordered) else ""


def stored_releases() -> tuple[list[dict[str, Any]], str]:
    """The releases the last check stored, and the newest version left out ("" when none was). Markdown stored
    by a version before 1.9.0 is one release (the found version) until the next check - even one that starts
    with a link ([…](…)): only an object is ours. Broken JSON is none, never an error (state() runs on every
    page)."""
    text = db.get_setting(FOUND_NOTES_KEY, "") or ""
    if not text.strip():
        return [], ""
    if not text.lstrip().startswith("{"):
        return [{"version": db.get_setting(FOUND_VERSION_KEY, ""), "url": db.get_setting(FOUND_URL_KEY, "") or PAGE,
                 "published_at": "", "body": text}], ""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return [], ""
    if not isinstance(data, dict) or not isinstance(data.get("releases"), list):
        return [], ""
    releases = [{key: str(r.get(key) or "") for key in ("version", "url", "published_at", "body")}
                for r in data["releases"] if isinstance(r, dict)]
    left_out = data.get("more")
    return releases, left_out if isinstance(left_out, str) else ""


def notes_html(text: str) -> str:
    """The release description from markdown to HTML - see release_notes (escaped first, then the markdown
    GitHub release notes really use)."""
    from . import release_notes
    return release_notes.to_html(text)


async def update() -> dict[str, Any]:
    """Downloads the new version and installs the dependencies. Does not restart.

    Exactly two steps of `deploy/update.sh` are done - `git pull` and
    `pip install` - and before them a dry run of whether the new version's
    packages will install (`_package_check`). The caller asks for the
    restart itself (see web.settings._schedule_restart), because the
    application can replace its process and does not need a service manager
    for that.

    We do not make a backup here: either the daily task runs, or a person
    starts it with a button. Making it quietly on every update would mean a
    copy of the database nobody asked for.

    When there are own changes in the folder, the update is NOT done -
    `git pull` would either overwrite them or end with a conflict in the
    middle. We would rather say so.
    """
    reason = why_no_update()
    if reason:
        return {"status": "error", "message": reason}

    changes = await _git("diff", "--quiet")
    if changes["code"] != 0:
        return {"status": "error",
                "message": "There are local changes in the app "
                           "folder. The update would lose them, so I did not run it."}

    # One fetch and a remembered commit. On it the translations and the
    # packages are checked and exactly that one is then downloaded
    # (`merge --ff-only <commit>`) - `git pull` would fetch again, and what
    # appeared on GitHub meanwhile would pass without the trial.
    unavailable = {"status": "error",
                   "message": "Could not find out what the update would fetch. Try again in "
                              "a moment."}
    if (await _git("fetch", "--quiet"))["code"] != 0:
        return unavailable
    verified = (await _git("rev-parse", "@{u}"))["output"].strip()
    if not re.fullmatch(r"[0-9a-f]{7,64}", verified):
        return unavailable

    # Without a new release out only translations may be downloaded. The
    # check guarded it already when comparing, but between the check (in the
    # morning) and the click (in the evening) main could have moved - so it
    # is checked again, on what would really be downloaded.
    if not state()["is_newer"]:
        extra = await _incoming_beyond_translations(verified)
        if extra is None:
            return {"status": "error",
                    "message": "Could not find out what the update would fetch. Try again in "
                               "a moment."}
        if extra:
            return {"status": "error",
                    "message": "GitHub has more than translations since "
                               "your version (code not yet released). "
                               "The update waits for the release."}

    failed = await _package_check(verified)
    if failed:
        return failed

    # The way back, should the packages fail after the download (see _put_back).
    running = (await _git("rev-parse", "HEAD"))["output"].strip()
    if not re.fullmatch(r"[0-9a-f]{7,64}", running):
        return unavailable

    pull = await _git("merge", "--ff-only", verified)
    if pull["code"] != 0:
        return {"status": "error", "message": pull["output"][-400:]}

    pip = await _pip("-r", str(ROOT / "requirements.txt"), limit=PIP_TIMEOUT_SECONDS)
    if pip["code"] != 0:
        return await _put_back(running, pip["output"])
    # The PostgreSQL driver is not in requirements.txt (a platform without its packages must not fail every
    # update). Where it is installed, it goes to the version of requirements-postgres.txt, as with update.sh - or
    # it would stay at the one of the day of the installation for good. A failure leaves the installed one working.
    if db.psycopg_available():
        driver = await _pip("-r", str(ROOT / "requirements-postgres.txt"), limit=PIP_TIMEOUT_SECONDS)
        if driver["code"] != 0:
            log.warning("the PostgreSQL driver could not be updated, the installed one keeps working")

    log.info("update downloaded: %s", pull["output"].strip().splitlines()[-1:])
    # What was just downloaded is no longer "new". Without this the message
    # about new translations would hang until the next check, even though
    # they are in the application already.
    db.set_setting(NEW_TRANSLATIONS_KEY, "")
    return {"status": "ok", "output": pull["output"]}


async def _put_back(running: str, output: str) -> dict[str, Any]:
    """The packages failed after the download: the code goes back to the commit that runs.

    Left in place, the new code would start at the next restart (for any reason) with the old packages and
    could fail at import, while the old one goes on running from memory until then.
    """
    said = output.strip()[-400:]
    if (await _git("reset", "--keep", running))["code"] == 0:
        return {"status": "error",
                "message": "The packages of the new version could not be installed, so the previous version "
                           "was put back and keeps running. What pip says: {output}",
                "parameters": {"output": said}}
    return {"status": "error",
            "message": "The packages of the new version could not be installed, and the previous version could "
                       "not be put back. Before the next restart run in the app folder: git reset --keep {commit} "
                       "- What pip says: {output}",
            "parameters": {"output": said, "commit": running}}


def _python_error(required: str) -> dict[str, Any]:
    return {"status": "error",
            "message": "The new version needs Python {required} or newer, this server runs "
                       "{current}. Nothing was downloaded, this version keeps running. "
                       "Install Python {required} next to the current one (Ubuntu: sudo "
                       "apt install python{required} python{required}-venv), then run git "
                       "pull and bash deploy/install.sh in the app folder and restart the "
                       "service - the environment is built on the new Python and the "
                       "database and .env stay.",
            "parameters": {"required": required, "current": "%d.%d" % sys.version_info[:2]}}


# How many files the new version's requirements.txt may pull in through -r / -c -
# a safeguard against references in a circle.
MAX_EMBEDDED = 10


async def _package_check(verified: str) -> dict[str, Any] | None:
    """Will the new version's packages install on this server? None = yes.

    It is tried as a dry run (`pip install --dry-run`) with `requirements.txt`
    of the new version, still BEFORE downloading. The code used to be
    downloaded first and only pip found out it would not work - 1.8.1 needs
    Python 3.11 and on 3.10 the button ended with a message that did not show
    the cause. This way nothing is downloaded, this version keeps running and
    the message says what to do.

    When it cannot be checked (an old pip without --dry-run), nothing is held
    up - the update runs as before and pip reports a possible error itself.
    Without a change of `requirements.txt` or `constraints.txt` (the versions of
    what the packages pull in; only translations changed, say) nothing is
    tried - the packages are the same ones that already run.

    First the Python minimum from the new version's `deploy/python-minimum`:
    a clear message without deciphering pip's output. Everything is taken
    from the verified commit.
    """
    minimum = await _git_content("show", f"{verified}:deploy/python-minimum")
    required = minimum["output"].strip() if minimum["code"] == 0 else ""
    if re.fullmatch(r"3\.[0-9]{1,2}", required) and tuple(
            int(x) for x in required.split(".")) > sys.version_info[:2]:
        return _python_error(required)
    if (await _git("diff", "--quiet", "HEAD", verified, "--", "requirements.txt", "constraints.txt"))["code"] == 0:
        return None
    new = await _git_content("show", f"{verified}:requirements.txt")
    if new["code"] != 0:
        return None
    with tempfile.TemporaryDirectory() as folder:
        root = pathlib.Path(folder)
        (root / "requirements.txt").write_text(new["output"], encoding="utf-8")
        # References to further files (-r base.txt, -c constraints.txt) - those
        # from the new version too, otherwise pip would not find them in the
        # temporary folder.
        queue = [new["output"]]
        fetched: set[str] = set()
        while queue and len(fetched) < MAX_EMBEDDED:
            for path in re.findall(r"(?m)^\s*(?:-r|-c|--requirement|--constraint)[\s=]+(\S+)",
                                   queue.pop()):
                path = pathlib.PurePosixPath(path).as_posix()
                if (path in fetched or path.startswith(("/", "..")) or "/../" in path
                        or "://" in path):
                    continue
                fetched.add(path)
                embedded = await _git_content("show", f"{verified}:{path}")
                if embedded["code"] != 0:
                    continue
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(embedded["output"], encoding="utf-8")
                queue.append(embedded["output"])
        trial = await _pip("--dry-run", "-r", str(root / "requirements.txt"), limit=PIP_TIMEOUT_SECONDS)
    output = trial["output"]
    if trial["code"] == 0 or "no such option: --dry-run" in output:
        return None
    required = _required_python(output)
    if required:
        return _python_error(required)
    return {"status": "error",
            "message": "The packages of the new version cannot be installed on this server, so "
                       "nothing was downloaded and this version keeps running. What pip says: {output}",
            "parameters": {"output": output.strip()[-400:]}}


def _required_python(output: str) -> str:
    """Which Python the package that could not be installed wants. Empty = I do not know.

    The list "Ignored the following versions ... Requires-Python" is put
    together by pip for all packages at once and without their names. So only
    the requirement of the version that really failed is taken ("No matching
    distribution found for starlette==1.8.0"), and only when it wants a newer
    Python than the one running - otherwise the message would advise
    installing something that is already here.
    """
    highest = ""
    for _, version in re.findall(r"No matching distribution found for ([\w.\-\[\]]+)==(\S+)",
                                 output):
        for requirement in re.findall(r"(?<![\w.])" + re.escape(version)
                                      + r" Requires-Python\s*>=\s*(3\.\d+)", output):
            number = tuple(int(x) for x in requirement.split("."))
            if number > sys.version_info[:2] and (not highest or number > tuple(
                    int(x) for x in highest.split("."))):
                highest = requirement
    return highest


async def _incoming_beyond_translations(verified: str) -> list[str] | None:
    """The files that downloading the verified commit would change and that are
    not translations. None = I do not know."""
    diff = await _git("diff", "--name-only", "HEAD", verified)
    if diff["code"] != 0:
        return None
    return [line.strip() for line in diff["output"].splitlines()
            if line.strip() and not line.strip().startswith(TRANSLATIONS_FOLDER)]


# How long one git command may take (a fetch from a GitHub that does not answer would otherwise hold the
# administrator's request for good) and one pip install (downloading, maybe building the packages).
GIT_TIMEOUT_SECONDS = 120
PIP_TIMEOUT_SECONDS = 1800


async def _git(*args: str) -> dict[str, Any]:
    return await _run("git", "-C", str(ROOT), *args)


def _environment() -> dict[str, str]:
    """The application's environment, with git never asking for a password: nobody is at a terminal to type it."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


async def _finish(process: asyncio.subprocess.Process, limit: float) -> bytes | None:
    """The command's stdout, waited for at most `limit` seconds. None = it took longer and was killed.

    Killed with what it started (git's helper for the network): left running, that one would keep the output
    open and the wait would not end. The commands run in a session of their own for that (start_new_session).
    """
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=limit)
    except TimeoutError:
        try:
            if hasattr(os, "killpg"):
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            pass
        return None
    return output


async def _git_content(*args: str) -> dict[str, Any]:
    """Git of which only stdout is needed - a file's content, not messages.

    `_run` merges stderr into stdout. With `git show` a warning (say
    "unable to access ...") would thus be written into requirements.txt as a
    requirement and the package trial would fail on something unrelated to them.
    """
    process = await asyncio.create_subprocess_exec(
        "git", "-C", str(ROOT), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(ROOT),
        env=_environment(),
        start_new_session=True,
    )
    output = await _finish(process, GIT_TIMEOUT_SECONDS)
    if output is None:
        return {"code": process.returncode or -1, "output": ""}
    return {"code": process.returncode, "output": output.decode("utf-8", "replace")}


async def _pip(*args: str, limit: float | None = None) -> dict[str, Any]:
    """`pip install` with the Python the application runs on - one place for
    the dry run and for the real installation, so they check the same."""
    return await _run(sys.executable, "-m", "pip", "install", "--quiet", *args, limit=limit)


async def _run(*command: str, limit: float | None = None) -> dict[str, Any]:
    """Runs a command and returns the exit code and the output together.

    At most `limit` seconds (GIT_TIMEOUT_SECONDS when not given); a command that takes longer is killed and
    ends as a failure.
    """
    wait = GIT_TIMEOUT_SECONDS if limit is None else limit
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(ROOT),
        env=_environment(),
        start_new_session=True,
    )
    output = await _finish(process, wait)
    if output is None:
        return {"code": process.returncode or -1,
                "output": f"{pathlib.Path(command[0]).name} did not finish within {wait:g} s and was stopped."}
    return {"code": process.returncode,
            "output": output.decode("utf-8", "replace")}
