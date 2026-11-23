# -*- coding: utf-8 -*-
"""Security checks that must not get lost.

Each of them answers a hole that really was in the app:

1. **The sessions' signing key.** When `SECRET_KEY` was not filled in, a fixed
   fallback value written in the source was used. Whoever knows it signs
   a cookie with somebody else's account and is inside as the administrator — without a password.

2. **The image proxy.** The id from the address was sent to Jellyfin as it
   came. It is put together into the path `/Items/<id>/Images/<kind>`, so
   a question mark or a slash in it changes what our server asks Jellyfin for —
   and it asks with our API key, that is with the administrator's rights.

3. **Guessing passwords.** Signing in could be tried for ever. Hashing is
   slow on purpose, but a dictionary attack overnight would work — and every
   attempt costs a quarter of a second of the processor, so it can flood the server too.

4. **/health without signing in** listed the size of the library and how much
   was playing right now.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "bezpecnost.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
# As if a proxy stood in front of the app. Exactly in this setup the
# X-Forwarded-For header used to be trusted regardless of who sent it.
os.environ["FORWARDED_ALLOW_IPS"] = "127.0.0.1"

from jellyscope import accounts, config, db  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "readerpass", is_admin=False)


print("--- the sessions' signing key ---")
# Without SECRET_KEY a random one must be made and stored, not a fixed
# value taken from the source.
home = Path(tempfile.mkdtemp())
original_base = config.BASE_DIR
config.BASE_DIR = home
try:
    key = config._generated_secret_key()
    check(len(key) >= 32, f"the key made is long enough ({len(key)})")
    check(key != "nezabezpeceny-vychozi-klic", "it is not the fixed value from the source")
    check((home / "data" / "secret_key").is_file(), "it was stored on disk")
    check(config._generated_secret_key() == key,
          "after a restart it is the same - signing in does not expire")

    second_home = Path(tempfile.mkdtemp())
    config.BASE_DIR = second_home
    check(config._generated_secret_key() != key,
          "another installation gets another key")
finally:
    config.BASE_DIR = original_base

source = (PROJECT / "jellyscope" / "config.py").read_text(encoding="utf-8")
check('"nezabezpeceny-vychozi-klic"' not in source.split("def _generated_secret_key")[0],
      "no fixed key is left in the code")


print()
print("--- the image proxy must not reach for anything but an image ---")
calls: list[str] = []


class FakeClient:
    """Writes down which address it would ask Jellyfin for."""

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def image_bytes(self, item_id: str, kind: str = "Primary",
                          max_width: int = 400) -> None:
        import httpx
        calls.append(str(httpx.URL("http://jellyfin:8096").join(
            f"/Items/{item_id}/Images/{kind}")))
        return None


import jellyscope.web as web  # noqa: E402
from jellyscope import images  # noqa: E402

# The images for the /image route (web/library.py) are fetched by the image store
# (jellyscope/images.py) - the stand-in must be where the client is read.
images.JellyfinClient = FakeClient  # type: ignore[assignment,misc]

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(web.app)
client.post("/login", data={"username": "reader", "password": "readerpass"})

# A clean request must pass.
calls.clear()
client.get("/image/abc123def")
check(calls == ["http://jellyfin:8096/Items/abc123def/Images/Primary"],
      f"an ordinary id asks for the image ({calls})")

# And these must not get to Jellyfin at all.
for written, label in [
    ("x%3Fapi_key%3Dcizi", "a question mark (slipped-in query parameters)"),
    ("..%2F..%2FSessions%3F", "a path out of /Items"),
    ("x%2F..%2FUsers", "a slash in the middle"),
    ("x%23kotva", "a hash"),
]:
    calls.clear()
    response = client.get(f"/image/{written}")
    check(response.status_code == 404 and not calls,
          f"{label}: refused ({response.status_code}), nothing to Jellyfin ({calls})")

# The image's kind and the width are taken from a fixed list, not from the address.
calls.clear()
client.get("/image/abc123def?kind=../../Users&w=99999")
check(calls == ["http://jellyfin:8096/Items/abc123def/Images/Primary"],
      f"an unknown kind falls back to Primary ({calls})")


print()
print("--- guessing passwords has a brake ---")
braked = TestClient(web.app)
states = []
for attempt in range(accounts.ATTEMPTS_BEFORE_BLOCK + 3):
    states.append(braked.post(
        "/login", data={"username": "admin", "password": f"wrong{attempt}"},
        follow_redirects=False).status_code)

check(states[0] == 401, f"the first attempt is an ordinary refusal ({states[0]})")
check(429 in states, f"after a few attempts the brake comes ({states})")
check(states.count(429) >= 3,
      f"and holds, not letting every other one through ({states.count(429)}x)")

# While the block holds, the right password does not get around it - otherwise it would be
# enough to keep guessing and try the last attempt with the right password.
sign_in = braked.post("/login",
                      data={"username": "admin", "password": "longpassword"},
                      follow_redirects=False)
check(sign_in.status_code == 429,
      f"the right password waits too, while the block lasts ({sign_in.status_code})")

# After unblocking it signs in normally - and leaves no record behind,
# so the next typo starts from the first level again.
for address in [r["ip"] for r in accounts.list_blocks()]:
    accounts.unblock(address)
sign_in = braked.post("/login",
                      data={"username": "admin", "password": "longpassword"},
                      follow_redirects=False)
check(sign_in.status_code == 303,
      f"after unblocking the right password passes ({sign_in.status_code})")
check(not accounts.list_blocks(),
      "a successful sign-in deletes the block's level too")

# A forged header must not get around the brake.
#
# Behind a proxy the real address is filled in from X-Forwarded-For - but that
# header can be sent by an attacker too. While the app trusted it on its own,
# it was enough to write a different number into it every time and each password attempt
# counted separately: the brake formally existed and never kicked in. Now the address is taken
# from request.client, where uvicorn rewrites it from the header - and only when
# somebody from the list of trusted proxies sent it.
states = []
for attempt in range(accounts.ATTEMPTS_BEFORE_BLOCK + 3):
    states.append(braked.post(
        "/login", data={"username": "admin", "password": f"wrong{attempt}"},
        headers={"X-Forwarded-For": f"203.0.113.{attempt}"},
        follow_redirects=False).status_code)
check(429 in states, f"a different address in the header every time does not get around the brake ({states})")
check(len(accounts.list_blocks()) == 1,
      f"and it makes one block, not {accounts.ATTEMPTS_BEFORE_BLOCK} different "
      f"({[r['ip'] for r in accounts.list_blocks()]})")
for address in [r["ip"] for r in accounts.list_blocks()]:
    accounts.unblock(address)


# Blocks escalate: each next one in a row lasts longer, the fourth is permanent already.
accounts.unblock("10.0.0.9")
levels = []
for _ in range(len(accounts.BLOCK_STEPS) + 1):
    for _ in range(accounts.ATTEMPTS_BEFORE_BLOCK - 1):
        ignored = accounts.count_failure("10.0.0.9")
    block = accounts.count_failure("10.0.0.9")
    levels.append("permanent" if block["permanent"] else block["seconds"])
    # The unlocking time cannot be hurried, so we just cancel the running pause - the level
    # stays, because the `level` column decides it.
    with db.connect() as conn:
        conn.execute("UPDATE login_blocks SET blocked_until = ? WHERE ip = ?",
                     ("2000-01-01 00:00:00", "10.0.0.9"))

check(levels == [60, 120, 300, 900, "permanent"],
      f"each next block is longer, the fifth permanent ({levels})")
check(accounts.block_remaining("10.0.0.9") == -1, "a permanent block does not dissolve")

listing = {r["ip"]: r for r in accounts.list_blocks()}
check("10.0.0.9" in listing, "the block is seen in the list for Settings")
check(listing["10.0.0.9"]["permanent"] is True, "and it is marked as permanent")

check(accounts.unblock("10.0.0.9"), "the administrator can cancel it")
check(accounts.block_remaining("10.0.0.9") == 0, "after unblocking one can try at once")
check(not accounts.unblock("10.0.0.9"), "the second time there is nothing to cancel")

# A block survives a restart of the app - otherwise it would be enough to wait for an update.
accounts._block("10.0.0.10")
accounts._attempts.clear()
check(accounts.block_remaining("10.0.0.10") > 0,
      "the block is in the database, not only in the process's memory")
accounts.unblock("10.0.0.10")


print()
print("--- controlling blocks is for the administrator only ---")
response = client.post("/settings/blocks/unblock", data={"ip": "1.2.3.4"},
                       follow_redirects=False)
check(response.status_code == 403, f"a reader may not unblock ({response.status_code})")
# All of Settings is for the administrator, so a reader does not get to the section with
# the blocks already at the door - does not even see the list of addresses.
check(client.get("/settings?section=blocks").status_code == 403,
      "and does not even get the section")


print()
print("--- /health without signing in gives away no content ---")
anonymous = TestClient(web.app)
public = anonymous.get("/health").json()
check(set(public) == {"status", "started_at"},
      f"a signed-out visitor sees only the state and the start time ({sorted(public)})")
check(public["status"] == "ok", "monitoring can tell the app is alive")

signed_in = client.get("/health").json()
check("library_version" in signed_in and "active_sessions" in signed_in,
      f"a signed-in one sees everything ({sorted(signed_in)})")


print()
print("--- the app's description for machines (OpenAPI) is not out ---")
# Otherwise FastAPI would hand anybody a list of all addresses with the descriptions of the
# routes at /openapi.json - a map of the app including the administrator's actions. Jellyscope has
# its own documentation of the read-only API (Settings -> API), nobody needs the machine description.
for address in ("/openapi.json", "/docs", "/redoc"):
    response = anonymous.get(address, follow_redirects=False)
    check(response.status_code == 404 and '"openapi"' not in response.text and "swagger" not in response.text.lower(),
          f"{address}: a signed-out visitor gets 404, not the app's description ({response.status_code}, {len(response.content)} B)")
    response = client.get(address, follow_redirects=False)
    check(response.status_code in (403, 404) and '"openapi"' not in response.text,
          f"{address}: a signed-in one does not get it either ({response.status_code})")
check(web.app.openapi_url is None and web.app.docs_url is None and web.app.redoc_url is None,
      f"the app hands out neither the machine description nor its viewers ({web.app.openapi_url}, {web.app.docs_url}, {web.app.redoc_url})")

print()
print("--- without signing in only the listed addresses ---")
# A page is protected by the guard at the route (Depends), not by the middleware: the middleware
# lets a signed-out visitor on, because the sign-in page must be seen. A route without a guard - or an
# address a library adds on its own, like /openapi.json - is therefore open to anybody. Here is their list.
from fastapi import FastAPI  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

from jellyscope.legacy_urls import LEGACY_URLS  # noqa: E402

sys.path.insert(0, str(PROJECT / "tests"))
from _package import flat_routes  # noqa: E402

GUARDS = {"require_login", "require_admin", "verify_token", "_admin_only"}
PUBLIC = ({"/setup", "/login", "/login/quick", "/login/quick/status", "/logout", "/health"}
          # Old addresses only redirect to the new ones (those have their guard already).
          | {old for old, _new, _methods in LEGACY_URLS})


def without_guard(application: Any) -> set[str]:
    """Addresses without a sign-in guard: routes without one and everything that is not a route of the app (except /static)."""
    found: set[str] = set()
    for route in flat_routes(application.router.routes):
        if not isinstance(route, APIRoute):
            if getattr(route, "path", "") != "/static":
                found.add(getattr(route, "path", "?"))
            continue
        dependencies, queue = set(), [route.dependant]
        while queue:
            dependency = queue.pop()
            queue += dependency.dependencies
            if dependency.call is not None:
                dependencies.add(getattr(dependency.call, "__name__", ""))
        if not dependencies & GUARDS:
            found.add(route.path)
    return found


open_addresses = without_guard(web.app)
check(open_addresses == PUBLIC,
      f"without a guard are only signing in, the first start, /health and the redirects of old addresses "
      f"(extra {sorted(open_addresses - PUBLIC)}, missing {sorted(PUBLIC - open_addresses)})")
# A trap: an app created the way it used to be (without openapi_url=None) must be recognised.
check("/openapi.json" in without_guard(FastAPI(docs_url=None, redoc_url=None)),
      "and the check recognises an address a library adds on its own (/openapi.json)")

print()
print("--- what must not get into a page ---")
# Jellyfin's API key is a password to somebody else's server. It does not belong in HTML, not even by mistake.
db.set_setting("jellyfin_api_key", "TAJNY-KLIC-1234567890")
admin_client = TestClient(web.app)
admin_client.post("/login", data={"username": "admin", "password": "longpassword"})
for path in ["/settings?section=jellyfin", "/settings?section=data", "/"]:
    content = admin_client.get(path).text
    check("TAJNY-KLIC-1234567890" not in content, f"{path}: the key is not there")

# A title's name goes into a chart as text, not as HTML.
from jellyscope import charts  # noqa: E402

attack = '<img src=x onerror=alert(1)>'
chart = charts.hbar_chart([{"label": attack, "value": 5}], "label", "value")
check(attack not in chart and "&lt;img" in chart,
      "a name with an HTML tag is written out as text in a chart")

# The same for the tooltip with the values. It comes into the page as JSON in an attribute
# and the browser puts nodes together from it through textContent - so not even a movie's name
# with <img onerror=...> has a way of becoming a tag.
tooltip = charts.area_chart_multi(
    [{"d": "2026-08-11", "v": 1}], "d", [{"key": "v", "label": attack, "slot": 1}])
check(attack not in tooltip and "&lt;img" in tooltip,
      "a name with an HTML tag is escaped in the tooltip too")

# Since the rebuild for CSP the scripts are without inline code, in static/js/app.js.
base = ((PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
        + (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8"))
handler = base[base.index("function fillTooltip"):]
handler = handler[:handler.index("document.addEventListener")]
check("innerHTML" not in handler,
      "the tooltip is put together from nodes, not by inserting HTML")
check("JSON.parse" in handler and "textContent" in handler,
      "the values are read as data and written as text")


print()
print("--- the response headers ---")
headers = admin_client.get("/").headers
for name, value in [("X-Content-Type-Options", "nosniff"),
                    ("X-Frame-Options", "SAMEORIGIN"),
                    ("Referrer-Policy", "same-origin")]:
    check(headers.get(name) == value,
          f"{name}: {headers.get(name)!r}")

# The app needs none of these, so not even a piece of a page that got in here by
# mistake can ask for them.
rules = headers.get("Permissions-Policy", "")
check(all(f"{what}=()" in rules
          for what in ("camera", "microphone", "geolocation")),
      f"Permissions-Policy forbids the camera, the microphone and the location ({rules!r})")

# HSTS only over HTTPS: the browser remembers it for a long time, so switched on on a
# server without a certificate it would lock people out.
check("Strict-Transport-Security" not in headers,
      "HSTS is not sent without HTTPS")

print()
print("--- where a page may load from and where it may send ---")
# Jellyscope has not a single foreign source: no CDN, no font from the internet,
# we lead the images through our own server. The rule can therefore read "only from
# here" - and above all: should a foreign script get into a page through a mistake,
# this takes away what matters to it. The data has nowhere to be sent (connect-src),
# a form has nowhere to redirect (form-action) and further code has nowhere to be
# downloaded from (script-src without a foreign source).
csp = headers.get("Content-Security-Policy", "")
for rule in ("default-src 'self'", "img-src 'self'", "connect-src 'self'",
             "form-action 'self'", "frame-ancestors 'self'",
             "base-uri 'self'", "object-src 'none'"):
    check(rule in csp, rule)
# 'unsafe-eval' is needed nowhere - and should it appear, CSP would stop
# being a defence against inline code.
check("unsafe-eval" not in csp, "eval is allowed nowhere")
check(headers.get("Content-Security-Policy") ==
      admin_client.get("/settings").headers.get("Content-Security-Policy"),
      "the same header on every page")

print()
print("--- the ceiling on an uploaded file holds while reading ---")
# The ceiling is here because of memory. When a file is read whole first and only
# then measured, it is a defence that comes after the damage: a gigabyte file
# brings the server down before it can say it is too big.
import asyncio  # noqa: E402

from jellyscope.web import settings as web_settings  # noqa: E402


class EndlessFile:
    """Pretends to be an uploaded file that never ends."""

    def __init__(self) -> None:
        self.read_so_far = 0

    async def read(self, size: int = -1) -> bytes:
        # Without a ceiling this would run until memory ran out.
        how_much = size if size > 0 else 4 * 1024 * 1024
        self.read_so_far += how_much
        return b"x" * how_much


class FakeRequest:
    """Only as much of a Request as the message needs."""

    def __init__(self) -> None:
        self.session: dict = {}


file = EndlessFile()
result = asyncio.run(web_settings._load_backup(FakeRequest(), file))
ceiling = web_settings.MAX_UPLOAD_MB * 1024 * 1024
check(result is None, "a file too big is refused")
check(file.read_so_far <= ceiling + 2 * 1024 * 1024,
      f"and reading stopped right past the ceiling ({file.read_so_far // 1024 // 1024} MB "
      f"with a ceiling of {web_settings.MAX_UPLOAD_MB} MB)")


print()
print("--- a translation by a stranger must not run anything ---")
# Since translating through Weblate is possible (see TRANSLATING.md), the interface's
# texts are written by strangers. Charts go into the page through `| safe`,
# so a translated sentence in them is HTML - and a tag in a translation would be
# runnable code. Exactly this happened in the heatmap: the day's abbreviation
# went in without escaping.
from jellyscope import charts, i18n, updates  # noqa: E402

ATTACK = "</div><script>alert(1)</script>"
original = {}
# Planted in the Czech translation: English is the source and has no table of its own.
for key in [charts.DAY_NAMES[0], "No data yet", "Drag across the chart to pick a range."]:
    original[key] = i18n.TRANSLATIONS["cs"].get(key)
    i18n.TRANSLATIONS["cs"][key] = ATTACK

original_language = db.get_setting("ui_language", "cs")
db.set_setting("ui_language", "cs")
db.forget_settings()
try:
    rendered = {
        "heatmap": charts.heatmap([[1] * 24 for _ in range(7)]),
        "an empty chart": charts.hbar_chart([], "label", "value"),
        "an area chart": charts.area_chart_multi(
            [{"day": "2026-09-01", "gb": 1}, {"day": "2026-09-02", "gb": 2}],
            "den", [{"key": "gb", "label": "GB"}], selectable=True),
    }
    for name, html in rendered.items():
        check("<script>" not in html, f"{name}: a tag from a translation does not run")
        check("&lt;script&gt;" in html or ATTACK not in html,
              f"{name}: the text is shown as text")
finally:
    for key, value in original.items():
        if value is None:
            i18n.TRANSLATIONS["cs"].pop(key, None)
        else:
            i18n.TRANSLATIONS["cs"][key] = value
    db.set_setting("ui_language", original_language)
    db.forget_settings()

# A trap: should escaping get lost somewhere, this attack gets through. A proof
# that the test can fail - the dangerous string really is dangerous.
check("<script>" in ATTACK, "and the attacking string really is runnable code")

print()
print("--- the waiting page after an update and a translation with an apostrophe ---")
# The waiting page is put together in Python (without a template, see _update_page),
# so Jinja's autoescape does not protect it. A translation from Weblate like "It's taking
# longer" would cause a SyntaxError in a JS string in apostrophes: the function zkus()
# would not come into being and the waiting page would stay at "Updating..." for ever after an update.
# And a tag in a translation would be rendered as HTML.
import json as _json  # noqa: E402
import re  # noqa: E402

from jellyscope.web import settings as _wn  # noqa: E402

ATTACK_APOSTROPHE = "It's </p><img src=x onerror=alert(1)>"
waiting_keys = ["Updating…", "This is taking longer than it should.", "Try again",
                "The new version is downloaded. The app is restarting now. As soon as it is up, you will be let through."]
original = {k: i18n.TRANSLATIONS["cs"].get(k) for k in waiting_keys}
for key in waiting_keys:
    i18n.TRANSLATIONS["cs"][key] = ATTACK_APOSTROPHE
original_language = db.get_setting("ui_language", "cs")
db.set_setting("ui_language", "cs")
db.forget_settings()
try:
    waiting_page = _wn._update_page("testovaci-nonce")
finally:
    for key, value in original.items():
        if value is None:
            i18n.TRANSLATIONS["cs"].pop(key, None)
        else:
            i18n.TRANSLATIONS["cs"][key] = value
    db.set_setting("ui_language", original_language)
    db.forget_settings()
check("<img" not in waiting_page, "a tag from a translation is not rendered in the waiting page")
expression = re.search(r"innerHTML\s*=\s*(.+?);\s*\n", waiting_page, re.S)
try:
    js_value = _json.loads(expression.group(1)) if expression else None
except ValueError:
    js_value = None
check(js_value is not None and "<img" not in js_value,
      "the text for a slow restart is a valid JS string, with an apostrophe too")

print()
print("--- texts in JavaScript go through tojson ---")
# The same in the templates. In <script> autoescape makes &#39; of an apostrophe, which
# then shows literally. In an attribute onsubmit="confirm('...')" it is worse:
# the browser turns &#39; back into an apostrophe before running it, the JS ends with an error
# and the form is sent WITHOUT the confirmation - the deletion of an account or a backup, say.
# Translations come from Weblate straight to the users (an update of the
# translations), without a release and without a check.
def _js_expressions(text: str) -> list[str]:
    expressions = []
    # re.I: the tag's letter case does not matter, and the end tag may have a space
    # or attributes (</script >) - both are watched by CodeQL py/bad-tag-filter.
    for block in re.findall(r"<script\b[^>]*>(.*?)</script\b[^>]*>", text, re.S | re.I):
        expressions += re.findall(r"\{\{(.*?)\}\}", block, re.S)
    for start in re.finditer(r"\son[a-z]+=([\"'])", text):
        quote, i, value = start.group(1), start.end(), ""
        while i < len(text) and text[i] != quote:
            if text.startswith("{{", i):
                end = text.find("}}", i)
                value += text[i:end + 2]
                i = end + 2
            else:
                i += 1
        expressions += re.findall(r"\{\{(.*?)\}\}", value, re.S)
    return expressions


without_tojson = []
for template in sorted((PROJECT / "jellyscope" / "templates").rglob("*.html")):
    for expression in _js_expressions(template.read_text(encoding="utf-8")):
        if "_(" in expression and "tojson" not in expression:
            without_tojson.append(f"{template.name}: {expression.strip()[:50]}")
check(not without_tojson, f"no translation in JS without tojson ({len(without_tojson)}: {without_tojson[:4]})")
# A trap: the search must find a translation in an attribute and in a script.
check(len(_js_expressions('<form onsubmit="return confirm(\'{{ _("a") }}\')">'
                          '<script>x = "{{ _("b") }}";</script>')) == 2,
      "and the search finds a translation in an attribute and in a script")

print()
print("--- an attack on all texts that go into a page without escaping ---")
# The previous section watches one place we already know about. This one asks the
# other way round: when runnable code is put into **every** text that goes into a
# chart, does code come out of it anywhere?
#
# The texts come from three sides and none of them is ours: translations are written by people through
# Weblate, names and titles are sent by Jellyfin, the release notes come
# from GitHub. All of them end in a chart that goes into the template through
# `| safe` - that is without the safety net Jinja otherwise holds.
from html.parser import HTMLParser  # noqa: E402


class _Parser(HTMLParser):
    """A part-time browser: what is a tag and what are its attributes.

    Searching for an attack in HTML as a string is not enough. `" onmouseover="alert(1)`
    stays visible in the text after escaping (`&quot; onmouseover=&quot;…`),
    but there it is a harmless sentence, not an event handler. The difference is told only by
    whoever takes the HTML apart into tags - so we do that too.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "iframe", "object", "embed"):
            self.found.append(f"<{tag}>")
        for name, value in attrs:
            name = (name or "").lower()
            value = (value or "").strip().lower()
            if name.startswith("on"):      # an event handler = code
                self.found.append(f"<{tag} {name}=>")
            if name in ("href", "src", "xlink:href", "action", "formaction"):
                clean = value.replace("\t", "").replace("\n", "")
                if clean.startswith(("javascript:", "data:text/html",
                                     "vbscript:")):
                    self.found.append(f"<{tag} {name}={clean[:24]}>")

    handle_startendtag = handle_starttag


