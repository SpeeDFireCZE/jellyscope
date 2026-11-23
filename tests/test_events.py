# -*- coding: utf-8 -*-
r"""Seasonal decoration: the calendar of holidays, the settings and the pages.

Around Christmas, Halloween, New Year's Eve and Easter Jellyscope decorates itself.
Watched here:

* the holidays' dates (both bounds included) and Easter for any year;
* where today's date comes from (the development hook → JELLYSCOPE_EVENT_DATE
  → the real date) and that a nonsensical variable breaks nothing;
* the administrator's switch and the choice "without motion" of an account.

Run:
    .venv/bin/python tests/test_events.py
"""
from __future__ import annotations

import html
import logging
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "udalosti.db")
os.environ["SECRET_KEY"] = "testovaci-klic"
os.environ["JELLYSCOPE_DEMO"] = "0"
os.environ.pop("JELLYSCOPE_EVENT_DATE", None)

from jellyscope import db, seasonal  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


def set_date(text: str | None) -> None:
    """Slips in the day through the environment variable (None = the real date)."""
    if text is None:
        os.environ.pop("JELLYSCOPE_EVENT_DATE", None)
    else:
        os.environ["JELLYSCOPE_EVENT_DATE"] = text


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)

print("--- the variable's name ---")
# JELLYSCOPE_EVENT_DATE since 1.9.0; the Czech name released earlier (1.8.x) still works, the new one wins.
os.environ["JELLYSCOPE_EVENT_DATE"] = "2026-12-24"
check(seasonal.today() == date(2026, 12, 24), f"JELLYSCOPE_EVENT_DATE sets the day ({seasonal.today()})")
os.environ[seasonal.OLD_VARIABLE] = "2026-10-31"
check(seasonal.today() == date(2026, 12, 24), f"with both, the English name wins ({seasonal.today()})")
os.environ.pop("JELLYSCOPE_EVENT_DATE")
check(seasonal.today() == date(2026, 10, 31), f"the old name still works alone ({seasonal.today()})")
os.environ.pop(seasonal.OLD_VARIABLE)
check(seasonal.OLD_VARIABLE == "JELLYSCOPE_UDALOST_DATUM", "the old name is the one 1.8.x documented")

print("--- Easter ---")
for year, expected in ((2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)),
                       (2027, date(2027, 3, 28)), (2038, date(2038, 4, 25)), (2285, date(2285, 3, 22))):
    check(seasonal.easter(year) == expected, f"{year}: {seasonal.easter(year)} (expected {expected})")
wrong = [year for year in range(1583, 4100)
         if seasonal.easter(year).weekday() != 6
         or not date(year, 3, 22) <= seasonal.easter(year) <= date(year, 4, 25)]
check(not wrong, f"1583–4099: always a Sunday between 22. 3. and 25. 4. ({wrong[:5]})")

print("--- dates ---")
for day, expected in (
        ("2026-10-23", None), ("2026-10-24", "halloween"), ("2026-10-31", "halloween"), ("2026-11-01", None),
        ("2026-12-16", None), ("2026-12-17", "christmas"), ("2026-12-26", "christmas"), ("2026-12-27", "newyear"),
        ("2026-12-31", "newyear"), ("2027-01-01", "newyear"), ("2027-01-02", None),
        ("2026-03-28", None), ("2026-03-29", "easter"), ("2026-04-06", "easter"), ("2026-04-07", None)):
    got = seasonal.event_for(date.fromisoformat(day))
    check(got == expected, f"{day}: {got} (expected {expected})")
check(seasonal.EVENTS == ("christmas", "halloween", "newyear", "easter"), "the list of keys")

print("--- where today comes from ---")
set_date(None)
check(seasonal.today() == date.today(), "without the variable the real date")
set_date(" 2026-12-24 ")
check(seasonal.today() == date(2026, 12, 24), "a variable with spaces around holds")
records: list[logging.LogRecord] = []


class Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        records.append(record)


logging.getLogger("jellyscope.seasonal").addHandler(Capture())
set_date("24.12.2026")
check(seasonal.today() == date.today() and seasonal.today() == date.today(),
      "a nonsensical variable = the real date")
