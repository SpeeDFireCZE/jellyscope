r"""A test of signing in, accounts, an empty database and the helper functions.

An empty database is the state in which you see the app at the very first -
and it is also the state in which something breaks most easily (division by zero,
an empty list, a missing row). That is why it is tested separately.

Run:
    .venv/bin/python tests/test_basics.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

# A folder of its own for every run. There used to be a fixed name in %TEMP% here -
# but then two runs of the tests at the same time (or two people) touched the same
# file and tripped over each other. The failure then looked like a failure of the
# app, not like a collision.
TMP_DB = Path(tempfile.mkdtemp()) / "jellyscope_test_basics.db"

os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
# A home of its own: without it DATABASE_PATH would beat the stored choice of the
# database in data/database.json and the test would run against the live database.
os.environ["JELLYSCOPE_HOME"] = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = str(TMP_DB)
os.environ["SECRET_KEY"] = "testovaci-klic"

from fastapi.testclient import TestClient  # noqa: E402

from _package import sample_movie  # noqa: E402
from jellyscope import accounts, charts, db, formatting, languages, probe  # noqa: E402
from jellyscope.i18n import translate as _t  # noqa: E402
from jellyscope.web import app  # noqa: E402

failures = 0
ROUTES = ["/", "/video/insights", "/video/languages", "/library", "/users", "/history",
          "/settings", "/?days=365"]


def check(ok: bool, message: str) -> None:
    global failures
    print(f"{'OK     ' if ok else 'FAIL   '}{message}")
    if not ok:
        failures += 1


with TestClient(app) as client:
    db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
    sample_movie()  # the Movies & series section exists only with a movie (navigation.has_video)
    print("--- without an account everything must lead to the first setup ---")
    for route in ROUTES:
        response = client.get(route, follow_redirects=False)
        check(
            response.status_code == 303 and response.headers.get("location") == "/setup",
            f"{route} -> {response.status_code} {response.headers.get('location', '')}",
        )
    check(client.get("/setup").status_code == 200, "/setup is shown")

    print("--- creating the first administrator ---")
    bad_inputs = [
        ({"username": "ab", "password": "longpassword", "password_again": "longpassword"},
         "a name too short"),
        ({"username": "petr", "password": "krat", "password_again": "krat"},
         "a password too short"),
        ({"username": "petr", "password": "longpassword", "password_again": "otherpass"},
         "the passwords do not match"),
        ({"username": "petr novak", "password": "longpassword", "password_again": "longpassword"},
         "a space in the name"),
    ]
    for data, label in bad_inputs:
        response = client.post("/setup", data=data, follow_redirects=False)
        check(response.status_code == 400, f"refused: {label}")
    check(not accounts.any_exists(), "after the invalid attempts still no account")

    response = client.post(
        "/setup",
        data={"username": "petr", "password": "secretpass", "password_again": "secretpass",
              "ui_language": "cs"},           # the first administrator picks Czech (the checks below read it)
        follow_redirects=False,
    )
    check(response.status_code == 303, "the administrator created and signed in straight away")
    check(accounts.count() == 1, "the database has one account")
    check(accounts.all_accounts()[0]["is_admin"] == 1, "the first account is an administrator")

    # The password must never be stored in a readable form.
    stored = accounts.get_by_name("petr")["password_hash"]
    check("secretpass" not in stored, "the password is not in the database in a readable form")
    check(stored.startswith("pbkdf2_sha256$"), "the password is stored as a PBKDF2 fingerprint")
    check(accounts.verify_password("secretpass", stored), "the fingerprint matches the right password")
    check(not accounts.verify_password("otherpass", stored), "the fingerprint does not match a wrong password")

    # A second "first administrator" must not get through any more.
    response = client.post(
        "/setup",
        data={"username": "podvod", "password": "longpassword", "password_again": "longpassword"},
        follow_redirects=False,
    )
    check(accounts.count() == 1, "a repeated /setup creates no other account")

    print("--- an empty database, signed in ---")
    for route in ROUTES + ["/health", "/api/now-playing", "/users/neexistuje"]:
        expected = 404 if route == "/users/neexistuje" else 200
        try:
            response = client.get(route)
        except Exception as exc:  # noqa: BLE001
            check(False, f"{route}: {type(exc).__name__}: {exc}")
            continue
        check(response.status_code == expected,
              f"{route} -> {response.status_code} ({len(response.content)} B)")

    print("--- managing accounts ---")
    response = client.post("/settings/accounts/create", data={
        "username": "jana", "password": "otherpass1", "password_again": "otherpass1",
    }, follow_redirects=False)
    check(accounts.count() == 2, "the administrator created a reader's account")
    check(accounts.get_by_name("jana")["is_admin"] == 0, "the new account is not an administrator")

    response = client.post("/settings/accounts/create", data={
        "username": "JANA", "password": "otherpass1", "password_again": "otherpass1",
    }, follow_redirects=True)
    check(accounts.count() == 2, "a duplicate name (other letter case) refused")

    check(client.post("/logout", follow_redirects=False).status_code == 303, "signing out")
    check(client.get("/", follow_redirects=False).status_code == 303, "after signing out the gate")

    print("--- signing in ---")
    response = client.post("/login", data={"username": "petr", "password": "spatne"},
                           follow_redirects=False)
    check(response.status_code == 401, f"a wrong password refused ({response.status_code})")

    response = client.post("/login", data={"username": "neexistuje", "password": "cokoliv"},
                           follow_redirects=False)
    check(response.status_code == 401, "an account that does not exist refused")

    response = client.post("/login", data={"username": "jana", "password": "otherpass1"},
                           follow_redirects=False)
    check(response.status_code == 303, "the reader signed in")

    print("--- a reader's rights ---")
    check(client.get("/").status_code == 200, "the reader sees the statistics")
    # Settings are for the administrator only - see `access`. A reader changes their own
    # password on the My account page (/account), see test_my_account.py.
    check(client.get("/settings").status_code == 403, "the reader does not see the settings")

    for route, data in [
        ("/settings", {"tech_source": "ffprobe", "poll_interval": "5",
                       "library_sync_minutes": "10", "ffprobe_path": "",
                       "ffprobe_concurrency": "2", "path_mappings": "[]"}),
        ("/settings/sync", {}),
        ("/settings/connection", {"jellyfin_url": "http://x", "action": "test"}),
        ("/settings/accounts/create", {"username": "vetrelec", "password": "longpassword",
                                       "password_again": "longpassword"}),
    ]:
        response = client.post(route, data=data, follow_redirects=False)
        check(response.status_code == 403, f"refused to the reader: POST {route}")

    check(accounts.count() == 2, "the reader created no account")

    # Not even their own password can be changed through Settings - the whole section is for the
    # administrator. One's own password belongs on My account (test_my_account.py).
    jana = accounts.get_by_name("jana")
    response = client.post("/settings/accounts/password", data={
        "account_id": jana["id"], "password": "newpass1", "password_again": "newpass1",
    }, follow_redirects=False)
    check(response.status_code == 403, "the reader does not change their password (Settings are closed)")
    check(accounts.authenticate("jana", "otherpass1") is not None,
          "the original password still holds")

    # Somebody else's not at all.
    petr = accounts.get_by_name("petr")
    response = client.post("/settings/accounts/password", data={
        "account_id": petr["id"], "password": "prevzato11", "password_again": "prevzato11",
    }, follow_redirects=False)
    check(response.status_code == 403, "the reader may not change somebody else's password")
    check(accounts.authenticate("petr", "secretpass") is not None, "the other password stayed")

    client.post("/logout")

    print("--- the last administrator cannot be removed ---")
    client.post("/login", data={"username": "petr", "password": "secretpass"})
    response = client.post("/settings/accounts/delete",
                           data={"account_id": petr["id"]}, follow_redirects=True)
    check(accounts.get_by_name("petr") is not None, "one's own account cannot be deleted")

    # The connection test is a part of the form for connecting - it tests
    # what is filled in, not the stored settings.
    response = client.post(
        "/settings/connection",
        data={"jellyfin_url": "http://127.0.0.1:1", "action": "test"},
        follow_redirects=True,
    )
    check(
        response.status_code == 200 and "selhalo" in response.text.lower(),
        "a connection test to an unreachable server reports an error instead of falling over",
    )

    # An empty address must not end with a message about a missing http:// - that is
    # confusing. It is to say the address is not filled in.
    response = client.post(
        "/settings/connection", data={"jellyfin_url": "", "action": "test"},
        follow_redirects=True,
    )
    check("vyplň adresu" in response.text.lower() or "vyplň adresu" in response.text,
          "an empty address: it says the address is missing")

print("--- keeping the position when the period changes ---")
# The 7/30/90 days filter changes the whole page, so it loads again - and a new
# load jumps to the top. The script in static/js/app.js remembers the position.
macros = (PROJECT / "jellyscope" / "templates" / "_macros.html").read_text(encoding="utf-8")
# Since the rebuild for CSP the scripts are without inline code, in static/js/app.js.
base = ((PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
        + (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8"))
check("data-keep-scroll" in macros, "the period filter is marked data-keep-scroll")
check("data-keep-scroll" in base, "the scripts have the handler that stores the position")
check("sessionStorage" in base and "scrollTo" in base,
      "the position is stored in sessionStorage and restored by scrollTo")
check("localStorage.setItem(\"scroll" not in base,
      "the position does not stay in localStorage for ever")

print("--- an episode's code ---")
# Specials are season 0 - zero is a valid number, not "unknown".
for row, want in [
    ({"parent_index_number": 1, "index_number": 3}, "S01E03"),
    ({"parent_index_number": 0, "index_number": 5}, "S00E05"),
    ({"parent_index_number": 2, "index_number": None}, ""),
    ({"parent_index_number": None, "index_number": 4}, ""),
    ({}, ""),
]:
    got = formatting.episode_code(row)
    check(got == want, f"episode_code({row}) = {got!r}, expecting {want!r}")

print("--- formatting numbers ---")
for got, want in [
    (formatting.bytes_human(0), "-"),
    (formatting.bytes_human(1536), "2 KB"),
    (formatting.bytes_human(52 * 1024 ** 3), "52,0 GB"),
    (formatting.bytes_human(520 * 1024 ** 3), "520 GB"),
    (formatting.hours_human(0), "0 min"),
    (formatting.hours_human(0.5), "30 min"),
    (formatting.hours_human(3.7), "3 h 42 min"),
    (formatting.bitrate_human(8_000_000), "8,0 Mb/s"),
    (formatting.bitrate_human(None), "-"),
    (formatting.resolution_human(2160), "4K"),
    (formatting.resolution_human(None), "-"),
    (formatting.number(1234567), "1 234 567"),
    (formatting.percent(12.345, 1), "12,3 %"),
    (formatting.datetime_human(None), "-"),
    (formatting.relative_human(None), "-"),
]:
    check(got == want, f"{got!r} == {want!r}")

print("--- numbers in the page's language ---")
# The server wrote every number the Czech way ("12,5 %", "1 234") while the page's
# JavaScript wrote the live counts in the page's language ("1,234") - on an English
# page a decimal comma then read as a thousands separator. Czech and Spanish keep
# what they had, English gets its own notation.
from jellyscope import charts as _charts, i18n as _i18n  # noqa: E402

for code, want in [
    ("cs", ["1 234 567", "12,3 %", "52,0 GB", "1 000 GB", "8,0 Mb/s", "1 234 h", "1 234", "2,5"]),
    ("es", ["1 234 567", "12,3 %", "52,0 GB", "1 000 GB", "8,0 Mb/s", "1 234 h", "1 234", "2,5"]),
    ("en", ["1,234,567", "12.3 %", "52.0 GB", "1,000 GB", "8.0 Mb/s", "1,234 h", "1,234", "2.5"]),
]:
    token = _i18n.set_account_language(code)
    try:
        got = [formatting.number(1234567), formatting.percent(12.345, 1),
               formatting.bytes_human(52 * 1024 ** 3), formatting.bytes_human(1000 * 1024 ** 3),
               formatting.bitrate_human(8_000_000), formatting.hours_human(1234),
               _charts._fmt(1234), _charts._fmt(2.5)]
    finally:
        _i18n.reset_account_language(token)
    check(got == want, f"numbers on a page in {code}: {got}")
check(_i18n.translate_plural("{n} records", 1234, "en") == "1,234 records"
      and _i18n.translate_plural("{n} records", 1234, "cs") == "1 234 záznamů",
      "a count in a sentence is written in the sentence's language, not the page's")

for raw in ("2026-08-12T09:47:00.1234567Z", "2026-08-12 09:47:00", "2026-08-12T09:47:00+02:00"):
    check(formatting.datetime_human(raw) != "-", f"the time {raw!r} is read")

print("--- unifying languages ---")
# Different notations of the same language must end at one value - otherwise the
# statistics would show four "Czechs".
for raw, want in [
    ("ces", "cs"), ("cze", "cs"), ("CS", "cs"), ("Czech", "cs"), ("cs-CZ", "cs"),
    ("eng", "en"), ("en-US", "en"), ("ger", "de"), ("slo", "sk"),
    ("", "und"), (None, "und"), ("und", "und"), ("zxx", "und"),
    ("klingon", "klingon"),
]:
    check(languages.normalize(raw) == want, f"normalize({raw!r}) -> {want!r}")

check(languages.display("ces") == "Čeština", "ces is shown as Čeština")
check(languages.display(None) == "Neuvedeno", "a missing language is shown as Neuvedeno")
check(languages.pack(["eng", "ces", "ces", None]) == "cs,en,und",
      f"pack sorts, removes duplicates and puts 'und' at the end: {languages.pack(['eng', 'ces', 'ces', None])!r}")
check(languages.unpack("cs,en") == ["cs", "en"], "unpack")
check(languages.unpack(None) == [], "unpack of an empty value")
check(languages.combination_label("cs,en") == "CS + EN", "the label of a combination")
check(languages.combination_label("und") == "Neuvedeno", "the label without a language")
check(languages.combination_label("cs,de,en,pl") == "CS + DE + EN + PL",
      "four languages are written out whole")
check(languages.combination_label("cs,de,en,pl,ru,sk") == "CS + DE + EN + PL + ostatní",
      f"above four languages the rest is summed up: {languages.combination_label('cs,de,en,pl,ru,sk')!r}")

print("--- converting paths ---")
maps = [{"from": "/media", "to": r"D:\media"}]
for got, want in [
    (probe.apply_path_mappings("/media/filmy/Duna.mkv", maps), r"D:\media\filmy\Duna.mkv"),
    (probe.apply_path_mappings("/jine/x.mkv", maps), "/jine/x.mkv"),
    (probe.apply_path_mappings("", maps), ""),
    (probe.apply_path_mappings(r"D:\media\a.mkv", []), r"D:\media\a.mkv"),
]:
    check(got == want, f"{got!r} == {want!r}")

print("--- charts with edge inputs ---")
try:
    charts.area_chart_multi([], "day", [{"key": "hours", "label": "hodiny"}])
    charts.area_chart_multi([{"day": "2026-08-12", "hours": 0}], "day",
                            [{"key": "hours", "label": "hodiny"}])
    charts.hbar_chart([], "label", "value")
    charts.hbar_chart([{"label": "x" * 80, "value": 0}], "label", "value")
    charts.stacked_bar([])
    charts.stacked_bar([{"label": "a", "value": 0}])
    charts.heatmap([[0.0] * 24 for _ in range(7)])
    charts.sparkline([])
    charts.sparkline([{"day": "2026-08-12", "hours": 0}])
    charts.sparkline([{"day": "2026-08-12", "hours": 2}, {"day": "2026-08-13", "hours": 5}])
    charts.legend([])
    for maximum in (0, 0.4, 1, 7, 99, 1234, 1e6):
        charts._nice_ticks(maximum)
    check(True, "empty and edge data did not break the charts")
except Exception as exc:  # noqa: BLE001
    check(False, f"charts: {type(exc).__name__}: {exc}")

# A count axis (queries, subtitles, requests) has whole ticks only: "1.5 queries" does not exist.
try:
    whole = {m: charts._nice_ticks(m, whole=True) for m in (0.4, 1, 2, 3, 7, 9, 10, 25, 99, 1234)}
    check(whole[2] == [0, 1, 2] and whole[0.4] == [0, 1] and all(float(t).is_integer() for ticks in whole.values() for t in ticks)
          and all(ticks[-1] >= m for m, ticks in whole.items()) and charts._nice_ticks(2) == [0, 0.5, 1, 1.5, 2],
          f"a count axis has whole ticks and still reaches past the maximum; the other axes do not change ({whole[2]}, {whole[10]})")
    svg = charts.area_chart_multi([{"day": "2026-11-15", "n": 2}, {"day": "2026-11-16", "n": 1}], "den",
                                  [{"key": "n", "label": "n", "slot": 1}], unit="", whole_ticks=True)
    check(">1,5<" not in svg and ">0,5<" not in svg and ">1<" in svg, "a count chart has no halves on the axis")
except Exception as exc:  # noqa: BLE001
    check(False, f"count axis: {type(exc).__name__}: {exc}")

# A colour must belong to the thing, not to the order: a segment with its own slot keeps it,
# even when it is second in the list.
svg = charts.stacked_bar([
    {"label": "en", "value": 30, "slot": 2},
    {"label": "cs", "value": 70, "slot": 1},
])
check("var(--series-2)" in svg and "var(--series-1)" in svg,
      "stacked_bar respects its own colour slot")

# Ten parts, 10 h each (100 h): the eight colour slots draw 10 % each - the share of the whole, as the table beside
# the bar says - and the last two go into one "Other" of 20 %, so the bar still makes 100 %.
svg = charts.stacked_bar([{"label": f"L{n}", "value": 10} for n in range(1, 11)])
tips = re.findall(r'data-tip="([^"]+)"', svg)
check(len(tips) == 9 and tips[0].endswith("(10 %)") and tips[-1].startswith(f"{_t('Other')}: ")
      and tips[-1].endswith("(20 %)"),
      f"stacked_bar shares are of the whole, the rest is one Other part ({tips[:1]} … {tips[-1:]})")
# Its legend names the same parts as the bar: the eight in colour, the grey "Other" (a legend is required - colour
# alone carries no identity) and not a part with nothing, which the bar does not draw. The legend used to cut the
# list at eight with no "Other", and a leading zero made it name another eight than the bar.
parts = [{"label": "L0", "value": 0}] + [{"label": f"L{n}", "value": 10} for n in range(1, 11)]
try:
    named = re.findall(r'background: ([^"]+)"></span>([^<]*)</li>', charts.stacked_legend(parts))
except AttributeError as exc:
    named = [("", f"{type(exc).__name__}: {exc}")]
drawn = re.findall(r'background: ([^;]+);[^>]*data-tip="([^:]+):', charts.stacked_bar(parts))
check(named == drawn and [n for _c, n in named] == [f"L{n}" for n in range(1, 9)] + [_t("Other")],
      f"the legend of a stacked bar names what the bar draws, its Other included ({named[-2:]} / {drawn[-2:]})")
# And the cards that draw a stacked bar use that legend, not the plain one that cuts at eight.
plain = sorted(path.name for path in (PROJECT / "jellyscope" / "templates").rglob("*.html")
               if "charts.stacked_bar(" in (text := path.read_text(encoding="utf-8")) and "charts.legend(" in text)
check(not plain, f"no template under a stacked bar draws the plain legend ({plain})")

svg = charts.hbar_chart([{"label": "<script>x</script>", "value": 5}], "label", "value")
check("<script>" not in svg and "&lt;script&gt;" in svg, "a dangerous name is escaped")

# A mini chart must carry the values just like a big chart - what looks like a chart
# is what a person tries to use as a chart.
spark = charts.sparkline(
    [{"day": "2026-08-11", "hours": 2}, {"day": "2026-08-12", "hours": 5}]
)
# The values come twice: in `data-tip-json` (the tooltip is put together from it -
# the heading and the rows in the series' colours) and in the plain `data-tip` as a fallback,
# in case the JSON could not be read.
check('data-tip="2026-08-12 · 5 h"' in spark, "the mini chart has values for hovering")
check('"heading":"2026-08-12"' in spark.replace("&quot;", '"'),
      "and the tooltip knows the date as its heading too")
check('aria-hidden' not in spark, "the mini chart is no longer marked as decoration")

# What a screen reader reads of a chart is in the page's language - it used to be Czech without diacritics for all.
from jellyscope import i18n  # noqa: E402

labels = {}
for code in ("cs", "en"):
    token = i18n.set_account_language(code)
    try:
        labels[code] = [re.search(r'aria-label="([^"]*)"', svg).group(1) for svg in (
            charts.sparkline([{"day": "2026-08-11", "hours": 2}, {"day": "2026-08-12", "hours": 5}]),
            charts.area_chart_multi([{"day": "2026-08-11", "a": 2, "b": 1}], "day",
                                    [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}]))]
    finally:
        i18n.reset_account_language(token)
check(labels == {"cs": ["Vývoj v čase, 2 dny", "Vývoj v čase, 2 série"],
                 "en": ["Trend over time, 2 days", "Trend over time, 2 series"]},
      f"the charts' label for a screen reader is translated ({labels})")

# No permanent point. All of them show only on hover - an end point in place
# looked like a point that "got stuck". The big chart does not have it either,
# and two shapes of the same chart are to behave the same.
outside_hover = spark.split('<g class="chart-hit"')[0]
check("<circle" not in outside_hover,
      "the mini chart has no permanent point at the end")
check(spark.count("<circle") == 2, f"the points are only in the hover groups ({spark.count('<circle')})")

big = charts.area_chart_multi(
    [{"day": "2026-08-11", "hours": 2}, {"day": "2026-08-12", "hours": 5}],
    "day", [{"key": "hours", "label": "Hodiny", "slot": 1}],
)
check("<circle" not in big.split('<g class="chart-hit"')[0],
      "and the big chart behaves the same")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