def _runnable(html: str) -> list[str]:
    """The places where a text became code. An empty list = clean."""
    parser = _Parser()
    parser.feed(f"<div>{html}</div>")
    parser.close()
    return parser.found


# Four shapes of an attack. Each defeats another defence: a tag passes where there is
# no escaping; a quote escapes from an attribute; `javascript:` waits for a link;
# and closing tags break SVG, where other rules hold than in HTML.
_ATTACKS = {
    "a tag": "<script>alert(1)</script>",
    "escaping from an attribute": '" onmouseover="alert(1)',
    "an address with a script": "javascript:alert(1)",
    "breaking SVG": "</text></svg><script>alert(1)</script>",
}

# Translations really used in the charts.
_TRANSLATIONS = [charts.DAY_NAMES[0], "No data yet",
                 "Drag across the chart to pick a range.", "days", "Genre share",
                 "Where they watch from"]


def _charts(attack: str) -> dict[str, str]:
    """Every chart with the attack in all the texts it takes.

    Not only in the label - in the id too (it goes into a link), in a country's code, in a
    place's name and **in the colour**. The colour ends in the `style` attribute, so a quote
    in it is the same hole as a tag in a label.
    """
    rows = [{"label": attack, "value": 5, "item_count": 5, "hours": 5,
             "id": attack, "user_id": attack, "percent": 50, "gb": 5,
             "code": attack, "slot": 1, "color": attack},
            {"label": "druhý", "value": 3, "item_count": 3, "hours": 3,
             "id": "x", "user_id": "x", "percent": 50, "gb": 3,
             "code": "cs", "slot": 2, "color": "var(--accent)"}]
    points = [{"lat": 50.0, "lon": 14.4, "seconds": 60, "plays": 1, "people": 1,
               "misto": attack, "country": attack, "city": attack, "description": attack,
               "viewers": frozenset({"u1"})}]
    return {
        "hbar_chart": charts.hbar_chart(rows, "label", "value"),
        "hbar_chart with a link": charts.hbar_chart(
            rows, "label", "value", link_prefix="/users/",
            link_key="user_id"),
        "legend": charts.legend(rows),
        "stacked_bar": charts.stacked_bar(rows),
        "donut_chart": charts.donut_chart(rows),
        "heatmap": charts.heatmap([[1] * 24 for _ in range(7)]),
        "sparkline": charts.sparkline([{"day": attack, "hours": 1},
                                       {"day": "x", "hours": 2}]),
        "area_chart_multi": charts.area_chart_multi(
            [{"day": attack, "gb": 1}, {"day": "x", "gb": 2}], "day",
            [{"key": "gb", "label": attack, "color": attack}], selectable=True),
        "world_map": charts.world_map(points),
        "release notes": updates.notes_html(attack),
    }