check(len(records) == 1, f"the nonsense is in the log once, not at every call ({len(records)}×)")
set_date("2026-12-24")
seasonal.DATE_SOURCE = lambda: date(2027, 3, 29)
check(seasonal.today() == date(2027, 3, 29), "the development hook takes precedence over the variable")
seasonal.DATE_SOURCE = lambda: None
check(seasonal.today() == date(2026, 12, 24), "a hook without a date = the variable")
seasonal.DATE_SOURCE = None

print("--- on / active / motion ---")
set_date("2026-12-24")
check(seasonal.enabled() and seasonal.active() == "christmas", "on by default, christmas at Christmas")
db.set_setting("ui_events", "0")
db.forget_settings()
check(not seasonal.enabled() and seasonal.active() is None, "switched off by the administrator = nothing")
check(seasonal.for_template() == "", "for the template a switched-off decoration = empty")
db.set_setting("ui_events", "1")
db.forget_settings()
check(seasonal.for_template() == "christmas", "for the template the holiday's key")
set_date("2026-07-01")
check(seasonal.active() is None and seasonal.for_template() == "", "nothing in summer")
check(seasonal.motion(None) and seasonal.motion({"events_motion": None})
      and not seasonal.motion({"events_motion": "0"}) and seasonal.motion({"events_motion": "x"}),
      "motion: only “0” switches it off")

print("--- the settings and the account ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts  # noqa: E402
from jellyscope.web import app  # noqa: E402


def sign_in(name: str, password: str) -> TestClient:
    k = TestClient(app)
    k.post("/login", data={"username": name, "password": password, "method": "local"},
           follow_redirects=False)
    return k


accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "readerpass")
accounts.create("viewer", "viewerpass")
with db.connect() as conn:
    conn.execute("UPDATE accounts SET jellyfin_user_id = 'u-viewer' WHERE username = 'viewer'")
    conn.commit()
admin = sign_in("admin", "longpassword")


def setting() -> str:
    return db.get_setting("ui_events", "") or ""


admin.post("/settings/interface", data={"ui_events": "0"}, follow_redirects=False)
db.forget_settings()
check(setting() == "0", f"the administrator switches the decoration off ({setting()!r})")
admin.post("/settings/interface", data={}, follow_redirects=False)
db.forget_settings()
check(setting() == "1", f"a form without the field does not switch the decoration off ({setting()!r})")
interface_page = admin.get("/settings?section=interface").text
check('name="ui_events"' in interface_page
      and "Kolem některých svátků se Jellyscope sám ozdobí. Tady se to dá vypnout." in interface_page,
      "Interface has a switch with a label")
card = interface_page[interface_page.find("Sezónní výzdoba"):]
card = card[:card.find("</form>")]
check(card and not any(s in card for s in ("Halloween", "Vánoce", "Velikonoce", "Silvestr")),
      "the switch's card does not give the holidays away")

reader = sign_in("reader", "readerpass")
account_id = accounts.get_by_name("reader")["id"]
check('action="/account/event-motion"' in reader.get("/account").text,
      "My account offers the choice of motion when the decoration is on")
reader.post("/account/event-motion", data={"motion": "0"}, follow_redirects=False)
check(accounts.get(account_id)["events_motion"] == "0", "an account switches motion off")
reader.post("/account/event-motion", data={"motion": "1"}, follow_redirects=False)
check(accounts.get(account_id)["events_motion"] is None, "and on again (NULL)")
reader.post("/account/event-motion", data={"motion": "nesmysl"}, follow_redirects=False)
check(accounts.get(account_id)["events_motion"] is None, "nonsense = motion on")
db.set_setting("ui_events", "0")
db.forget_settings()
check('action="/account/event-motion"' not in reader.get("/account").text,
      "with the decoration off the choice is not on My account")
db.set_setting("ui_events", "1")
db.forget_settings()

viewer = sign_in("viewer", "viewerpass")
response = viewer.post("/account/event-motion", data={"motion": "0"}, follow_redirects=False)
check(response.status_code == 303 and response.headers.get("location") == "/account"
      and accounts.get_by_name("viewer")["events_motion"] == "0",
      f"a viewer from Jellyfin may switch motion off ({response.status_code} {response.headers.get('location')})")

