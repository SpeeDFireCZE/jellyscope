# -*- coding: utf-8 -*-
r"""A custom period: statistics for a range a person chooses themselves.

So far it only asked "since when" (`datetime('now', '-30 days')`), so the window
always ended now. "The whole of last December" could not be expressed that way -
and yet it is one of the first questions a person looks at
statistics for.

Every query therefore got an upper bound too. For "the last N days" it is open
(see `stats.END_OF_TIME`): the time is stored rounded to seconds, so
a play written in the same second in which we ask would not fit into the window
otherwise - and exactly that is the record a person is looking for.

Run:
    .venv/bin/python tests/test_custom_period.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "obdobi.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from _package import sample_movie  # noqa: E402
from jellyscope import accounts, db, formatting, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
sample_movie()  # the Movies & series section exists only with a movie (navigation.has_video)
accounts.create("admin", "longpassword", is_admin=True)

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def add_play(when: datetime, seconds: int, key: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO playback (session_key, user_id, user_name, item_id,
                                     item_name, item_type, started_at,
                                     last_seen_at, watched_seconds, is_active)
               VALUES (?, 'u1', 'Petr', 'film-1', 'Duna', 'Movie', ?, ?, ?, 0)""",
            (key, when.strftime(db.TIME_FORMAT), when.strftime(db.TIME_FORMAT), seconds),
        )


# Three plays on different days - by the window a different three is to be counted.
add_play(NOW, 3600, "dnes")
add_play(NOW - timedelta(days=10), 7200, "pred-10-dny")
add_play(NOW - timedelta(days=100), 1800, "pred-100-dny")

print("--- the last N days ---")
check(stats.overview(7)["plays"] == 1, "for a week only today is seen")
check(stats.overview(30)["plays"] == 2, "for a month two records")
check(stats.overview(365)["plays"] == 3, "for a year all three")

print()
print("--- a record from the second running right now must not drop out ---")
# Because of this the upper bound of a relative period is open: the time is
# stored to seconds, so "now" in the database and "now" in the query are the same
# string - and with a strict upper bound it would not fit into the window.
add_play(datetime.now(timezone.utc).replace(tzinfo=None), 60, "prave-ted")
check(stats.overview(7)["plays"] == 2, "a play written just now is seen at once")

print()
print("--- a custom range ---")
# The record's day in the local zone, not in UTC: the range is counted in local days
# and between the local midnight and the midnight in UTC it would be the day next to it.
ten_days_ago = (NOW - timedelta(days=10)).replace(tzinfo=timezone.utc).astimezone(formatting.zone()).date().isoformat()
period = stats.period_between(ten_days_ago, ten_days_ago)
check(period is not None, "a range of one day makes sense")
check(period.days == 1, f"and it is one day long ({period.days})")
check(stats.overview(period)["plays"] == 1,
      "that day has exactly one record")
check(int(stats.overview(period)["watched_seconds"]) == 7200,
      "and the watched time matches too")

print()
print("--- the end of the range includes the whole day ---")
# The midnight of the following day goes into the query, otherwise everything after 00:00
# would drop out of the last day - so practically the whole day.
#
# That midnight is LOCAL, so in the application's zone. In UTC (and that is how the history
# is stored) it comes to another moment - in summer to 22:00 of the previous day.
# Formerly the midnight in UTC was taken and "20 August" then really
# meant 20 August from two in the morning to two in the morning of the next day.
local_end = datetime.strptime(period.until_local, db.TIME_FORMAT)
check(local_end.date() == date.fromisoformat(ten_days_ago) + timedelta(days=1)
      and local_end.time().isoformat() == "00:00:00",
      f"the upper bound is the local midnight of the next day ({period.until_local})")
check(datetime.strptime(period.until, db.TIME_FORMAT)
      .replace(tzinfo=timezone.utc).astimezone(formatting.zone())
      .strftime(db.TIME_FORMAT) == period.until_local,
      f"and in the query it is the same moment converted to UTC ({period.until})")
check(period.whole_day, "a period given in days is whole-day")

print()
print("--- what makes no sense is not used ---")
check(stats.period_between("2026-08-10", "2026-08-01") is None,
      "an end before the start is refused")
check(stats.period_between("nesmysl", "2026-08-01") is None, "text too")
check(stats.period_between(None, None) is None, "and empty")