def _try_attacks() -> list[str]:
    """Renders everything with everything and returns the list of places where code came out."""
    found: list[str] = []
    backup = {k: i18n.TRANSLATIONS["cs"].get(k) for k in _TRANSLATIONS}
    language = db.get_setting("ui_language", "cs")
    try:
        for label, attack in _ATTACKS.items():
            for key in _TRANSLATIONS:
                i18n.TRANSLATIONS["cs"][key] = attack
            db.set_setting("ui_language", "cs")
            db.forget_settings()
            for name, html in _charts(attack).items():
                for place in _runnable(html):
                    found.append(f"{name} / {label}: {place}")
    finally:
        for key, value in backup.items():
            if value is None:
                i18n.TRANSLATIONS["cs"].pop(key, None)
            else:
                i18n.TRANSLATIONS["cs"][key] = value
        db.set_setting("ui_language", language)
        db.forget_settings()
    return found


found = _try_attacks()
for item in found:
    print("       ", item)
check(not found, f"out of {len(_ATTACKS)} attacks × 10 charts no code gets out")

# And can that test fail at all? Zero findings means either a clean app or
# a blind test - the only way to tell them apart is to switch the defence off for a while.
# If the test does not squeal even at a hole we made in it ourselves,
# its silence means nothing.
_e_backup, _colour_backup = charts._e, charts._colour
try:
    charts._e = lambda value: str(value or "")
    charts._colour = lambda value: str(value or "")
    blind = _try_attacks()
