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
print("--- bounds beyond the history are clamped ---")
# Any date strptime reads used to pass: 2000-9000 made every daily chart build a point for each of ~2.6
# million days (a minute of CPU and gigabytes per request, replayed from the session), 31.12.9999
# overflowed when the day after the end was added, a year below 1000 could not be read back
# (%Y does not pad it on Linux). Nothing is stored before OLDEST_HISTORY and nothing ahead of now,
# so the window is cut to OLDEST_HISTORY .. the end of next year.
THIS_YEAR = datetime.now(formatting.zone()).year
NEXT_YEARS_END = f"{THIS_YEAR + 2}-01-01 00:00:00"


def _period_or_error(since: str, until: str):
    try:
        return stats.period_between(since, until), ""
    except Exception as e:  # noqa: BLE001
        return None, repr(e)


huge, error = _period_or_error("2000-01-01", "9000-01-01")
check(huge is not None and huge.until_local == NEXT_YEARS_END and huge.days <= 30 * 366,
      f"a 7000-year window ends with next year ({huge.until_local if huge else error},"
      f" {huge.days if huge else '-'} days)")
forever, error = _period_or_error("2026-11-01", "9999-12-31")
check(forever is not None and forever.until_local == NEXT_YEARS_END,
      f"until 31.12.9999 is no error ({forever.until_local if forever else error})")
ancient, error = _period_or_error("0001-01-02", "2026-01-01")
check(ancient is not None and ancient.since_local == stats.OLDEST_HISTORY,
      f"a start in year 1 begins with the oldest history ({ancient.since_local if ancient else error})")
check(_period_or_error("0001-01-01", "0001-01-02") == (None, ""),
      "a window entirely before the history is no period")
check(_period_or_error("9000-01-01", "9000-01-02") == (None, ""),
      "nor one entirely after next year")
for since, until in (("2026-11-01", "9999-12-31"), ("0001-01-02", "2026-01-01"),
                     ("0999-01-01", "2026-11-24")):
    try:
        clamped = stats.period_between(since, until)
        stats.previous_period(clamped)
        stats.chart_days([{"day": "2026-01-05", "plays": 1}], clamped, ("plays",))
        stats.daily_activity_split(clamped)
        error = ""
    except Exception as e:  # noqa: BLE001
        error = repr(e)
    check(error == "", f"{since} .. {until}: the previous period and the daily charts work {error}")
# Ordinary periods stay as they were, byte for byte - the full current and next year too.
whole_years = stats.period_between(f"{THIS_YEAR}-01-01", f"{THIS_YEAR + 1}-12-31")
check(whole_years is not None and whole_years.since_local == f"{THIS_YEAR}-01-01 00:00:00"
      and whole_years.until_local == NEXT_YEARS_END and whole_years.whole_day,
      f"this year and the next one are not cut ({whole_years})")
first_year = stats.period_between("2000-01-01", "2000-12-31")
check(first_year is not None and first_year.since_local == "2000-01-01 00:00:00"
      and first_year.until_local == "2001-01-01 00:00:00" and first_year.days == 366,
      f"nor is the first year of the history ({first_year})")
exact = stats.period_between("2026-10-01 10:00", "2026-10-02 11:30")
check(exact is not None and exact.since_local == "2026-10-01 10:00:00"
      and exact.until_local == "2026-10-02 11:30:00" and not exact.whole_day,
      f"nor an exact span from a chart ({exact})")

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
# The owner's decision (25. 11. 2026): the previous period of "the last N days" is the same span shifted back by N
# days in the app's zone - from the midnight N days before the first day to this clock time N days ago.
previous = stats.previous_period(30)
month = stats._as_period(30)
local_now = datetime.now(formatting.zone()).replace(tzinfo=None)
shifted_start = datetime.strptime(month.since_local, db.TIME_FORMAT) - timedelta(days=30)
previous_end = datetime.strptime(previous.until_local, db.TIME_FORMAT)
check(previous.since_local == shifted_start.strftime(db.TIME_FORMAT),
      f"the previous window starts at the midnight 30 days before the chosen one ({previous.since_local})")
