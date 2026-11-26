# -*- coding: utf-8 -*-
r"""Network statistics: how much flowed, when the peak was, from where.

It is computed from `playback.bitrate` - the flow the collector has been storing for every play
for a long time. It needs no new collecting, only another question over
data that is already there.

The most interesting piece is the concurrent flow: it is not computed by sampling, but by
walking through events (every play adds its bitrate at the start
and takes it away at the end). The test therefore builds a situation where two plays
overlap only for a while - and checks that the peak matches exactly that
overlap, not the sum of everything.

Run:
    .venv/bin/python tests/test_network.py
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
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sit.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import accounts, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
accounts.create("admin", "longpassword", is_admin=True)


def add_play(name: str, since: str, until: str, mbit: float, seconds: int,
             address: str | None, method: str = "DirectPlay",
             player: str = "Jellyfin Web") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id,"
            " item_name, item_type, started_at, last_seen_at, ended_at,"
            " watched_seconds, bitrate, remote_address, play_method, client,"
            " device_name) VALUES (?, ?, ?, 'i1', 'Film', 'Movie',"
            " datetime('now', ?), datetime('now', ?), datetime('now', ?),"
            " ?, ?, ?, ?, ?, 'Chrome')",
            # Every viewer their own id: the same id = the same (renamed) person.
            (f"s-{name}-{since}", f"u-{name.lower()}", name, since, until, until, seconds,
             int(mbit * 1e6), address, method, player),
        )


# Two plays, each 10 Mbit/s, overlapping only for an hour in the middle.
add_play("Jana", "-4 hours", "-2 hours", 10, 7200, "192.168.1.5")
add_play("Petr", "-3 hours", "-1 hours", 10, 7200, "192.168.1.6")
# The third is from the internet and transcoded - a small flow, but another category.
add_play("Eva", "-9 hours", "-8 hours", 4, 3600, "89.24.10.7", "Transcode",
         "Jellyfin Android TV")
# The fourth has neither an address (from an import) nor a bitrate - it must not count in the sums.
add_play("Tomas", "-20 hours", "-19 hours", 0, 3600, None)


print("--- the summary ---")
summary = stats.bandwidth_summary(2)
check(summary["playbacks"] == 3,
      f"it counts only plays whose bitrate we know ({summary['playbacks']})")
# The volume: (10 + 10) Mbit/s * 7200 s + 4 * 3600, all / 8 to bytes.
expected = (10e6 * 7200 + 10e6 * 7200 + 4e6 * 3600) / 8
check(abs(summary["byte_count"] - expected) < 1000,
      f"the volume of data matches ({summary['byte_count']} vs {int(expected)})")
check(abs(summary["transcode_share"] - 9.1) < 0.5,
      f"the share of the transcoded ({summary['transcode_share']} %)")


print()
print("--- the concurrent flow ---")
timeline = stats.bandwidth_timeline(2, points=200)
check(bool(timeline), "the curve is not empty")
peak = max(b["mbit"] for b in timeline)
# Here is the core: in the overlap both plays flow at once, so 20 Mbit/s.
# If the maximum of the single plays were taken, it would come out as 10.
check(abs(peak - 20.0) < 0.5, f"the peak is the sum in the overlap ({peak} Mbit/s)")
check(abs(summary["peak_mbit"] - peak) < 0.5,
      "and the summary reports the same number")
check(all("label" in b for b in timeline), "every point has a label for the axis")


print()
print("--- who and from where ---")
by_user = stats.bandwidth_by(2, "user_name")
check([p["label"] for p in by_user][:2] == ["Jana", "Petr"]
      or [p["label"] for p in by_user][:2] == ["Petr", "Jana"],
      f"those two downloaded the most ({[p['label'] for p in by_user]})")
check(all("gb" in p for p in by_user), "the chart gets gigabytes, not bare bytes")

locations = stats.watching_locations(2)
check(locations["groups"]["at_home"]["plays"] == 2, "two plays from the home network")
check(locations["groups"]["internet"]["plays"] == 1, "one from the internet")
check(locations["groups"]["unknown_origin"]["plays"] == 1, "one without an address (an import)")
check(any(not a["home"] for a in locations["urls"]),
      "the public address is marked as the internet in the list")

check(stats.is_home("192.168.1.5") and stats.is_home("10.0.0.1")
      and stats.is_home("172.20.0.9"), "private ranges are recognised")
check(not stats.is_home("89.24.10.7") and not stats.is_home(""),
      "neither a public address nor an empty value is home")


print()
print("--- the page ---")
from fastapi.testclient import TestClient  # noqa: E402
from jellyscope.web import app  # noqa: E402

with TestClient(app) as client:
    client.post("/login", data={"username": "admin", "password": "longpassword"},
                follow_redirects=False)
    r = client.get("/network")
    check(r.status_code == 200, f"/network is drawn ({r.status_code})")
    check("Mbit/s" in r.text, "and the flow is on it")
    check("89.24.10.7" in r.text, "and the table of addresses")
    # The number looks like a measurement, but it is not one - the page must say so.
    check("ne měření drátu" in r.text or "not a measurement of the wire" in r.text,
          "the page admits it is an estimate")
    check(client.get("/network?days=7").status_code == 200, "and the period can be switched")

    # The live card refreshes with the period of its page, not with the one another tab put into the session:
    # the Network page for a week left open, a year chosen in another tab - the card switched to a year.
    import re  # noqa: E402

    def live_note(html: str) -> str:
        card = html.split('class="card live-card"')[1]
        return re.sub(r"\s+", " ", card.split('<p class="card-note">')[1].split("</p>")[0]).strip()

    week = client.get("/network?days=7").text
    partial = re.search(r'id="network-live" data-refresh="[^"]*" data-partial="([^"]+)"', week).group(1).replace("&amp;", "&")
    year_note = live_note(client.get("/network?days=365").text)
    refreshed = live_note(client.get(partial).text)
    check(live_note(week) != year_note and refreshed == live_note(week),
          f"the live card refreshes with its page's week, not the year of another tab ({partial}: {refreshed!r},"
          f" a week {live_note(week)!r}, a year {year_note!r})")

print()
print("--- the peak's time over a year is the moment of the peak, not the middle of a chart step ---")
# Over a year one step of a 600-point curve is about 14 hours; the time under "Peak" must still be the minute the
# two streams met.
from datetime import UTC, datetime, timedelta  # noqa: E402

from jellyscope import formatting  # noqa: E402

met = (datetime.now(UTC) - timedelta(days=150)).replace(hour=13, minute=0, second=0, microsecond=0)
# A small play at the start of the year stretches the curve over the whole year.
add_play("Ida", "-360 days", "-360 days", 5, 600, None)
for name, offset in (("Ota", 0), ("Ula", 1800)):
    start = met - timedelta(seconds=offset)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, started_at,"
            " last_seen_at, ended_at, watched_seconds, bitrate, play_method) VALUES (?, ?, ?, 'i1', 'Film', 'Movie',"
            " ?, ?, ?, 3600, 40000000, 'DirectPlay')",
            (f"s-{name}", f"u-{name.lower()}", name, start.strftime(db.TIME_FORMAT),
             (start + timedelta(hours=1)).strftime(db.TIME_FORMAT), (start + timedelta(hours=1)).strftime(db.TIME_FORMAT)))
year = stats.bandwidth_summary(365)
expected_at = met.astimezone(formatting.zone()).strftime("%d.%m. %H:%M")
check(year["peak_mbit"] == 80.0, f"the peak over a year is both streams at once ({year['peak_mbit']} Mbit/s)")
check(year["peak_at"] == expected_at, f"at the minute the second one started ({year['peak_at']} vs {expected_at})")

print()
print("--- the Network page reads the period's plays once ---")
# The figures and the peaks by day each fetched the period's plays and parsed every time with strptime - over a
# year of a busy server seconds of the page's time. One fetch and one parse are shared by the page's cards now.
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402

from jellyscope import cards  # noqa: E402
from jellyscope.stats import network as network_module  # noqa: E402
from jellyscope.web import network as network_page  # noqa: E402

expected_summary, expected_peaks = stats.bandwidth_summary(365), stats.bandwidth_daily_peaks(365)
fetches: list[int] = []
real_rows = network_module._network_rows


def counting_rows(days):
    fetches.append(1)
    return real_rows(days)


restore = substitute(stats, "_network_rows", counting_rows)
try:
    # What the page computes: its cards in one data_for (web/network.py).
    page = cards.data_for([cards.BY_KEY[key] for key in network_page.NETWORK_CARDS], 365)
finally:
    restore()
check(len(fetches) == 1, f"one fetch of the period's plays for the whole page ({len(fetches)})")
check(page["network_summary"] == expected_summary and page["daily_peaks"] == expected_peaks,
      "and the figures and the peaks by day are the same as computed alone")
check(network_module._seconds("2026-03-01 10:00:00") == network_module._seconds("2026-03-01T10:00:00.1234567Z")
      == datetime(2026, 3, 1, 10, tzinfo=UTC).timestamp() and network_module._seconds("nonsense") is None,
      "a time from the database is read in UTC, with a T and fractions too; nonsense is None")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