finally:
    charts._e, charts._colour = _e_backup, _colour_backup
check(len(blind) > 10, "and without escaping code gets out (the test can fail)")

print()
print("--- the markdown of release notes (a foreign text from GitHub) ---")
notes_attacks = {
    # Without brackets in the address: a ( or ) is no link at all, so it would not test the scheme check.
    "javascript:": "[x](javascript:alert%281%29)", "JaVaScRiPt:": "[x](JaVaScRiPt:void0)",
    "data:": "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "an image with javascript:": "![x](javascript:void0)",
    "javascript: with brackets": "[x](javascript:alert(1))",
    # No space or bracket either: with them it is no address at all and the quote would not be tested.
    "a quote out of the address": '[x](https://a.cz/"onmouseover="location=name)',
    "a quote out of a bare address": 'https://a.cz/"onmouseover="location=name',
    "a quote out of an address in <>": '<https://a.cz/"onmouseover="location=name>',
    "a tag in the text, the address and the link's text": "<script>a</script> [<script>b</script>](https://a.cz/<script>c</script>)",
    "a link in a link": "[a [b](https://x.cz)](https://y.cz)",
}
notes_found = {name: updates.notes_html(text) for name, text in notes_attacks.items()}
check(not any(_runnable(html) for html in notes_found.values()),
      f"no attack through the markdown gets code into the page ({[n for n, h in notes_found.items() if _runnable(h)]})")