print("--- the pages ---")
anonymous = TestClient(app)
admin_id = accounts.get_by_name("admin")["id"]
for day, key in (("2026-10-31", "halloween"), ("2026-12-24", "christmas"),
                 ("2026-12-31", "newyear"), ("2026-04-05", "easter")):
    set_date(day)
    for address, page in (("/login", anonymous.get("/login").text), ("/", admin.get("/").text)):
        check(f'data-event="{key}"' in page and 'data-event-motion="1"' in page
              and f"/static/events/{key}.css" in page
              and "/static/events/events.css" in page
              and "/static/events/events.js" in page
              and 'class="brand-logo"' in page,
              f"{day} {address}: the decoration {key}")
set_date("2026-12-24")
accounts.set_event_motion(admin_id, False)
check('data-event-motion="0"' in admin.get("/").text, "an account without motion: data-event-motion=\"0\"")
check('data-event-motion="1"' in anonymous.get("/login").text, "the sign-in page always with motion")
accounts.set_event_motion(admin_id, True)


def without_decoration(page: str) -> bool:
    return not any(s in page for s in ("/static/events/", "data-event", "brand-ornaments",
                                       "brand-logo"))


set_date("2026-07-01")
check(without_decoration(anonymous.get("/login").text) and without_decoration(admin.get("/").text),
      "in summer the page contains nothing of the decoration")
set_date("2026-12-24")
db.set_setting("ui_events", "0")
db.forget_settings()
check(without_decoration(anonymous.get("/login").text) and without_decoration(admin.get("/").text),
      "a switched-off decoration: nothing even at Christmas")
db.set_setting("ui_events", "1")
db.forget_settings()

original_enabled = seasonal.enabled


def broken() -> bool:
    raise RuntimeError("the database does not answer")


seasonal.enabled = broken
fallback_page = anonymous.get("/login")
check(seasonal.for_template() == "" and fallback_page.status_code == 200 and without_decoration(fallback_page.text),
      f"when the settings cannot be read, the page runs without the decoration ({fallback_page.status_code})")
seasonal.enabled = original_enabled

print("--- the decoration's files ---")
STATIC = PROJECT / "jellyscope" / "static" / "events"
for key in seasonal.EVENTS:
    css = (STATIC / f"{key}.css").read_text(encoding="utf-8") if (STATIC / f"{key}.css").exists() else ""
    check("prefers-reduced-motion" in css and '[data-event-motion="0"]' in css,
          f"{key}.css: the ornaments by the logo stand without motion (the system and the account)")
shared = (STATIC / "events.css").read_text(encoding="utf-8") if (STATIC / "events.css").exists() else ""
check("@media print" in shared and "pointer-events: none" in shared,
      "events.css: it is not printed and clicks go through")

print("--- effects (events.js) ---")
js_file = STATIC / "events.js"
js = js_file.read_text(encoding="utf-8") if js_file.exists() else ""
for what, why in (("visibilitychange", "stands still on a hidden tab"),
                  ("prefers-reduced-motion", "watches the system's limit on animations"),
                  ("data-event-motion", "watches the account's choice"),
                  ("aria-hidden", "screen readers skip the layer"),
                  ("resize", "it is recomputed when the window changes")):
    check(what in js, f"events.js: {why}")
import re  # noqa: E402

limits = dict(re.findall(r"([A-Z_]+)\s*=\s*(\d+)\s*[,;]", js))
check(limits.get("SNOW") == "50" and limits.get("SNOW_FEW") == "25"
      and limits.get("BATS") == "6" and limits.get("BATS_FEW") == "3"
      and limits.get("NARROW") == "720",
      f"limits of the number of elements: snow 50/25, bats 6/3, a narrow window under 720 px ({limits})")

print("--- data unchanged ---")
# A holiday changes the application's colour (--accent). The state "info" (Direct stream),
# single-colour charts and the water on the map carry meaning though - at Halloween
# direct streaming would look like a transcode. They use --data-color.
from jellyscope import charts  # noqa: E402