print()
print("--- the chart by day has as many rows as the period has days ---")
check(len(stats.daily_activity_split(30)) == 30, "30 days = 30 rows")
check(len(stats.daily_activity_split(7)) == 7, "7 days = 7 rows")
week = stats.period_between((NOW - timedelta(days=6)).date().isoformat(),
                            NOW.date().isoformat())
check(len(stats.daily_activity_split(week)) == 7,
      f"a custom week seven too ({len(stats.daily_activity_split(week))})")

print()
print("--- comparison with the previous period ---")
previous = stats.previous_period(30)
check(previous.until == stats._as_period(30).since,
      "the previous window ends where the chosen one starts")
check(previous.days == 30, "and is of the same length")

print()
print("--- the whole history ---")
# From the first play until now. The end is open as with "the last N days",
# so a remembered choice includes what comes tomorrow too.
whole = stats.whole_history_period()
check(whole.whole_history and whole.until == stats.END_OF_TIME,
      "the whole history: a period with an open end")
check(stats.overview(whole)["plays"] == 4,
      f"it includes all the plays ({stats.overview(whole)['plays']})")
check(whole.since <= (NOW - timedelta(days=100)).strftime(db.TIME_FORMAT),
      f"it starts at the latest with the first play ({whole.since})")
check(len(stats.daily_activity_split(whole)) == whole.days and whole.days >= 100,
      f"the chart by day goes from the first day ({whole.days} days)")
check(stats.overview(stats.previous_period(whole))["plays"] == 0,
      "before the first play there is nothing - the comparison has nothing to compare with")

print()
print("--- the whole history: a nonsensical date from an import ---")
# An import from Playback Reporting can bring DateTime.MinValue (0001-01-01).
# If the whole history started from it, the conversion to the zone would overflow and
# the remembered choice would bring down every page with statistics. And a play
# with zero seconds is not a play - it does not determine the start.
db.set_setting("app_timezone", "Europe/Prague")
add_play(datetime(1, 1, 1), 600, "nesmysl-min")
add_play(NOW - timedelta(days=400), 0, "nula-vterin")
try:
    whole2, error = stats.whole_history_period(), ""
except Exception as e:  # noqa: BLE001
    whole2, error = None, repr(e)
check(whole2 is not None, f"a nonsensical date brings nothing down {error}")
if whole2 is not None:
    check(whole2.since >= stats.OLDEST_HISTORY and abs(whole2.days - whole.days) <= 1,
          f"it starts with the first real play ({whole2.since}, {whole2.days} days)")
    check(stats.can_compare(whole2) is False, "and the comparison with the previous window does not fall over")
db.set_setting("app_timezone", "")

print()
print("--- the switch and the dialog on the page ---")
from fastapi.testclient import TestClient  # noqa: E402