check(all("href" not in notes_found[n] for n in ("javascript:", "JaVaScRiPt:", "data:", "an image with javascript:",
                                                 "javascript: with brackets")),
      "an address without http(s) is no link")
link_attributes = [a for html in notes_found.values() for a in re.findall(r"<a ([^>]*)>", html)]
check(link_attributes and all(re.fullmatch(r'href="[^"<>]*" target="_blank" rel="noopener noreferrer"', a)
                              for a in link_attributes),
      f"a link has only href, target and rel - a quote does not leave href ({link_attributes})")
check(not re.search(r"<a [^>]*>(?:(?!</a>).)*<a ", notes_found["a link in a link"], re.S),
      f"no link inside a link ({notes_found['a link in a link']})")
check("<script>" not in "".join(notes_found.values()), "a tag stays text everywhere")

print()
print("--- the demo cannot lead off to a foreign server ---")
# The demo mode sends a person back where they came from - by the `referer`
# header. That comes from the visitor, so it cannot be trusted: when the whole
# address is taken from it, the app is an open redirector. A link looks as if
# it leads to jellyscope.cz, and ends elsewhere - exactly how passwords
# are collected.
#
# There used to be a check here, but it was done by a prefix ("starts with our
# address"), and `jellyscope.cz.utocnik.cz` passes that too.
from jellyscope.web import _referer_path  # noqa: E402