check(abs((previous_end - (local_now - timedelta(days=30))).total_seconds()) < 5 and previous.until <= month.since,
      f"and ends at this clock time 30 days ago, before the chosen one starts ({previous.until_local})")
check(previous.days == 30, "and is of the same number of days")
check(stats.previous_period(week).until == week.since,
      "a custom period's previous window ends where the chosen one starts")

print()
print("--- 'the last N days' are whole calendar days, the days the chart by day draws ---")
# The owner's decision (25. 11. 2026): the numbers count by whole days. They used to count 7 x 24 hours back from
# now, while the chart by day draws today and the six days before it: a play in between (the rest of the eighth day
# back) was in the headline and in no bar, so the bars added up to less than the number above them.
zone = formatting.zone()
today = datetime.now(zone).date()
first_midnight = (datetime.combine(today - timedelta(days=6), datetime.min.time())
                  .replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None))
rolling_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
week_since = stats.period_of_days(7).since
check(week_since == first_midnight.strftime(db.TIME_FORMAT),
      f"the week starts at the local midnight six days back ({week_since}, midnight {first_midnight})")
headline_before = int(stats.overview(7)["watched_seconds"])
previous_before = int(stats.previous_overview(7)["watched_seconds"] or 0)
if rolling_start < first_midnight:
    # A play in the gap: less than 7 x 24 hours ago, but on the day before the seven.
    add_play(rolling_start + (first_midnight - rolling_start) / 2, 7200, "qs3-gap")
add_play(first_midnight + timedelta(minutes=1), 3600, "qs3-first-day")
headline = int(stats.overview(7)["watched_seconds"])
check(headline == headline_before + 3600,
      f"the headline counts the first of the seven days, not the day before them ({headline - headline_before} s)")
bars = sum(row["hours"] for row in stats.daily_activity_split(7))
check(abs(bars - headline / 3600) < 0.05,
      f"the chart's bars add up to the headline ({bars:.2f} h, headline {headline / 3600:.2f} h)")
if rolling_start < first_midnight:
    previous_after = int(stats.previous_overview(7)["watched_seconds"] or 0)
    check(previous_after == previous_before,
          f"the rest of the day before the seven belongs to neither week: the previous one ends at this clock time "
          f"({previous_after - previous_before} s)")
arr_chart = stats.chart_days([{"day": today.isoformat(), "plays": 1}], 7, ("plays",))
check([p["day"] for p in arr_chart][:1] == [(today - timedelta(days=6)).isoformat()] and len(arr_chart) == 7,
      f"the *Arr and Seerr charts draw the same seven days ({arr_chart[0]['day']}, {len(arr_chart)} points)")
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE session_key LIKE 'qs3-%'")

print()
print("--- a day is not always 24 hours: the bounds stay on the local midnight across a change of the clocks ---")
# Prague left summer time on 25 October 2026. The week before 3 November starts on 28 October at midnight (CET) and
# the previous one on 21 October at midnight (CEST) - one hour more apart than 7 x 24 hours.


class _Clock(datetime):
    current = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current.astimezone(tz) if tz is not None else cls.current.astimezone().replace(tzinfo=None)


db.set_setting("app_timezone", "Europe/Prague")
real_datetime = stats.periods.datetime
stats.periods.datetime = _Clock
try:
    clock_week, clock_before = stats.period_of_days(7), stats.previous_period(7)
finally:
    stats.periods.datetime = real_datetime
    db.set_setting("app_timezone", "")
check((clock_week.since, clock_week.since_local) == ("2026-10-27 23:00:00", "2026-10-28 00:00:00"),
      f"the week starts at the midnight of 28 October ({clock_week.since} UTC, {clock_week.since_local})")
check((clock_before.since, clock_before.since_local) == ("2026-10-20 22:00:00", "2026-10-21 00:00:00"),
      f"the previous week at the midnight of 21 October, in summer time ({clock_before.since} UTC,"
      f" {clock_before.since_local})")
check((clock_before.until, clock_before.until_local) == ("2026-10-27 10:00:00", "2026-10-27 11:00:00"),
      f"and it ends at 11:00 on 27 October, the clock time of now a week ago ({clock_before.until} UTC,"
      f" {clock_before.until_local})")