ACCENT = re.compile(r"var\(--accent(-2)?\)")
check(charts.ROLE_COLOURS["info"] == "var(--data-color)", f"the state info = the data colour ({charts.ROLE_COLOURS['info']})")
bar = charts.stacked_bar([{"label": "a", "value": 3, "role": "info"}, {"label": "b", "value": 1}])
check("var(--data-color)" in bar and "var(--ink-data-color" in bar, "the info piece: the data colour and its ink")
for source_file in ("jellyscope/charts.py", "jellyscope/templates/_card_growth.html",
                    "jellyscope/templates/_card_peaks_by_day.html", "jellyscope/templates/_network_live.html"):
    text = (PROJECT / source_file).read_text(encoding="utf-8")
    check(not ACCENT.search(text), f"{source_file}: charts without the holiday colour ({ACCENT.findall(text)[:3]})")
STYLE = (PROJECT / "jellyscope" / "static" / "style.css").read_text(encoding="utf-8")
for selector in (".badge.info     .dot", ".session-tags .badge.info {", ".chart-selection {",
                 ".chart .chart-hit circle", ".map-detailed .v-lakes", ".map-detailed .v-rivers {"):
    i = STYLE.find(selector)
    body = STYLE[i:STYLE.find("}", i)] if i >= 0 else ""
    check(i >= 0 and "--data-color" in body and not ACCENT.search(body), f"style.css {selector.strip()}: the data colour")

print("--- the effects' robustness ---")
# The attribute fill="var(--x)" in SVG is not taken by every browser; style="fill: var(--x)" is.
for source_file in (STATIC / "events.js", PROJECT / "jellyscope" / "templates" / "_brand.html"):
    text = source_file.read_text(encoding="utf-8")
    attributes = re.findall(r'(?:fill|stroke|stop-color)="var\(', text)
    check(not attributes, f"{source_file.name}: colours from variables only through style, not an attribute ({len(attributes)}×)")
# A mobile browser sends resize at every hiding of the address bar.
# The effect is to be rebuilt only when the WIDTH changes, the height only recomputes --h.
check(re.search(r"innerWidth\s*!==?\s*\w+|\w+\s*!==?\s*window\.innerWidth", js) is not None,
      "events.js: when only the window's height changes the effect is not rebuilt")

print("--- games after clicking an ornament ---")
import json as json_module  # noqa: E402


def game_texts(page: str) -> dict | None:
    match = re.search(r'<script type="application/json" id="event-texts">(.*?)</script>', page, re.S)
    return json_module.loads(match.group(1)) if match else None


def game_button(page: str) -> str:
    match = re.search(r'<button[^>]*class="event-game[ "][^>]*>', page)
    return match.group(0) if match else ""


for day, key, wish in (("2026-12-24", "christmas", "Veselé Vánoce!"), ("2026-10-31", "halloween", None),
                       ("2026-12-31", "newyear", "Šťastný nový rok!"), ("2026-04-05", "easter", "Veselé Velikonoce!")):
    set_date(day)
    for address, page in (("/login", anonymous.get("/login").text), ("/", admin.get("/").text)):
        tl, tx = game_button(page), game_texts(page) or {}
        # The button is an icon (its SVG is aria-hidden): its name is the ornament's text, not empty.
        check(f'data-game="{key}"' in tl and 'type="button"' in tl and tx.get("ornament")
              and f'aria-label="{html.escape(tx["ornament"], quote=True)}"' in tl
              and tx.get("close") and tx.get("hint")
              and (wish is None or tx.get("wish") == wish),
              f"{day} {address}: the game button {key} and its texts")
# The code of today's holiday's game is downloaded with the page (in the head), not only after
# the click - on the Overview it would otherwise wait in the queue behind the posters.
for day, key in (("2026-12-24", "christmas"), ("2026-10-31", "halloween"),
                 ("2026-12-31", "newyear"), ("2026-04-05", "easter")):
    set_date(day)
    for address, page in (("/login", anonymous.get("/login").text), ("/", admin.get("/").text)):
        head = page.split("</head>")[0]
        check(f'<script defer src="/static/events/games/{key}.js?v=' in head
              and head.find("/events.js?v=") < head.find(f"/games/{key}.js?v="),
              f"{day} {address}: the game {key} is downloaded with the page (after events.js)")
set_date("2026-07-01")
check("/static/events/games/" not in admin.get("/").text, "outside the season no game is downloaded")