FOREIGN = [
    "http://jellyscope.cz.utocnik.cz/past",   # our name as a prefix
    "https://utocnik.cz/",
    "//utocnik.cz/past",                      # the browser reads it as an address
    "https://utocnik.cz/?x=http://jellyscope.cz",
    "javascript:alert(1)",
    "http://utocnik.cz",
]
for address in FOREIGN:
    target = _referer_path(address)
    check(target.startswith("/") and not target.startswith("//"),
          f"{address[:36]!r} -> {target!r} stays with us")
    check("utocnik.cz" not in target.split("?")[0],
          f"{address[:36]!r} does not lead elsewhere")

# And it still sends a person back where they were - otherwise the safeguard would be useless.
for address, expected in (("http://jellyscope.cz/settings?section=api",
                           "/settings?section=api"),
                          ("http://jellyscope.cz/library", "/library"),
                          ("http://jellyscope.cz/", "/"),
                          (None, "/"),
                          ("", "/")):
    check(_referer_path(address) == expected,
          f"{str(address)[:38]!r} -> {expected!r}")


print()
print("--- neither a password nor somebody else's address gets into the log ---")
# The log is passed on when something goes wrong - and so it gets to people it was
# not written for. A setting's value is therefore written into it only where it is
# without risk, and the list is **reversed**: it allows, it does not forbid.
# A denylist would have to know beforehand every secret that will ever come, and the one
# forgotten would be written out whole.
import io  # noqa: E402
import logging  # noqa: E402