from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    # The date can be entered the Czech way and in the form from the address - the same parser as
    # in the history filter.
    czech = client.get("/?since=1.8.2026&until=24.8.2026")
    check(czech.status_code == 200 and "1.8.2026" in czech.text,
          "a date written the Czech way passes")

    page = client.get(f"/?since={ten_days_ago}&until={ten_days_ago}").text
    check('id="modal-period"' in page, "the dialog for a custom period is on the page")
    check(page.count("data-calendar") >= 2,
          "both fields have a suggester with a calendar")
    # The month arrows redraw the panel's content, so before the click could
    # bubble up to the "clicked outside" handler, the button is no longer in the document
    # and `closest` on a detached node returns null. The calendar closed because of it
    # and the months could not be switched at all - hence the capture phase.
    # Since the rebuild for CSP the scripts are without inline code, in static/js/app.js.
    base = ((PROJECT / "jellyscope" / "templates" / "base.html").read_text(encoding="utf-8")
            + (PROJECT / "jellyscope" / "static" / "js" / "app.js").read_text(encoding="utf-8"))
    handler = base[base.index("input[data-calendar]"):]
    handler = handler[:handler.index("});") + 3]
    check(handler.rstrip().endswith("}, true);")
          or "}, true);" in base[base.index("var input = event.target.closest"):
                                 base.index("var input = event.target.closest") + 600],
          "a click outside the calendar is watched in the capture phase")
    check(page.count("data-period-since=") == 3,
          "and there are three quick choices (this month, last month, this year)")
    # The date is written the Czech way - the native <input type="date"> looks
    # different in every browser and sticks out in the middle of the dialog.
    # The fields are text, not the native <input type="date"> - that looks
    # different in every browser. (Looking for `type="date"` alone in the page
    # does not work: that sentence is in a comment too that explains why.)
    check('<input type="text" id="period-since"' in page
          and '<input type="text" id="period-until"' in page,
          "both date fields are text")
    check(ten_days_ago in page, "and the switch shows the chosen date")

    # It is remembered the same way as the number of days, so it holds on further
    # pages too - otherwise the window would go back to 30 days at every move.
    # The Czech way, the same as in the fields - so it is one date, not two
    # different writings of the same.
    d = datetime.strptime(ten_days_ago, "%Y-%m-%d")
    ten_days_ago_czech = f"{d.day}.{d.month}.{d.year}"

    next_page = client.get("/video/languages").text
    check(ten_days_ago_czech in next_page, "the custom period holds after moving elsewhere too")

    # Watch out for a naive search for the date in the page: the same day appears
    # in the table under the chart too. So we ask the switch.
    import re

    def chosen_chip(html: str) -> str:
        m = re.search(r'class="chip active"[^>]*>\s*([^<]+?)\s*<', html)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    check(ten_days_ago_czech in chosen_chip(page),
          f"the custom period is chosen in the switch ({chosen_chip(page)})")
    back = client.get("/?days=30").text
    check(ten_days_ago_czech not in chosen_chip(back),
          f"and a click on 30 days cancels it again ({chosen_chip(back)})")

    for path in ("/video/insights", "/network", "/users", "/video/languages"):
        response = client.get(f"{path}?since={ten_days_ago}&until={ten_days_ago}")
        check(response.status_code == 200, f"{path} with a custom period")

    # The whole history: a button in the custom period's dialog, not another choice
    # in the switch.
    check('href="/video/overview?days=0"' in page, "the custom period's dialog has Celá historie")
    everything = client.get("/?days=0").text
    check(chosen_chip(everything) == "Celá historie",
          f"after the click the switch shows it ({chosen_chip(everything)})")
    check(chosen_chip(client.get("/users").text) == "Celá historie",
          "and it holds on further pages too, like every period")
    for path in ("/", "/video/insights", "/network", "/users", "/video/languages", "/library"):
        check(client.get(f"{path}?days=0").status_code == 200, f"{path} for the whole history")
    # The links in the dialog keep the page's position the same as the choices in the switch -
    # otherwise a click on "Celá historie" would jump up.
    dialog = page[page.index('id="modal-period"'):]
    for address in ("/video/overview?days=0", "/video/overview?days=30"):
        tag = re.search(r'<a [^>]*href="' + re.escape(address) + r'"[^>]*>', dialog)
        check(tag is not None and "data-keep-scroll" in tag.group(0),
              f"the link {address} in the dialog keeps the page's position")

    # Compare: the whole history has no window of the same length before it - before
    # the first play there is nothing. The default pair is then 30 days against 30
    # previous ones, not everything against emptiness.
    client.get("/?days=0")
    compare_page = client.get("/video/compare").text
    check('name="a_days" value="30"' in compare_page,
          "Compare with the whole history starts at 30 days")

    # Languages: the choice of the preferred language sent a hidden `days` - with a period
    # it was a dump of the object and the form ended with an error 422.
    with db.connect() as conn:
        conn.execute("INSERT INTO items (id, name, type, audio_languages)"
                     " VALUES ('film-1', 'Duna', 'Movie', 'ces')")
        conn.commit()
    languages_page = client.get("/video/languages").text
    check('name="code"' in languages_page, "the Languages have the choice of the preferred language")
    check("Period(" not in languages_page, "and it carries no dump of the period object")
    sent = client.post("/video/languages/preferred", data={"code": "ces"},
                       follow_redirects=False)
    check(sent.status_code == 303, f"saving passes ({sent.status_code})")
    for_match = re.search(r"celkem\s+[^<]*?\s+za\s+([^<]*?)\s*<", languages_page)
    check(for_match is None or for_match.group(1).strip() != "",
          f"“celkem … za” has a period after it ({for_match.group(1).strip() if for_match else 'no block'})")
    # The same in the card of space nobody uses: `day_labels` does not know periods.
    insights = client.get("/video/insights?days=0").text
    nothing = re.search(r"nic nepřehráno za\s*([^<&]*?)\s*(&middot;|<)", insights)
    check(nothing is not None and nothing.group(1).strip() != "",
          f"“nic nepřehráno za” has a period after it ({nothing.group(1).strip() if nothing else 'no card'})")

    check(chosen_chip(client.get("/?days=30").text) == "30 dnů",
          "a click on 30 days cancels it again")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
