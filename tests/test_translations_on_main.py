# -*- coding: utf-8 -*-
r"""The update check notices translations that were added on `main`.

Translations from Weblate come by merging, not by releases, so a check by the
version number never catches them. So the application asks GitHub apart: what
changed on `main` since my commit **in the folder with translations**?

Three answers and each means something else:

* only files in the folder with translations -> "new translations", a button to download,
* anything elsewhere (code, documentation) -> nothing; `git pull` would
  download that too, and a release is for that,
* GitHub does not know the commit (404)     -> "I do not know", what was there stays.

GitHub is faked through `httpx.MockTransport`, so the test runs without the
network and the answer can be written exactly.

Run:
    .venv/bin/python tests/test_translations_on_main.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "preklady.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"

import httpx  # noqa: E402

from jellyscope import db, i18n, updates  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()


def github(files: list[str] | None, status: int = 200):
    """A fake GitHub: returns the list of changed files, or 404."""
    def response(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"message": "Not Found"})
        return httpx.Response(200, json={
            "ahead_by": len(files or []),
            "files": [{"filename": s} for s in (files or [])],
        })

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(response),
                                 **kwargs)
    return factory


# The application in the test does not run from git like production, so the commit is faked.
async def _fake_commit():
    return "abc123"


updates._own_commit = _fake_commit
updates.why_no_update = lambda: ""       # as if it could be updated


def count(files, status=200):
    return asyncio.run(updates._new_translations(
        client_factory=github(files, status)))


print("--- what counts ---")
check(count(["jellyscope/translations/es.json",
             "jellyscope/translations/log/es.json"]) == 2,
      "two translation files and nothing else = 2")
check(count(["jellyscope/translations/es.json",
             "jellyscope/translations/log/es.json",
             "README.md"]) == 0,
      "translations + anything outside the folder = 0 (a pull would download the rest too)")
check(count(["jellyscope/web.py", "CHANGELOG.md", "README.md"]) == 0,
      "code and documentation = 0 (a release is for that)")
# The tag pushed, CI failed, the Release was not created: main has code and translations.
# `git pull` would download both, so the translations are not reported - they come with the release.
check(count(["jellyscope/translations/cs.json",
             "jellyscope/translations/en.json",
             "jellyscope/web.py", "CHANGELOG.md"]) == 0,
      "translations next to code = 0 (a release in progress, a pull would download the code too)")
check(count([]) == 0, "nothing changed = 0")
check(count(None, status=404) is None,
      "an unknown commit = None (not zero: \"I do not know\" is not \"nothing\")")
check(count(None, status=500) is None, "a server error = None")

print()
print("--- when updating does not work, it does not ask ---")
updates.why_no_update = lambda: "Tohle je ukázka"
check(count(["jellyscope/translations/es.json"]) is None,
      "in the demo or in the image GitHub is not bothered")
updates.why_no_update = lambda: ""

print()
print("--- the state for the pages ---")
db.set_setting(updates.NEW_TRANSLATIONS_KEY, "3")
db.forget_settings()
state = updates.state()
check(state["new_translations"] == 3, f"state() carries the count ({state['new_translations']})")
check(state["is_newer"] is False, "and without a new release is_newer=False")

db.set_setting(updates.NEW_TRANSLATIONS_KEY, "")
db.forget_settings()
check(updates.state()["new_translations"] == 0, "empty = 0")

print()
print("--- an update without a release downloads only translations ---")
# git is faked: what a pull would bring is said by the fake `diff --name-only`.
CALLS: list[tuple] = []


def fake_git(*args):
    async def run():
        CALLS.append(args)
        if args[0] == "diff" and "--name-only" in args:
            return {"code": 0, "output": chr(10).join(REMOTE)}
        if args[0] == "rev-parse":
            return {"code": 0, "output": "0123456789abcdef0123456789abcdef01234567"}
        return {"code": 0, "output": "Already up to date."}
    return run()


updates._git = fake_git
updates._run = lambda *a, **k: fake_git("pip")
db.set_setting(updates.FOUND_VERSION_KEY, "")          # no new release out
db.forget_settings()

REMOTE = ["jellyscope/translations/es.json", "jellyscope/translations/log/es.json"]
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "ok", f"only translations on the remote -> the pull ran ({result['status']})")
i_fetch = next((i for i, v in enumerate(CALLS) if v[0] == "fetch"), 10 ** 6)
i_diff = next((i for i, v in enumerate(CALLS) if v[0] == "diff" and "--name-only" in v), -1)
check(i_fetch < i_diff, "the fetch comes before finding out what the update would bring")
check(any(v[0] == "merge" for v in CALLS), "and then the verified commit is downloaded (merge)")

REMOTE = ["jellyscope/translations/es.json", "jellyscope/web.py"]
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "error", "translations + code without a release -> nothing is downloaded")
# The message is the English key, translated where it is shown.
check("more than translations" in result.get("message", "")
      and "víc než překlady" in i18n.translate(result.get("message", ""), "cs"), "and the message says why")
check(not any(v[0] in ("pull", "merge") for v in CALLS), "nothing was downloaded")

db.set_setting(updates.FOUND_VERSION_KEY, "99.0.0")     # the release is out
db.forget_settings()
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "ok" and any(v[0] == "merge" for v in CALLS),
      "with a release out everything is downloaded (that is the release)")
db.set_setting(updates.FOUND_VERSION_KEY, "")
db.forget_settings()

print()
print("--- in the template the translation wins only without a release ---")
template = (PROJECT / "jellyscope" / "templates" / "base.html").read_text(
    encoding="utf-8")
i_version = template.find("{% if version_info.is_newer %}")
i_translations = template.find("{% elif version_info.new_translations %}")
check(0 < i_version < i_translations,
      "the footer: first the new release, only then translations")
check("{% if version_info.is_newer or version_info.new_translations %}" in template,
      "the window opens for translations alone too")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
