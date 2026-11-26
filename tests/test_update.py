# -*- coding: utf-8 -*-
r"""Updating from the browser: the indicator, the window with the release notes, the button.

While updating went only through `deploy/update.sh`, the new-version
indicator was a badge linking to GitHub. Now it opens a window with the
release notes and a button that downloads the new version and restarts
the app.

Two things are watched especially:

* **The release notes are foreign text.** They came from GitHub, so they
  go through escaping before HTML is put together from the markdown.
* **Only the administrator may update**, and only where there is
  something to update from - an installation cloned from git.

Run:
    .venv/bin/python tests/test_update.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "aktualizace.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, updates  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword", is_admin=False)

print("--- release notes from markdown ---")
html = updates.notes_html(
    "### Fixed\n\n"
    "- **Plakát** se nedal obnovit, viz `ImageTags`\n"
    "  a pokračování na dalším řádku.\n"
    "- Druhá odrážka\n\n"
    "Odstavec na konec.")
check("<h4>Fixed</h4>" in html, "the heading is put together")
check(html.count("<li>") == 2, f"the bullets too ({html.count('<li>')})")
check("dalším řádku" in html.split("</li>")[0],
      "a bullet's continuation stays in it")
check("<strong>Plakát</strong>" in html and "<code>ImageTags</code>" in html,
      "bold and code are converted")
check("<p>Odstavec na konec.</p>" in html, "and a paragraph is a paragraph")

# CHANGELOG breaks at ~72 characters. Every line of a paragraph used to be its own <p>,
# so in the New version window a paragraph fell apart into pieces with gaps between
# them. The lines of one paragraph belong together - it ends only at an empty line,
# a heading or a bullet.
wrapped = updates.notes_html(
    "Prvni radek odstavce\na jeho pokracovani.\n\nDruhy **odstavec**\nzase dva radky.\n"
    "### Nadpis\nTreti odstavec\n- odrazka\nPo odrazce\n\nCtvrty.")
check("<p>Prvni radek odstavce a jeho pokracovani.</p>" in wrapped,
      "a wrapped paragraph is one <p>")
check("<p>Druhy <strong>odstavec</strong> zase dva radky.</p>" in wrapped,
      "an empty line separates paragraphs, formatting holds across the whole one")
check("<h4>Nadpis</h4><p>Treti odstavec</p><ul><li>odrazka Po odrazce</li></ul><p>Ctvrty.</p>"
      in wrapped, "a heading and a bullet end a paragraph; a line after a bullet continues it")
check(wrapped.count("<p>") == 4, f"there are as many paragraphs as in the text ({wrapped.count('<p>')})")

print()
print("--- a foreign tag does not get into the page ---")
attack = updates.notes_html("- <img src=x onerror=alert(1)> a <b>tučně</b>")
check("<img" not in attack and "&lt;img" in attack, "the tag is written out as text")
check("<b>" not in attack and "&lt;b&gt;" in attack, "the harmless one too")

print()
print("--- markdown of the release notes as on GitHub ---")
md = updates.notes_html
REPO = "https://github.com/SpeeDFireCZE/jellyscope"
check(md("## ✨ Features") == "<h4>✨ Features</h4>" and md("### 🐛 Fixes") == "<h4>🐛 Fixes</h4>"
      and md("#### Detail") == "<h5>Detail</h5>", "headings: up to ### big (the CHANGELOG's sections), #### smaller")
check(md("- a\n  - b\n    - c") == "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>",
      "nested bullets by the indent (" + md("- a\n  - b\n    - c") + ")")
check(md("1. a\n2. b") == "<ol><li>a</li><li>b</li></ol>", "a numbered list (" + md("1. a\n2. b") + ")")
check("<em>kurzíva</em>" in md("*kurzíva*") and "<em>kurzíva</em>" in md("_kurzíva_")
      and "<del>x</del>" in md("~~x~~"), "italics and strikethrough")
check(md("snake_case_name") == "<p>snake_case_name</p>" and md("2 * 3 * 4") == "<p>2 * 3 * 4</p>",
      "underscores inside a word and lone stars stay")
check(md("> citace") == "<blockquote><p>citace</p></blockquote>" and md("---") == "<hr>", "a quote and a rule")
check(md("```python\na **b** #1\n```") == "<pre><code>a **b** #1</code></pre>", "a code block is not converted inside")
LINK = ' target="_blank" rel="noopener noreferrer"'
check(md("[text](https://x.cz/a_b#c)") == f'<p><a href="https://x.cz/a_b#c"{LINK}>text</a></p>', "a link")
COMPARE = REPO + "/compare/v1.9.0...v1.9.1"
check(md(COMPARE) == f'<p><a href="{COMPARE}"{LINK}>{COMPARE}</a></p>', "a bare address is a link")
credited = md("- Add a card (#23 by @login)")
check(f'<a href="{REPO}/issues/23"{LINK}>#23</a>' in credited
      and f'<a href="https://github.com/login"{LINK}>@login</a>' in credited, f"#23 and @login as on GitHub ({credited})")
plain = [md(t) for t in ("C#", "a#b", "jan@example.com", "`#23`", "[x](https://x.cz/#23)")]
check(plain[:3] == ["<p>C#</p>", "<p>a#b</p>", "<p>jan@example.com</p>"] and plain[3] == "<p><code>#23</code></p>"
      and plain[4] == f'<p><a href="https://x.cz/#23"{LINK}>x</a></p>', f"what only looks like a reference stays ({plain})")
image = md("![logo](https://x.cz/l.png)")
check("<img" not in image and f'<a href="https://x.cz/l.png"{LINK}>logo</a>' in image, "an image is a link")
check(md("### Support C#") == "<h4>Support C#</h4>" and md("## Title ##") == "<h4>Title</h4>",
      f"a # at the end of a heading stays, only a closing ## after a space goes ({md('### Support C#')})")
check(md("<https://x.cz/a>") == f'<p><a href="https://x.cz/a"{LINK}>https://x.cz/a</a></p>',
      f"an address in <> is a link without the brackets ({md('<https://x.cz/a>')})")
check(md('see "https://x.cz" here') == f'<p>see &quot;<a href="https://x.cz"{LINK}>https://x.cz</a>&quot; here</p>',
      f"a quote ends a bare address ({md(chr(34).join(['see ', 'https://x.cz', ' here']))})")
try:
    deep = md("> " * 2000 + "x")
    check(deep.count("<blockquote>") <= 3 and "x" in deep, f"quotes nest at most three deep ({deep.count('<blockquote>')})")
except RecursionError:
    check(False, "a quote nested 2000 deep does not throw")
import time  # noqa: E402
slowest = 0.0
for line in ("*a " * 6600, "**a " * 5000, "_a " * 6600, "~~a " * 5000, "__a " * 5000, "# a" + " " * 19990 + "b",
             "- a" + " " * 19990 + "b", "`" + " a`" * 6600, "- " * 10000 + "x"):
    started = time.perf_counter()
    md(line)
    slowest = max(slowest, time.perf_counter() - started)
check(slowest < 0.5, f"a 20 000-character line of unclosed marks renders quickly ({slowest:.2f} s)")
KITCHENOWL = (
    "This release brings many fixes and a few new cards.\n\n"
    "**Before you upgrade:** back up the database.\n\n"
    "### ✨ Features\n\n"
    "- Add the Lidarr cards (#31 by @someone)\n"
    "- Show the release notes of every skipped release\n\n"
    "### 🐛 Fixes\n\n"
    "- Fix `pool_pre_ping` on PostgreSQL (#33)\n\n"
    "### 🌍 Translations\n\n"
    "- Added German (#23 by @translator)\n\n"
    "### New Contributors\n\n"
    "- @translator made their first contribution in #23\n\n"
    f"**Full Changelog**: {COMPARE}\n")
whole = md(KITCHENOWL)
check("[" not in whole and "](" not in whole and whole.count("<h4>") == 4 and whole.count("<li>") == 5
      and "<strong>Full Changelog</strong>" in whole and f'href="{COMPARE}"' in whole and "<code>pool_pre_ping</code>" in whole,
      "release notes in the KitchenOwl style come out whole")
fenced = md("```deploy/update.sh``` now checks Python\n\n### Fixes\n\n- Fix a")
check(fenced == "<p><code>deploy/update.sh</code> now checks Python</p><h4>Fixes</h4><ul><li>Fix a</li></ul>",
      f"triple backticks inside a line are code, not a block that swallows the rest ({fenced})")
check(md("``a`b``") == "<p><code>a`b</code></p>" or md("``x``") == "<p><code>x</code></p>",
      f"a code span of two backticks ({md('``x``')})")
coded = md("[`deploy/update.sh`](https://x.cz/a)")
check(coded == f'<p><a href="https://x.cz/a"{LINK}><code>deploy/update.sh</code></a></p>',
      f"a link whose text is code ({coded})")
nested_image = md("[![a](https://x.cz/i.png)](https://y.cz)")
check(nested_image.count("<a ") == 1, f"an image inside a link's text is no second link ({nested_image})")
bold_address = md("**https://x.cz/releases**")
check(bold_address == f'<p><strong><a href="https://x.cz/releases"{LINK}>https://x.cz/releases</a></strong></p>',
      f"marks after a bare address are no part of it ({bold_address})")
# Code right after an address was taken into it: its put-aside mark came back as <code> inside href.
import re  # noqa: E402

glued = [md(t) for t in ("https://x.cz/`path`", "<https://x.cz/`c`>", 'https://x.cz/`x" onmouseover="y`')]
check(glued[0] == f'<p><a href="https://x.cz/"{LINK}>https://x.cz/</a><code>path</code></p>'
      and not any("<" in address for out in glued for address in re.findall(r'href="([^"]*)"', out)),
      f"the address ends where code starts, the code follows the link ({glued[0]})")

print()
print("--- the state for the template ---")
db.set_setting(updates.FOUND_VERSION_KEY, "99.0.0")
db.set_setting(updates.FOUND_NOTES_KEY, "### Nové\n\n- Něco")
state = updates.state()
check(state["is_newer"] is True, "a new version is recognised")
check([r["version"] for r in state["releases"]] == ["99.0.0"] and "<h4>Nové</h4>" in state["releases"][0]["html"],
      "the notes go to the template as HTML (markdown stored by an older version = one release)")
check(state["can_update"] is True,
      "the dev folder is a git repository, so updating is possible")

print()
print("--- who sees what ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    page = client.get("/").text
    check('data-modal="modal-update"' in page,
          "the indicator opens a window, not a link to GitHub")
    check('id="modal-update"' in page, "the window is on the page")
    check('action="/settings/update"' in page, "and in it the Update button")
    check("Něco" in page, "and the release notes")

with TestClient(app) as client:
    client.post("/login", data={"username": "reader", "password": "longpassword"},
                follow_redirects=False)
    page = client.get("/").text
    check('id="modal-update"' in page, "a reader sees the window")
    check('action="/settings/update"' not in page,
          "but not the Update button")
    response = client.post("/settings/update", follow_redirects=False)
    check(response.status_code in (302, 303, 401, 403),
          f"and the route does not let them through ({response.status_code})")
    check(db.get_setting(updates.FOUND_VERSION_KEY) == "99.0.0",
          "nothing was started meanwhile")

print()
print("--- every skipped release ---")
# Whoever skipped a few releases is to see what all of them bring, not only the newest one: one query for the
# list of releases, those newer than the running version, newest first. Drafts, pre-releases and tags without
# a number are no releases.
import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

import httpx  # noqa: E402

import jellyscope  # noqa: E402

TAG_URL = "https://github.com/SpeeDFireCZE/jellyscope/releases/tag/"


def release(tag: str, body: str = "", day: str = "2026-11-01", **flags) -> dict:
    return {"tag_name": tag, "html_url": TAG_URL + tag, "body": body, "published_at": f"{day}T10:00:00Z",
            "draft": False, "prerelease": False, **flags}


LISTED = [release("v1.9.3", "### ✨ Features\n\n- Add a card (#40 by @someone)", "2026-11-25"),
          release("v1.9.4", "- not yet", draft=True), release("v2.0.0-rc1", "- soon", prerelease=True),
          release("v1.9.1", "- c", "2026-11-18"), release("latest", "- a moving tag"),
          release("v1.9.2", "### 🐛 Fixes\n\n- Fix b", "2026-11-20"), release("v1.9.0", "- d", "2026-11-16"),
          release("v1.8.4", "- e", "2026-11-07")]
found, more = updates.skipped_releases(LISTED, "1.9.0")
check([r["version"] for r in found] == ["1.9.3", "1.9.2", "1.9.1"] and more == "",
      f"the releases newer than the running one, by version, newest first ({[r['version'] for r in found]}, {more})")
check(found and found[0] == {"version": "1.9.3", "url": TAG_URL + "v1.9.3", "published_at": "2026-11-25T10:00:00Z",
                             "body": "### ✨ Features\n\n- Add a card (#40 by @someone)"}, f"each with its page and notes ({found[:1]})")
many, more = updates.skipped_releases([release(f"v1.9.{n}", "x") for n in range(30, 5, -1)], "1.9.0")
check(len(many) == updates.MAX_RELEASES == 20 and more == "1.9.10",
      f"at most 20 releases, and the newest left out is kept ({len(many)}, {more})")
half = "y" * (updates.MAX_NOTES // 2 + 10)
cut, more = updates.skipped_releases([release("v1.9.3", half), release("v1.9.2", half), release("v1.9.1", "z")], "1.9.0")
check([r["version"] for r in cut] == ["1.9.3"] and more == "1.9.2",
      f"the notes together fit MAX_NOTES: a release that does not fit ends the list ({[r['version'] for r in cut]})")
huge, more = updates.skipped_releases([release("v1.9.3", "w" * (updates.MAX_NOTES + 5)), release("v1.9.2", "v")], "1.9.0")
check([r["version"] for r in huge] == ["1.9.3"] and len(huge[0]["body"]) == updates.MAX_NOTES and more == "1.9.2",
      "the newest is always there, its notes cut as before")
check(updates.skipped_releases({"message": "Not Found"}, "1.9.0") == ([], "")
      and updates.skipped_releases([None, 5, "x", {"tag_name": None}], "1.9.0") == ([], ""),
      "an answer that is no list of releases gives none, without an error")
foreign = updates.skipped_releases([release("v1.9.3") | {"html_url": "javascript:alert(1)"}], "1.9.0")[0]
check(foreign and foreign[0]["url"].startswith("https://github.com/"), "a release page is GitHub's or none")

asked: list[str] = []


def github(listed, status: int = 200):
    """A fake GitHub: the list of releases, any other address 404."""
    def respond(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path.endswith("/releases"):
            return httpx.Response(status, json=listed)
        return httpx.Response(404, json={"message": "Not Found"})

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(respond), **kwargs)
    return factory


real_client, real_version = httpx.AsyncClient, jellyscope.__version__
saved = {key: db.get_setting(key, "") for key in (updates.FOUND_VERSION_KEY, updates.FOUND_URL_KEY, updates.FOUND_NOTES_KEY)}
try:
    jellyscope.__version__ = "1.9.0"
    httpx.AsyncClient = github(LISTED)
    result = asyncio.run(updates.check(forced=True))
    check(updates.RELEASES_API in asked and updates.RELEASES_API.endswith("/releases?per_page=30"),
          f"one query for the list of releases ({asked[:1]})")
    check(db.get_setting(updates.FOUND_VERSION_KEY) == "1.9.3" and db.get_setting(updates.FOUND_URL_KEY) == TAG_URL + "v1.9.3",
          f"the newest release is the found version, its page the link ({result.get('found_one')})")
    state = updates.state()
    check([r["version"] for r in state["releases"]] == ["1.9.3", "1.9.2", "1.9.1"] and state["more_releases"] is False
          and "<h4>✨ Features</h4>" in state["releases"][0]["html"] and state["releases"][0]["date"] == "2026-11-25"
          and state["releases"][1]["url"] == TAG_URL + "v1.9.2" and "notes" not in state,
          f"the template gets the releases with HTML and a date ({[(r['version'], r['date']) for r in state['releases']]})")
    jellyscope.__version__ = "1.9.2"
    check([r["version"] for r in updates.state()["releases"]] == ["1.9.3"],
          "after an update only what is still newer is shown, before the next check too")
    jellyscope.__version__ = "1.9.0"

    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "longpassword"}, follow_redirects=False)
        page = client.get("/").text
    check(re.findall(r'class="notes-release">v([\d.]+)', page) == ["1.9.3", "1.9.2", "1.9.1"]
          and all(f'href="{TAG_URL}v1.9.{n}"' in page for n in (1, 2, 3)) and "25.11.2026" in page,
          f"the window lists them under each other with their pages and dates "
          f"({re.findall(r'class=.notes-release.>([^<]*)', page)})")
    check("More releases are on the release page." not in page and "Další vydání najdeš na stránce vydání." not in page,
          "nothing left out, nothing said")
    # The line above the releases says which version runs. It used the key of the "now playing" card ("Elapsed"
    # - time watched), right only in Czech ("Běží"): English read "Elapsed v1.9.0", German "Laufzeit".
    language = db.get_setting("ui_language", "cs")
    db.set_setting("ui_language", "en")
    db.forget_settings()
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "longpassword"}, follow_redirects=False)
        english = client.get("/").text
    db.set_setting("ui_language", language)
    db.forget_settings()
    running = re.search(r'id="modal-update".*?<p class="card-note">\s*(.*?)\s*&middot;', english, re.S)
    check(running is not None and running.group(1).split() == ["Running", "v1.9.0"],
          f"the window says which version runs ({running.group(1).split() if running else None})")
    httpx.AsyncClient = github([release(f"v1.9.{n}", "x") for n in range(30, 5, -1)])
    asyncio.run(updates.check(forced=True))
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "longpassword"}, follow_redirects=False)
        page = client.get("/").text
    check(updates.state()["more_releases"] is True
          and ("More releases are on the release page." in page or "Další vydání najdeš na stránce vydání." in page),
          "with releases left out the window says the others are on the release page")
    check('href="https://github.com/SpeeDFireCZE/jellyscope/releases"' in page, "and links the list of releases")
    jellyscope.__version__ = "1.9.12"     # updated past the releases left out (1.9.10 and older)
    later = updates.state()
    check(later["more_releases"] is False and [r["version"] for r in later["releases"]][-1] == "1.9.13",
          f"after an update past them the sentence goes ({later['more_releases']})")
    jellyscope.__version__ = "1.9.5"
    check(updates.state()["more_releases"] is True, "while some are still newer it stays")
    jellyscope.__version__ = "1.9.0"

    httpx.AsyncClient = github(LISTED)
    asyncio.run(updates.check(forced=True))
    for listed, status, label in (([], 200, "an empty list"), ({"message": "rate limit"}, 403, "a refusal")):
        httpx.AsyncClient = github(listed, status)
        result = asyncio.run(updates.check(forced=True))
        check(result.get("status") == "error" and db.get_setting(updates.FOUND_VERSION_KEY) == "1.9.3"
              and len(updates.state()["releases"]) == 3,
              f"{label}: an error, and what was known stays ({result.get('status')}: {str(result.get('message'))[:60]})")
    stored = json.loads(db.get_setting(updates.FOUND_NOTES_KEY))
    check(isinstance(stored, dict) and [r["version"] for r in stored["releases"]] == ["1.9.3", "1.9.2", "1.9.1"],
          "the releases are stored as JSON")
    db.set_setting(updates.FOUND_NOTES_KEY, "### Fixed\n- x")
    check(updates.stored_releases() == ([{"version": "1.9.3", "url": TAG_URL + "v1.9.3", "published_at": "",
                                          "body": "### Fixed\n- x"}], ""),
          "markdown stored by an older version is one release, until the next check")
    db.set_setting(updates.FOUND_NOTES_KEY, "[Full changelog](https://x.cz)")
    check([r["body"] for r in updates.stored_releases()[0]] == ["[Full changelog](https://x.cz)"],
          "markdown of an older version that starts with a link is markdown, not JSON")
    for broken in ("{broken", '{"releases": ' + "[" * 20000, "[" * 20000):
        db.set_setting(updates.FOUND_NOTES_KEY, broken)
        try:
            releases = updates.state()["releases"]
        except RecursionError:
            releases = None
        check(releases is not None and len(releases) <= 1, f"a broken stored value is no error ({broken[:16]!r}...)")
finally:
    httpx.AsyncClient, jellyscope.__version__ = real_client, real_version
    for key, value in saved.items():
        db.set_setting(key, value)

print()
print("--- the styles of the notes ---")
# Every tag the renderer makes has its rule in the window, the colours only through the variables (the light and
# the dark look follow by themselves) and no :has() - old TV browsers (Chromium 53) drop the whole rule.
css = re.sub(r"/\*.*?\*/", "", (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8"), flags=re.S)
notes_rules = [(selector.strip(), body) for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
               if ".notes" in selector]
selectors = {part.strip() for selector, _ in notes_rules for part in selector.split(",")}
needed = [".notes a", ".notes em", ".notes del", ".notes ol", ".notes ul ul", ".notes blockquote", ".notes pre",
          ".notes pre code", ".notes hr", ".notes h5", ".notes h4.notes-release"]
check(not [n for n in needed if n not in selectors],
      f"every tag of the notes has a rule (missing: {[n for n in needed if n not in selectors]})")
check(not [sel for sel, body in notes_rules if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", body)],
      "colours only through the variables")
check(not [sel for sel, _ in notes_rules if ":has(" in sel], "no :has()")

print()
print("--- where updating is not possible, and why ---")
# "It does not work" without a reason sends a person looking for the fault on their side. Each
# of these cases has a different right answer, so it is said straight away.
from jellyscope import config  # noqa: E402

os.environ["JELLYSCOPE_DOCKER"] = "1"
config.load_config(reload=True)
check(updates.can_update() is False, "in a container there is no update from the browser")
reason = updates.why_no_update()
check("docker compose" in reason, f"and it says what to use instead ({reason})")

import asyncio  # noqa: E402

result = asyncio.run(updates.update())
check(result["status"] == "error" and "docker compose" in result["message"],
      "an attempt to update ends with a message, not an error")
check(updates.state()["no_update_reason"] == reason,
      "and the template gets that reason")
os.environ.pop("JELLYSCOPE_DOCKER")
config.load_config(reload=True)
check(updates.why_no_update() == "", "outside a container nothing stands in the way")

# The systemd service of deploy/ may write only into data/ (ProtectSystem=strict, ReadWritePaths). The button used
# to be offered there and every click ended in "Could not find out what the update would fetch. Try again in a
# moment." - git could not write .git/FETCH_HEAD. An update writes the code (git) and the packages (pip): where the
# app may not write them, the button is not offered and deploy/update.sh is named instead.
import stat as _stat  # noqa: E402
import types as _types  # noqa: E402

_real_root, _real_sysconfig = updates.ROOT, updates.sysconfig if hasattr(updates, "sysconfig") else None
_folder = Path(tempfile.mkdtemp())
(_folder / ".git").mkdir()
_packages = _folder / "site-packages"
_packages.mkdir()
if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
    print("SKIPPED  as root (or on Windows) the rights of a folder do not stop writing - the check is not shown")
else:
    updates.ROOT = _folder
    updates.sysconfig = _types.SimpleNamespace(get_path=lambda name: str(_packages))
    try:
        check(updates.why_no_update() == "", "a git folder the app may write: the button is offered")
        for _locked, _label in ((_folder / ".git", "git's folder"), (_folder, "the code"),
                                (_packages, "the packages")):
            _locked.chmod(_stat.S_IRUSR | _stat.S_IXUSR)
            try:
                _reason, _can = updates.why_no_update(), updates.can_update()
                _result = asyncio.run(updates.update())
            finally:
                _locked.chmod(_stat.S_IRWXU)
            check(_can is False and "deploy/update.sh" in _reason and "data/" in _reason
                  and _result["status"] == "error" and _result["message"] == _reason,
                  f"{_label} may not be written: no button, the reason names deploy/update.sh ({_reason!r})")
    finally:
        updates.ROOT = _real_root
        if _real_sysconfig is None:
            del updates.sysconfig
        else:
            updates.sysconfig = _real_sysconfig
check(updates.why_no_update() == "", "and back in the real folder the button is there again")

print()
print("--- the waiting page after an update ---")
# The templates are read from the file on every request, the app's code lives in
# the process's memory. Between `git pull` and the restart OLD CODE therefore runs over
# NEW TEMPLATES - and a new template that wants a variable the old code does not know
# about falls over with Internal Server Error. Exactly that is what the
# redirect to /?wait=restart did.
#
# The answer to an update is therefore a page put together in Python: no
# template, no context, nothing that could drift apart from the version.
from jellyscope.web import settings as web_settings  # noqa: E402

waiting_page = web_settings._update_page("testovaci-nonce")
check("<!doctype html>" in waiting_page.lower(), "it is a whole page")
check("{{" not in waiting_page and "{%" not in waiting_page,
      "no template - nothing the old code could not render")
check("/health" in waiting_page and "started_at" in waiting_page,
      "it waits for the new process by /health")
check("location.href" in waiting_page, "and then lets the person on")

source = "\n".join(p.read_text(encoding="utf-8")
                   for p in sorted((PROJECT / "jellyscope" / "web").rglob("*.py")))
route = source[source.index("async def settings_update("):]
route = route[:route.index("def _update_page")]
check("async def settings_updates" not in route and route.count("\n") < 80,
      f"the cut is the update route, not a piece of another file ({route.count(chr(10))} lines)")
check("wait=restart" not in route,
      "the route no longer redirects to a page of the app")
check("_schedule_restart()" in route, "but it still schedules the restart")

print()
print("--- no updating in the demo mode ---")
# The demo runs from a git folder, but updating somebody else's installation would be
# a surprise - the demo touches nothing.
os.environ["JELLYSCOPE_DEMO"] = "1"
from jellyscope import config  # noqa: E402

config.load_config(reload=True)
check(updates.can_update() is False, "the demo cannot be updated")
os.environ.pop("JELLYSCOPE_DEMO")
config.load_config(reload=True)
check(updates.can_update() is True, "and outside the demo it can again")

print()
print("--- watching for a version is a task, not a setting in General ---")
from jellyscope import tasks  # noqa: E402

check("updates" in tasks.TASKS, "the task „Kontrola aktualizací“ is in the schedule")
task = tasks.TASKS["updates"]
check(task.is_daily and task.time_setting == "task_updates_time",
      "it is planned for a time like the other daily tasks")
check(updates.ENABLED_KEY == task.enabled_setting,
      "it is switched on by the same key as the task - two places would be a trap")

# Whoever had the watching on under the original key is to have it on after the
# update too. Otherwise it would quietly stop working and they would have no way
# of telling why.
with db.connect() as conn:
    conn.execute("INSERT INTO settings (key, value)"
                 " VALUES ('update_check_enabled', '1')"
                 " ON CONFLICT (key) DO UPDATE SET value = '1'")
    conn.commit()
db.forget_settings()
db.init_db()                      # what happens when the new version starts
db.forget_settings()
check(tasks.is_enabled(task), "the watching that was on carries over to the task")
check(db.get_setting("update_check_enabled", "") == "",
      "and the old key is tidied away, so it does not confuse in the database")

# The carry-over must cope with a row as psycopg returns it too - a dict.
# SQLite returns `sqlite3.Row`, which can be read by position as well, so
# `row[0]` looks right during development and with a person on PostgreSQL brings
# the whole start down with "KeyError: 0". Exactly that happened.
import sqlite3 as _sqlite3  # noqa: E402


def _carries_over(row: object) -> bool:
    """Does the migration decide the same for both shapes of a row?"""
    return row is not None and str(dict(row).get("value")) == "1"


class _PsycopgRow(dict):
    """What psycopg returns: a dict, where `row[0]` is a KeyError."""


check(_carries_over(_PsycopgRow(value="1")), "a dict row carries over")
check(not _carries_over(_PsycopgRow(value="0")), "and one switched off stays off")

_connection = _sqlite3.connect(":memory:")
_connection.row_factory = _sqlite3.Row
_row = _connection.execute("SELECT '1' AS value").fetchone()
check(_carries_over(_row), "and a row from SQLite too")
_connection.close()

print()
print("--- before downloading, the new version's packages are checked ---")
# 1.8.1 needs Python 3.11. The button in 1.8.0 did `git pull` and then fell over
# on pip with a message that did not show the cause. Now the new version's packages
# are first tried as a dry run (pip --dry-run), and when that fails, nothing is
# downloaded and the message says why.
CALLS: list[tuple] = []
PIP_TRIAL = {"code": 0, "output": ""}
GIT = {"requirements_stejne": False}
NEW_REQUIREMENTS = "starlette==9.0.0\n"


VERIFIED = "abc123def4567890abc123def4567890abc12345"
RUNNING = "0123456789abcdef0123456789abcdef01234567"
INSTALL = {"code": 0, "output": ""}
MINIMUM = {"python": "3.0"}
BASE_REQUIREMENTS = "starlette==9.0.0\n"


def fake_git(*arguments):
    async def run():
        CALLS.append(("git",) + arguments)
        if arguments[:2] == ("diff", "--quiet") and "requirements.txt" in arguments:
            changed = set() if GIT["requirements_stejne"] else {"requirements.txt"}
            changed |= GIT.get("also_changed", set())
            return {"code": 1 if changed & set(arguments[arguments.index("--") + 1:]) else 0, "output": ""}
        if arguments == ("rev-parse", "HEAD"):
            return {"code": 0, "output": RUNNING + "\n"}
        if arguments[0] == "rev-parse":
            return {"code": 0, "output": VERIFIED + "\n"}
        if arguments[0] == "reset" and GIT.get("reset_fails"):
            return {"code": 128, "output": "error: Entry 'run.py' not uptodate. Cannot merge."}
        return {"code": 0, "output": "Already up to date."}
    return run()


def fake_content(*arguments):
    async def run():
        CALLS.append(("git-obsah",) + arguments)
        if arguments[-1].endswith("deploy/python-minimum"):
            return {"code": 0, "output": MINIMUM["python"] + "\n"}
        if arguments[-1].endswith(":base.txt"):
            return {"code": 0, "output": BASE_REQUIREMENTS}
        return {"code": 0, "output": NEW_REQUIREMENTS}
    return run()


DRIVER = {"code": 0, "output": ""}


def fake_run(*command, **options):
    async def run():
        CALLS.append(command)
        if "--dry-run" in command:
            return dict(PIP_TRIAL)
        if str(command[-1]).endswith("requirements-postgres.txt"):
            return dict(DRIVER)
        return dict(INSTALL)
    return run()


original = (updates._git, updates._git_content, updates._run)
updates._git, updates._git_content, updates._run = fake_git, fake_content, fake_run
db.set_setting(updates.FOUND_VERSION_KEY, "99.0.0")
db.forget_settings()


def downloaded() -> bool:
    return any(v[:2] == ("git", "merge") for v in CALLS)


def installed() -> bool:
    return any("pip" in v and "install" in v and "--dry-run" not in v for v in CALLS)


def tried() -> bool:
    return any("--dry-run" in v for v in CALLS)


# A Python that does not exist (3.99): the number can then get to the page in no
# other way than filled in from pip's output.
NEW_PYTHON = ("ERROR: Ignored the following versions that require a different python "
              "version: 8.0.0 Requires-Python >=3.8; 9.0.0 Requires-Python >=3.99\n"
              "ERROR: Could not find a version that satisfies the requirement "
              "starlette==9.0.0 (from versions: 0.1.0, 1.7.0)\n"
              "ERROR: No matching distribution found for starlette==9.0.0")
PIP_TRIAL.update(code=1, output=NEW_PYTHON)
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "error" and not downloaded() and not installed(),
      f"an old Python: nothing is downloaded or installed ({result['status']})")
check(result.get("parameters", {}).get("required") == "3.99"
      and result.get("parameters", {}).get("current") == "%d.%d" % sys.version_info[:2]
      and "Python" in result.get("message", ""),
      f"the message says which Python is needed and which one runs ({result.get('parameters')})")
check(any(v[:2] == ("git", "fetch") for v in CALLS)
      and any(v[:2] == ("git-obsah", "show") and "requirements.txt" in v[-1] for v in CALLS),
      "the packages are taken from the new version's requirements.txt (fetch + show)")
trial = next((v for v in CALLS if "--dry-run" in v), ())
check(bool(trial) and trial[0] == sys.executable,
      "the trial runs with the Python the app runs on")

# pip puts the list "Ignored ..." together for all packages and without names. The
# Python version is taken only for the version that really failed, and only when it is
# newer than the one that runs - otherwise the message would advise nonsense.
for output, label in (
        ("ERROR: Ignored the following versions that require a different python version: "
         "2.0 Requires-Python >=3.99\nERROR: No matching distribution found for starlette==9.0.0",
         "Requires-Python belongs to another version"),
        ("ERROR: Ignored the following versions that require a different python version: "
         "9.0.0 Requires-Python >=3.8\nERROR: No matching distribution found for starlette==9.0.0",
         "the Python asked for is not newer than the one that runs")):
    PIP_TRIAL.update(code=1, output=output)
    CALLS.clear()
    result = asyncio.run(updates.update())
    check(result["status"] == "error" and not downloaded()
          and "required" not in result.get("parameters", {})
          and "output" in result.get("parameters", {}),
          f"{label}: a general message with pip's output, not advice about Python")

PIP_TRIAL.update(code=1, output="ERROR: No matching distribution found for nesmysl==9")
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "error" and not downloaded()
      and "nesmysl==9" in result.get("parameters", {}).get("output", ""),
      "another problem with the packages: nothing is downloaded and the message shows what pip said")

PIP_TRIAL.update(code=2, output="no such option: --dry-run")
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "ok" and downloaded() and installed(),
      "an old pip without --dry-run: the trial is skipped, the update runs as before")

# An unknown option in the NEW requirements.txt is not an old pip - that is not downloaded.
PIP_TRIAL.update(code=2, output="ERROR: Invalid requirement\nno such option: --budouci-volba")
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "error" and not downloaded(),
      "an unknown option in the new requirements.txt: not downloaded")

PIP_TRIAL.update(code=0, output="Would install starlette-9.0.0")
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "ok" and downloaded() and installed(),
      "the packages can be installed: downloaded and installed")
i_trial = next((i for i, v in enumerate(CALLS) if "--dry-run" in v), 10 ** 6)
i_pull = next((i for i, v in enumerate(CALLS) if v[:2] == ("git", "merge")), -1)
check(i_trial < i_pull, "the trial is BEFORE the download")
# Between the trial and the download main on GitHub could have moved on. So exactly
# the verified commit is downloaded (merge --ff-only <sha>), not what lies remotely at
# the moment, and there is one fetch only.
check(any(v[:2] == ("git", "merge") and "--ff-only" in v and v[-1] == VERIFIED for v in CALLS)
      and not any(v[:2] == ("git", "pull") for v in CALLS)
      and sum(1 for v in CALLS if v[:2] == ("git", "fetch")) == 1,
      "exactly the verified commit is downloaded, no second fetch or pull")
check(any(v[:2] == ("git-obsah", "show") and v[-1] == f"{VERIFIED}:requirements.txt" for v in CALLS),
      "requirements.txt is taken from the verified commit")
real = next((v for v in CALLS if "pip" in v and "--dry-run" not in v), ())
check(bool(trial) and [x for x in trial if x not in ("--dry-run",) and not str(x).endswith("requirements.txt")]
      == [x for x in real if not str(x).endswith("requirements.txt")],
      "the trial and the real installation call pip the same way (only --dry-run on top)")

# The new version's Python minimum is in one place (deploy/python-minimum):
# the message is clear without deciphering pip's output, and pip is not even tried.
MINIMUM["python"] = "3.99"
PIP_TRIAL.update(code=0, output="Would install starlette-9.0.0")
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "error" and not downloaded() and not tried()
      and result.get("parameters", {}).get("required") == "3.99",
      f"the new version wants a newer Python (deploy/python-minimum): nothing is downloaded ({result.get('parameters')})")
MINIMUM["python"] = "3.0"

# A requirements.txt referring to another file (-r): that one is taken from the new
# version too, otherwise pip would not find it in the temporary folder.
NEW_REQUIREMENTS = "-r base.txt\nuvicorn==9.0.0\n"
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "ok"
      and any(v[:2] == ("git-obsah", "show") and v[-1] == f"{VERIFIED}:base.txt" for v in CALLS),
      "the file from -r is taken from the verified commit")
NEW_REQUIREMENTS = "starlette==9.0.0\n"

# With requirements.txt unchanged (an update of the translations only) nothing is tried.
GIT["requirements_stejne"] = True
PIP_TRIAL.update(code=1, output=NEW_PYTHON)
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "ok" and not tried() and downloaded(),
      "requirements.txt unchanged: no trial, straight to the download")
# The versions of what the packages pull in live in constraints.txt (requirements.txt refers to it). A new version
# that changes only them is tried as well: a pin can need a newer Python just as a package of requirements.txt can.
GIT["also_changed"] = {"constraints.txt"}
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "error" and tried() and not downloaded()
      and result.get("parameters", {}).get("required") == "3.99",
      f"only constraints.txt changed: tried, and not downloaded when it cannot install ({result['status']})")
GIT["also_changed"] = set()
GIT["requirements_stejne"] = False

# The message reaches the page too, with the version numbers.
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

PIP_TRIAL.update(code=1, output=NEW_PYTHON)
admin_client = TestClient(app)
admin_client.post("/login", data={"username": "admin", "password": "longpassword"})
page = admin_client.post("/settings/update").text
check("3.99" in page and "python3.99-venv" in page
      and "%d.%d" % sys.version_info[:2] in page,
      "the page has the message with the needed and the running version and the command for it")

print()
print("--- the packages fail to install after the download ---")
# The dry run passed, then the network dropped or a mirror failed. Left in place, the new code would start at
# the next restart (for any reason) with the old packages and could fail at import: it goes back to the
# version that runs, and the message says so.
PIP_TRIAL.update(code=0, output="Would install starlette-9.0.0")
INSTALL.update(code=1, output="ERROR: Could not install packages due to an OSError: Connection reset by peer")
CALLS.clear()
result = asyncio.run(updates.update())
i_merge = next((i for i, v in enumerate(CALLS) if v[:2] == ("git", "merge")), 10 ** 6)
i_back = next((i for i, v in enumerate(CALLS) if v[:3] == ("git", "reset", "--keep") and v[-1] == RUNNING), -1)
check(result["status"] == "error" and i_back > i_merge,
      f"the code goes back to the commit that runs (git reset --keep {RUNNING[:7]}) after the download")
check("put back" in result.get("message", "")
      and "Connection reset" in result.get("parameters", {}).get("output", ""),
      f"and the message says so, with what pip said ({result.get('message', '')[:80]})")
GIT["reset_fails"] = True
CALLS.clear()
result = asyncio.run(updates.update())
check(result["status"] == "error" and result.get("parameters", {}).get("commit") == RUNNING
      and "git reset --keep {commit}" in result.get("message", ""),
      f"when that fails too, the message gives the command to do it by hand ({result.get('message', '')[:80]})")
GIT["reset_fails"] = False
INSTALL.update(code=0, output="")
# What `git reset --keep` really does to the folder - the recorded call above says nothing about it. A repository of
# its own: the commit that runs, the downloaded one on top (a changed file and a new one), a file of the
# installation git does not track (.env); then _put_back with the real git.
import shutil  # noqa: E402
import subprocess  # noqa: E402

if not shutil.which("git"):
    check(True, "(no git on this machine; the real way back is not tried)")
else:
    repository = Path(tempfile.mkdtemp())

    def in_repository(*arguments: str) -> str:
        return subprocess.run(["git", "-C", str(repository), *arguments], capture_output=True, text=True,
                              check=True).stdout.strip()

    in_repository("init", "-q")
    in_repository("config", "user.email", "test@example.invalid")
    in_repository("config", "user.name", "test")
    (repository / "run.py").write_text("old\n", encoding="utf-8")
    in_repository("add", "run.py")
    in_repository("commit", "-qm", "the version that runs")
    running_commit = in_repository("rev-parse", "HEAD")
    (repository / "run.py").write_text("new\n", encoding="utf-8")
    (repository / "added.py").write_text("new\n", encoding="utf-8")
    in_repository("add", "run.py", "added.py")
    in_repository("commit", "-qm", "the downloaded version")
    (repository / ".env").write_text("SECRET_KEY=mine\n", encoding="utf-8")
    real_root = updates.ROOT
    updates._git, updates._run, updates.ROOT = original[0], original[2], repository
    try:
        put_back = asyncio.run(updates._put_back(running_commit, "ERROR: Connection reset by peer"))
    finally:
        updates._git, updates._run, updates.ROOT = fake_git, fake_run, real_root
    check(put_back["status"] == "error" and "put back" in put_back["message"]
          and in_repository("rev-parse", "HEAD") == running_commit
          and (repository / "run.py").read_text(encoding="utf-8") == "old\n" and not (repository / "added.py").exists()
          and (repository / ".env").read_text(encoding="utf-8") == "SECRET_KEY=mine\n",
          f"with the real git the folder is the running version again, the installation's own file stays ({put_back['message'][:60]})")

print()
print("--- the PostgreSQL driver at its pinned version ---")
# The driver is not in requirements.txt (a platform without its packages must not fail every update), it is pinned
# in requirements-postgres.txt. The button does what update.sh does: where the driver is installed, it is brought
# to that version after the packages; where it is not, nothing; a failure is only a warning in the log.
import logging  # noqa: E402

PIP_TRIAL.update(code=0, output="Would install starlette-9.0.0")
real_available = updates.db.psycopg_available
warnings: list[str] = []


class _Catch(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            warnings.append(record.msg)


_catch = _Catch()
updates.log.addHandler(_catch)
try:
    for present, fails, label in ((True, False, "where it is installed, it is brought to the pinned version"),
                                  (False, False, "where it is not, the update does not install it"),
                                  (True, True, "a failure is a warning in the log, the update is done")):
        updates.db.psycopg_available = lambda present=present: present
        DRIVER.update(code=1 if fails else 0, output="ERROR: no wheel" if fails else "")
        warnings.clear()
        CALLS.clear()
        result = asyncio.run(updates.update())
        pip_calls = [v for v in CALLS if "pip" in v and "--dry-run" not in v]
        driver = [i for i, v in enumerate(pip_calls)
                  if v[-2:] == ("-r", str(PROJECT / "requirements-postgres.txt"))]
        packages = [i for i, v in enumerate(pip_calls) if v[-1] == str(PROJECT / "requirements.txt")]
        ok = result["status"] == "ok" and bool(packages)
        if not present:
            ok = ok and not driver
        else:
            ok = ok and bool(driver) and driver[0] > packages[0]
            ok = ok and (any("PostgreSQL driver" in w for w in warnings) if fails else not warnings)
        check(ok, f"{label} ({result['status']}, pip: {[v[-1] for v in pip_calls]}, log: {warnings})")
finally:
    updates.db.psycopg_available = real_available
    updates.log.removeHandler(_catch)
DRIVER.update(code=0, output="")

updates._git, updates._git_content, updates._run = original

print()
print("--- git and pip have a time limit ---")
# A `git fetch` against a GitHub that does not answer (or waiting for a password nobody types) used to hold the
# administrator's request for good, and a second click started a second git next to it.
import time as _time  # noqa: E402

PID_FILE = Path(_tmp) / "slow-command.pid"
limit_before = getattr(updates, "GIT_TIMEOUT_SECONDS", 120)
updates.GIT_TIMEOUT_SECONDS = 1
started = _time.monotonic()
slow = asyncio.run(updates._run(sys.executable, "-c",
                                f"import os, time; open({str(PID_FILE)!r}, 'w').write(str(os.getpid())); time.sleep(30)"))
took = _time.monotonic() - started
slow_pid = int(PID_FILE.read_text()) if PID_FILE.is_file() else 0
alive = bool(slow_pid) and Path(f"/proc/{slow_pid}").exists()
check(slow["code"] != 0 and took < 10, f"a command past the limit ends as a failure in time ({slow['code']}, {took:.1f} s)")
check(not alive or sys.platform == "win32", f"and the command is stopped (process {slow_pid} running: {alive})")
if alive:
    os.kill(slow_pid, 9)
started = _time.monotonic()
content = asyncio.run(updates._git_content("-c", "alias.slow=!sleep 3", "slow"))
check(content["code"] != 0 and _time.monotonic() - started < 2.5,
      f"the same for git read for its content ({content['code']}, {_time.monotonic() - started:.1f} s)")
updates.GIT_TIMEOUT_SECONDS = limit_before
prompt = asyncio.run(updates._git_content("-c", "alias.envcheck=!printenv GIT_TERMINAL_PROMPT", "envcheck"))
prompt_run = asyncio.run(updates._run(sys.executable, "-c", "import os; print(os.environ.get('GIT_TERMINAL_PROMPT'))"))
check(prompt["output"].strip() == "0" and prompt_run["output"].strip() == "0",
      f"git never asks for a password nobody can type ({prompt['output'].strip()!r}, {prompt_run['output'].strip()!r})")

db.set_setting(updates.FOUND_VERSION_KEY, "")
db.forget_settings()

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