# The New Year's Eve finale writes the year that is celebrated - by the server's date (a
# slipped-in one too), not by the browser's clock.
check(seasonal.celebrated_year(date(2026, 12, 27)) == 2027 and seasonal.celebrated_year(date(2027, 1, 1)) == 2027,
      "the celebrated year: 27. 12. and 1. 1. are the new one")
for day in ("2026-12-31", "2027-01-01"):
    set_date(day)
    check((game_texts(admin.get("/").text) or {}).get("year") == "2027", f"{day}: the finale celebrates 2027")
accounts.set_language(admin_id, "en")
check((game_texts(admin.get("/").text) or {}).get("wish") == "Happy New Year!", "the New Year wish with an exclamation mark (en)")
accounts.set_language(admin_id, None)


# The development date source reads the database; when it fails, the year for the template must not
# bring the page down (the holiday already shows at that moment).
def _outage() -> date:
    raise RuntimeError("the database does not answer")


seasonal.DATE_SOURCE = _outage
try:
    year = seasonal.year_for_template()
except Exception as error:  # noqa: BLE001
    year = f"exception {error}"
seasonal.DATE_SOURCE = None
check(year == str(seasonal.celebrated_year(date.today())), f"the year for the template survives an outage of the date source ({year})")
set_date("2026-12-24")
from jellyscope import web  # noqa: E402

_year_global = web.templates.env.globals["seasonal_year"]
web.templates.env.globals["seasonal_year"] = _outage
try:
    _christmas = admin.get("/")
    _christmas_ok = _christmas.status_code == 200 and "rok" not in (game_texts(_christmas.text) or {})
except RuntimeError:
    _christmas_ok = False
web.templates.env.globals["seasonal_year"] = _year_global
check(_christmas_ok, "the year is computed only on New Year's Eve")

# Signing in with a code (login_quick.html) has the ornament and the texts like the other pages.
from starlette.requests import Request  # noqa: E402

_request = Request({"type": "http", "method": "POST", "path": "/login/quick", "headers": [],
                    "query_string": b"", "session": {}, "app": web.app})
_quick = web.templates.TemplateResponse(_request, "login_quick.html", {"code_value": "123456"}).body.decode()
# The context as from the route (web/login.py) - a page without the code would pass here otherwise too.
check("123456" in _quick, "the page shows the code from the context (code_value)")
check(game_button(_quick) and (game_texts(_quick) or {}).get("wish") == "Veselé Vánoce!",
      "signing in with a code: the game button and the texts")
set_date("2026-12-24")
page = admin.get("/").text
link = page.find('<a class="brand"')
check(link >= 0 and page.find("event-game", link) > page.find("</a>", link),
      "the ornament is outside the link to the Overview (a click on it does not open the Overview)")
accounts.set_language(admin_id, "en")
check((game_texts(admin.get("/").text) or {}).get("wish") == "Merry Christmas!", "the game texts in the account's language (en)")
accounts.set_language(admin_id, None)
set_date("2026-07-01")
check("event-game" not in anonymous.get("/login").text and "event-texts" not in admin.get("/").text,
      "outside the season no button and no game texts")
for key in seasonal.EVENTS:
    source_file = STATIC / "games" / f"{key}.js"
    content = source_file.read_text(encoding="utf-8") if source_file.exists() else ""
    check(f'JellyscopeGames.register("{key}"' in content, f"games/{key}.js registers itself")
js = (STATIC / "events.js").read_text(encoding="utf-8")
for what, why in (("JellyscopeGames", "the games' frame"), ("Escape", "Esc closes the scene"), ("aria-modal", "the scene is a dialog"),
                  ("/static/events/games/", "a game is downloaded only after the click"), (".focus()", "the focus into the scene and back"),
                  ('"Tab"', "Tab stays in the scene"), ("onerror", "a game that did not load breaks nothing")):
    check(what in js, f"events.js: {why}")