SENSITIVE = {
    "jellyfin_api_key": "TAJNY-KLIC-123",
    "notify_smtp_heslo": "secretpass",
    "notify_telegram_token": "1234:ABCDEF",
    "notify_discord_webhook": "https://discord.com/api/webhooks/1/tajne",
    # Not secrets, but somebody else's data: an address and an account's name.
    "notify_smtp_komu": "petr@example.org",
    "notify_smtp_uzivatel": "petr.novak",
    "notify_smtp_odesilatel": "jellyscope@example.org",
    # A setting that does not exist yet - it must keep silent too.
    "notify_pushover_klic": "budouci-tajemstvi",
}

caught = io.StringIO()
listener = logging.StreamHandler(caught)
listener.setFormatter(logging.Formatter("%(message)s"))
logger = logging.getLogger("jellyscope.db")
logger.addHandler(listener)
original_level = logger.level
logger.setLevel(logging.INFO)
try:
    for key, value in SENSITIVE.items():
        db.set_setting(key, value)
    # And something harmless, so it is seen the log did not go mute entirely.
    db.set_setting("poll_interval", "42")
finally:
    logger.removeHandler(listener)
    logger.setLevel(original_level)

written = caught.getvalue()
for key, value in SENSITIVE.items():
    check(value not in written, f"{key}: the value is not in the log")
    check(key in written, f"{key}: but the change is recorded")
check("poll_interval: 10 -> 42" in written,
      "a harmless setting is written out with its value")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