stats.periods.datetime = _Clock
db.set_setting("app_timezone", "Europe/Prague")
try:
    clock_fortnight = stats.previous_period(14)
finally:
    stats.periods.datetime = real_datetime
    db.set_setting("app_timezone", "")
check((clock_fortnight.since, clock_fortnight.until, clock_fortnight.until_local)
      == ("2026-10-06 22:00:00", "2026-10-20 09:00:00", "2026-10-20 11:00:00"),
      f"across the change of the clocks the end keeps the clock time, not 14 x 24 hours ({clock_fortnight.until} UTC,"
      f" {clock_fortnight.until_local})")

print()
print("--- at 10:00 the same plays in the same hours of both spans give no change ---")
# The previous period used to be N whole days, while "the last N days" hold only the part of today that has passed:
# every morning looked like a drop. A play every day at 08:00 and every evening at 20:00 - the same in both weeks.
from jellyscope import cards  # noqa: E402


class _Morning(_Clock):
    current = datetime(2030, 2, 20, 9, 0, tzinfo=timezone.utc)      # 10:00 in Prague


db.set_setting("app_timezone", "Europe/Prague")
real_overview_datetime = stats.overview_page.datetime
stats.periods.datetime = stats.overview_page.datetime = _Morning
try:
    for ago in range(14):
        morning = datetime(2030, 2, 20, 7, 0) - timedelta(days=ago)          # 08:00 CET
        add_play(morning, 3600, f"qs4-morning-{ago}")
        if ago:
            add_play(morning + timedelta(hours=12), 3600, f"qs4-evening-{ago}")    # 20:00 CET, today's not yet
    summary = cards.period_summary(7)
    side_by_side = stats.comparison(7, stats.previous_period(7))
    length_now, length_before = side_by_side["a"]["days"] * 86400, side_by_side["b"]["days"] * 86400
    bars_now = sum(row["hours"] for row in stats.daily_activity_split(7))
finally:
    stats.periods.datetime, stats.overview_page.datetime = real_datetime, real_overview_datetime
    db.set_setting("app_timezone", "")
    with db.connect() as conn:
        conn.execute("DELETE FROM playback WHERE session_key LIKE 'qs4-%'")
check(summary["overview"]["watched_seconds"] == 13 * 3600 and summary["deltas"]["watched"] == 0
      and summary["deltas"]["plays"] == 0,
      f"13 hours in both, no change of the hours or the plays ({summary['overview']['watched_seconds'] / 3600:g} h,"
      f" {summary['deltas']})")
check(abs(length_now - (6 * 24 + 10) * 3600) < 1 and abs(length_before - length_now) < 1 and side_by_side["same_length"],
      f"Comparison: both are six days and ten hours long, the time that has really passed, so the daily averages"
      f" divide by it ({length_now / 3600:g} h, {length_before / 3600:g} h)")
check(abs(bars_now - 13) < 0.01, f"the headline still equals the sum of the daily chart's bars ({bars_now:g} h)")

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

    # Edge-of-calendar dates written into the form: "until 31.12.9999" meant "until forever" and gave
    # a bare 500 on every page with statistics, a start in year 1 the same.
    def status(address: str) -> str:
        try:
            return str(client.get(address).status_code)
        except Exception as e:  # noqa: BLE001
            return repr(e)

    for path in ("/video/overview", "/video/insights", "/network", "/users", "/video/languages"):
        for query in ("since=1.1.2026&until=31.12.9999", "since=0001-01-02&until=2026-01-01"):
            answer = status(f"{path}?{query}")
            check(answer == "200", f"{path}?{query} -> {answer}")
    # The remembered period is the clamped one, so the next page does not replay the huge window.
    status("/?since=1.1.2026&until=31.12.9999")
    check(f"31.12.{THIS_YEAR + 1}" in chosen_chip(client.get("/users").text),
          f"the switch shows the clamped end ({chosen_chip(client.get('/users').text)})")
    client.get("/?days=30")

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
    answer = status("/video/compare?a_since=1.1.2026&a_until=31.12.9999")
    check(answer == "200", f"/video/compare with until 31.12.9999 -> {answer}")

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