# The code of the single games: texts only from api.texts (translations), timers through api
# (cancelled after the scene is closed), control by keyboard too, without motion the final state.
GAME_EXPECTS = {
    "christmas": ("api.texts.wish", "api.texts.hint", "api.texts.gift", '"Enter"', "tabindex",
                  "api.quiet()", "api.confetti(", "api.frame(", "api.after("),
    "halloween": ("api.texts.hint", "api.texts.lightUp", "api.texts.blowOut", "api.texts.again",
                  "api.texts.pumpkin", "api.quiet()", "api.pointInSvg(", "pointercancel"),
    "newyear": ("api.texts.hint", "api.texts.burning", "api.texts.lighter", "api.texts.wish",
                "api.texts.year", '"Enter"', "tabindex", "api.quiet()", "api.pointInSvg(", "api.frame(",
                "api.after(", "api.close()", "event-finale", "onfinish"),
    "easter": ("api.texts.hint", "api.texts.egg", "api.texts.pip", "api.texts.wish", '"Enter"',
               "tabindex", "api.quiet()", "api.confetti(", "api.frame(", "api.after("),
}
CZECH_TEXTS = ("Veselé", "Klikni", "Dárek", "Dýně", "Hoří", "píp", "Šťastný")
# The ids in the games' drawings have the prefix event- like the rest of the decoration - otherwise they
# could meet the ids of charts or another drawing on the page.
for source_file in sorted((STATIC / "games").glob("*.js")):
    content = source_file.read_text(encoding="utf-8")
    ids = re.findall(r'\bid="([^"]+)"', content) + re.findall(r'\bid = "([^"]+)"', content)
    wrong = sorted({i for i in ids if not i.startswith("event-")})
    check(ids and not wrong, f"games/{source_file.name}: ids with the prefix event- ({wrong})")
for source_file in ("halloween.css",):
    references = re.findall(r"url\(#([^)]+)\)", (STATIC / source_file).read_text(encoding="utf-8"))
    check(all(o.startswith("event-") for o in references), f"{source_file}: references to the games' ids with the prefix ({references})")
# Enlarging an ornament under the mouse only where a mouse is - on touch it would stay hanging.
css = (STATIC / "events.css").read_text(encoding="utf-8")
outside_media = re.sub(r"@media \(hover: hover\) \{[^}]*\}\s*\}", "", css)
check(".event-game:hover" in css and ".event-game:hover" not in outside_media,
      "enlarging an ornament under the mouse only in @media (hover: hover)")
for key, expects in GAME_EXPECTS.items():
    content = (STATIC / "games" / f"{key}.js").read_text(encoding="utf-8")
    missing = [what for what in expects if what not in content]
    check(not missing, f"games/{key}.js: texts, the keyboard, quiet, timers through api (missing {missing})")
    check("setTimeout(" not in content and "requestAnimationFrame(" not in content,
          f"games/{key}.js: no timer outside api (after the scene is closed it would run on)")
    check(not [t for t in CZECH_TEXTS if t in content], f"games/{key}.js: no hard-coded Czech text")

print("--- without the development files (prod, demo) ---")
# jellyscope/devtools.py is not copied into prod (DEV_ONLY). The application without it
# must start and take the date only from the variable or from the calendar.
import subprocess  # noqa: E402

# The Jinja global devtools.py registers; the run below checks it is missing without devtools.py.
# In dev the name must exist in this process - a stale name would make that check pass over anything.
DEVTOOLS_GLOBAL = "devtools_date"
if (PROJECT / "jellyscope" / "devtools.py").is_file():
    check(DEVTOOLS_GLOBAL in web.templates.env.globals,
          f"in dev devtools.py registers the global {DEVTOOLS_GLOBAL} (the check below watches a real name)")
environment = {**os.environ, "JELLYSCOPE_HOME": tempfile.mkdtemp(),
               "DATABASE_PATH": str(Path(tempfile.mkdtemp()) / "bez.db"), "PYTHONIOENCODING": "utf-8"}
run = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.modules['jellyscope.devtools'] = None\n"
     "import jellyscope.web as w\n"
     "from jellyscope import seasonal\n"
     "assert w.devtools is None, 'devtools'\n"
     "assert seasonal.DATE_SOURCE is None, 'date source'\n"
     f"assert {DEVTOOLS_GLOBAL!r} not in w.templates.env.globals, 'global'\n"
     "print('without devtools ok')"],
    cwd=str(PROJECT), env=environment, capture_output=True, text=True, timeout=120)
check(run.returncode == 0 and "without devtools ok" in run.stdout,
      f"the application runs without devtools.py too ({run.returncode} {run.stderr.strip()[-160:]})")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
