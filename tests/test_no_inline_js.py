r"""No inline JavaScript and no handlers in attributes (on…=, javascript:).

The CSP header has `script-src 'self'`: the browser runs only scripts from the
app's files. Should a mistake in escaping show up anywhere in the output of data,
an inserted `<script>` or `onerror=` does not run - but only when the app does not
use them itself. Otherwise CSP would have to allow inline code and the safeguard
would be gone. A new script belongs in static/js, values from a template in the
data block `texts-js` or in `data-…` attributes.

It is watched twice: in the sources of the templates (the branches not rendered
in the test too - the demo, the holidays) and in the rendered pages and fragments
as the administrator, a reader, a viewer and signed out (the HTML Python puts together too).

Spec: docs/superpowers/specs/2026-11-05-csp-skripty-design.md

Run:
    .venv/bin/python tests/test_no_inline_js.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a stand-in in every file of the web/settings subpackage

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bez_js.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import access, accounts, cards, db  # noqa: E402
from jellyscope.jellyfin import JellyfinClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


# A handler in an attribute: on…= inside a tag, across several lines too.
HANDLER = re.compile(r"<[a-zA-Z][^<>]*?\son[a-z]+\s*=", re.S)
# A javascript: address in an attribute's value.
JS_ADDRESS = re.compile(r"=\s*[\"']?\s*javascript:", re.I)
# A runnable inline script: <script> without src. A data block
# (type="application/json") does not run, that one may.
INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>", re.I)


def handlers(html: str) -> list[str]:
    """Handlers in attributes, javascript: addresses and inline scripts - snippets for the report."""
    found = [html[max(0, m.end() - 80):m.end() + 40] for m in HANDLER.finditer(html)]
    found += [html[m.start():m.end() + 60] for m in INLINE_SCRIPT.finditer(html)]
    found += [html[max(0, m.start() - 40):m.end() + 20] for m in JS_ADDRESS.finditer(html)]
    return found


print("--- checking itself ---")
check(len(handlers('<a onclick="x()">')) == 1, "finds onclick= in a tag")
check(len(handlers('<button\n  class="b"\n  onclick="a;\n b">')) == 1, "finds a handler across several lines")
check(len(handlers('<a href="javascript:void(0)">')) == 1, "finds a javascript: address")
check(handlers('<a data-close-modal href="/x">') == [], "a data-… attribute is not a handler")
check(handlers("if (a < b) { el.onclick = f; }") == [], "code with a comparison and an assignment is not a tag")
check(len(handlers("<script>alert(1)</script>")) == 1, "finds an inline script")
check(len(handlers('<SCRIPT type="module">x()</SCRIPT>')) == 1, "with another type and in capitals too")
check(handlers('<script type="application/json" id="x">{}</script>') == [], "a JSON data block may")
check(handlers('<script src="/static/js/app.js?v=1" defer></script>') == [], "a script from a file may")


print()
print("--- (a) the sources of the templates ---")
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
forbidden_sources = []
for file in sorted((PROJECT / "jellyscope" / "templates").rglob("*.html")):
    text = JINJA_COMMENT.sub("", file.read_text(encoding="utf-8"))
    name = file.relative_to(PROJECT / "jellyscope" / "templates").as_posix()
    forbidden_sources += [f"{name}: {n!r}" for n in handlers(text)]
for line in forbidden_sources:
    print("       " + line)
check(not forbidden_sources, f"templates without handlers in attributes and inline scripts ({len(forbidden_sources)} found)")


print()
print("--- (b) the rendered pages ---")
NOW = datetime.now(UTC).replace(tzinfo=None)


def time_at(shift: timedelta) -> str:
    return (NOW + shift).strftime(db.TIME_FORMAT)


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
accounts.create("viewer", "longpassword")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-jana' WHERE username = 'viewer'")
    conn.execute("INSERT INTO users (id, name, is_administrator, is_disabled) VALUES"
                 " ('u-jana', 'Jana', 0, 0), ('u-petr', 'Petr', 0, 0)")
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies'),"
                 " ('serialy', 'Seriály', 'tvshows')")
    conn.execute("INSERT INTO items (id, name, type, library_id, production_year, path, is_missing, date_created,"
                 " image_tag, video_codec, height, width) VALUES ('f1', 'Film', 'Movie', 'filmy', 2024,"
                 " '/media/filmy/Film.mkv', 0, ?, 'tag', 'hevc', 2160, 3840)", (time_at(-timedelta(days=1)),))
    conn.execute("INSERT INTO items (id, name, type, library_id, series_id, series_name, parent_index_number,"
                 " index_number, path, is_missing, date_created) VALUES ('e1', 'Pilot', 'Episode', 'serialy',"
                 " 's1', 'Seriál', 1, 1, '/media/serialy/S/S01E01.mkv', 0, ?)", (time_at(-timedelta(days=2)),))
    for key, uid, name, item, kind in (("h1", "u-jana", "Jana", "f1", "Movie"),
                                       ("h2", "u-petr", "Petr", "e1", "Episode")):
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
            " client, device_name, remote_address, play_method, audio_language, bitrate, started_at,"
            " last_seen_at, ended_at, watched_seconds, paused_seconds, is_active) VALUES (?, ?, ?, ?, 'X', ?,"
            " 'filmy', 'Jellyfin Web', 'Obyvak', '203.0.113.7', 'Transcode', 'cze', 8000000, ?, ?, ?, 3600, 0, 0)",
            (key, uid, name, item, kind, time_at(-timedelta(hours=5)), time_at(-timedelta(hours=4)),
             time_at(-timedelta(hours=4))))
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, library_id,"
        " client, device_name, play_method, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " position_ticks, is_active, media_runtime_ticks) VALUES ('zive', 'u-jana', 'Jana', 'f1', 'Film',"
        " 'Movie', 'filmy', 'Jellyfin Web', 'Obyvak', 'DirectPlay', ?, ?, 600, 0, 6000000000, 1, 72000000000)",
        (time_at(-timedelta(minutes=10)), time_at(-timedelta(seconds=5))))
    conn.commit()
db.set_setting(cards.ENABLED_KEY, "1")
db.set_setting(access.LOGIN_KEY, "1")


# A quick sign-in opens by a POST from /login and asks Jellyfin - which is not
# in the test, the answers are faked.
async def _yes(self) -> bool:
    return True


async def _start(self):
    return {"code": "123456", "secret": "t"}


JellyfinClient.quick_connect_enabled = _yes
JellyfinClient.quick_connect_start = _start


def sign_in(name: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": name, "password": "longpassword", "method": "local"})
    return k


# Downloads and actions that are not a page. Otherwise all links of the menu are taken.
NOT = ("/logout", "/static/", "/image/", "/settings/backup/", "/settings/log/download", "/api/")
LINK = re.compile(r'href="(/[^"#]*)"')


def page_key(address: str) -> str:
    """Variants of one page with only another period or filter are one page."""
    parts = urlsplit(address.replace("&amp;", "&"))
    kept = {k: v[0] for k, v in parse_qs(parts.query).items() if k in ("section", "tab")}
    return parts.path + ("?" + "&".join(f"{k}={v}" for k, v in sorted(kept.items())) if kept else "")


def walk(k: TestClient, starts: list[str], depth: int) -> dict[str, str]:
    """The pages reachable by links from `starts` down to the given depth: {address: HTML}."""
    seen: dict[str, str] = {}
    wave = [page_key(z) for z in starts]
    for _ in range(depth + 1):
        following = []
        for address in wave:
            if address in seen or address.startswith(NOT):
                continue
            r = k.get(address, follow_redirects=False)
            seen[address] = r.text if r.status_code == 200 and "html" in r.headers.get("content-type", "") else ""
            following += [page_key(o) for o in LINK.findall(seen[address])]
        wave = following
    return {a: h for a, h in seen.items() if h}


FRAGMENTS = ["/partials/now-playing", "/partials/recently-added", "/video/partials/daily", "/video/partials/top-items",
             "/partials/network-live",
             # Every card of the registry drawn again at once, as an open page asks for its cards.
             "/partials/cards?keys=" + ",".join(card.key for card in cards.ALL_CARDS)]
admin = sign_in("admin")
PAGES = sorted(walk(admin, ["/", "/settings"], 2)) + FRAGMENTS
check(len(PAGES) >= 40, f"enough pages were walked through ({len(PAGES)})")


def check_pages(label: str, k: TestClient, addresses: list[str], *, post: bool = False) -> None:
    for address in addresses:
        r = k.post(address, follow_redirects=False) if post else k.get(address, follow_redirects=False)
        if "html" not in r.headers.get("content-type", ""):
            continue
        forbidden = handlers(r.text)
        check(not forbidden, f"{label} {address} ({r.status_code}): without a handler and an inline script {forbidden[:2]}")
        # Old JavaScript from the cache after an update: every file with a version.
        without_version = [s for s in re.findall(r'src="(/static/js/[^"]+)"', r.text) if "?v=" not in s]
        check(not without_version, f"{label} {address}: scripts with a version in the address {without_version}")


check_pages("administrator", admin, PAGES + ["/neexistuje"])
check_pages("reader", sign_in("reader"), PAGES + ["/settings"])
check_pages("viewer", sign_in("viewer"), PAGES + ["/users/u-jana"])
guest = TestClient(app)
check_pages("signed out", guest, ["/login", "/neexistuje"])
check_pages("signed out", guest, ["/login/quick"], post=True)

print()
print("--- (d) the theme in the head, before rendering ---")
for address, k in (("/", admin), ("/login", guest)):
    html = k.get(address).text
    head = html.split("</head>", 1)[0]
    tag = re.search(r'<script\b[^>]*src="/static/js/base\.js[^"]*"[^>]*>', head)
    check(bool(tag) and "defer" not in tag.group(0) and "async" not in tag.group(0),
          f"{address}: base.js in <head> without defer and async ({tag and tag.group(0)})")

print()
print("--- (g) the CSP header ---")
# Without 'unsafe-inline' for scripts: the browser runs only the app's files.
# Styles stay (a decision of 5. 11. 2026 - outside the rebuild's scope).
for address, k in (("/", admin), ("/login", guest)):
    csp = k.get(address).headers.get("Content-Security-Policy", "")
    rules = {c.split()[0]: c.split()[1:] for c in (x.strip() for x in csp.split(";")) if c}
    check(rules.get("script-src") == ["'self'"], f"{address}: script-src only 'self' ({rules.get('script-src')})")
    check(rules.get("style-src") == ["'self'", "'unsafe-inline'"],
          f"{address}: style-src unchanged ({rules.get('style-src')})")

print()
print("--- (h) pages Python puts together ---")
# The waiting page after an update (web.settings._update_page) is put together
# without a template and cannot avoid an inline script: the old version renders it
# over the new files, so it must not touch anything that changes with the version - not
# even static/js. Its script therefore carries a one-time nonce from the header.
from jellyscope import updates  # noqa: E402
from jellyscope.web import settings as web_settings  # noqa: E402


async def _update_ok():
    return {"status": "ok"}


_original = (updates.update, web_settings._schedule_restart)
updates.update = _update_ok
substitute(web_settings, "_schedule_restart", lambda: None)
try:
    r = admin.post("/settings/update", follow_redirects=False)
finally:
    updates.update = _original[0]
    substitute(web_settings, "_schedule_restart", _original[1])
csp = r.headers.get("Content-Security-Policy", "")
nonce = re.search(r"script-src 'self' 'nonce-([A-Za-z0-9_-]{16,})'", csp)
scripts = re.findall(r"<script\b[^>]*>", r.text, re.I)
check(r.status_code == 200 and bool(nonce), f"the waiting page: the header has a nonce ({csp[:120]})")
check(bool(scripts) and nonce is not None and all(f'nonce="{nonce.group(1)}"' in s for s in scripts),
      f"the waiting page: every script of it carries the nonce from the header ({scripts})")
check(not [n for n in handlers(r.text) if "nonce=" not in n], "the waiting page: otherwise without inline code")
check(admin.get("/").headers.get("Content-Security-Policy", "").count("nonce-") == 0,
      "the other pages have no nonce (they stay without inline code)")

print()
print("--- (f) texts for the scripts ---")
import json  # noqa: E402

from jellyscope import i18n  # noqa: E402

TEXTS = re.compile(r'<script type="application/json" id="texts-js">(.*?)</script>', re.S)


def texts(text: str) -> dict:
    """The block's content as app.js reads it (JSON.parse textContent).

    Without html.unescape: the browser does not decode entities inside <script>, so
    a block escaped as HTML would bring JSON.parse down - and with it the whole app.js.
    """
    m = TEXTS.search(text)
    try:
        return json.loads(m.group(1)) if m else {}
    except ValueError:
        return {}


def in_language(language: str, load):
    """The page in the server's language `language` (accounts without a choice of their own take it over)."""
    db.set_setting("ui_language", language)
    try:
        return texts(load())
    finally:
        db.set_setting("ui_language", "cs")


for label, load, keys in (
        ("/", lambda: admin.get("/").text, {"demo", "done", "takingLong"}),
        ("/login/quick", lambda: TestClient(app).post("/login/quick").text, {"codeExpired"})):
    cs, en = in_language("cs", load), in_language("en", load)
    check(set(cs) == keys and all(cs.values()), f"{label}: texts-js is valid JSON with the keys {sorted(keys)} ({sorted(cs)})")
    check(set(en) == keys and en != cs, f"{label}: other texts in English ({en})")

# A translation that would end the block: tojson escapes "<", so </script>
# stays text inside the block and no runnable script appears after it.
# Planted in the Czech translation: English is the source and has no table of its own.
ATTACK = 'hotovo</script><script>alert(1)</script>'
original = i18n.TRANSLATIONS["cs"].get("done")
i18n.TRANSLATIONS["cs"]["done"] = ATTACK
try:
    db.set_setting("ui_language", "cs")
    page = admin.get("/").text
finally:
    db.set_setting("ui_language", "cs")
    if original is None:
        i18n.TRANSLATIONS["cs"].pop("done", None)
    else:
        i18n.TRANSLATIONS["cs"]["done"] = original
check(texts(page).get("done") == ATTACK, "a translation with </script> stays whole inside the block")
check(not handlers(page), f"and no inline script appears after the block ({handlers(page)[:1]})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
