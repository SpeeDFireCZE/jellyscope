# -*- coding: utf-8 -*-
r"""Music's comparison: two periods side by side, like the comparison of Movies & series.

The user, 22. 11. 2026: Music laid out like Movies & series - besides the overview a comparison and the insights,
with the same rights. stats.music_comparison() gives the shape of stats.comparison() (groups of rows with the
difference, the genres with their shares like the languages, the charts of both periods), /music/compare draws it with
the same macros as /video/compare (templates/_compare.html).

Run:
    .venv/bin/python tests/test_music_comparison.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "music-comparison.db")
os.environ["SECRET_KEY"] = "music-comparison-test-key-long-enough"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "x"
os.environ["JELLYSCOPE_DEMO"] = "0"

from _apps import login  # noqa: E402
from _music import LISTENERS, fill_music  # noqa: E402

from jellyscope import access, accounts, db, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "en")
accounts.create("admin", "longpassword", is_admin=True)
accounts.create("reader", "longpassword")
reader_account = accounts.get_by_name("reader")
DEFAULTS = [area.key for area in access.AREAS if area.default]

# Period A = the last 7 days (hours ago 2, 3, 26), period B = the 7 days before them (9 and 10 days ago).
LISTENS = (
    ("u-jana", "First Light", 120, 2, "Finamp", "DirectPlay", None),
    ("u-jana", "Polar Drift", 60, 3, "Finamp", "DirectPlay", None),
    ("u-petr", "Neon Pier", 60, 26, "Jellyfin Web", "Transcode", "AudioCodecNotSupported"),
    ("u-eva", "Low Tide Drive", 30, 24 * 9, "Feishin", "DirectPlay", None),
    ("u-eva", "First Light", 90, 24 * 10, "Feishin", "DirectPlay", None),
)
tracks = fill_music(LISTENS)

print("--- the computation: the shape of the video comparison ---")
first = stats.period_of_days(7)
second = stats.previous_period(first)
c = stats.music_comparison(first, second)
groups = {group["heading"]: group for group in c["groups"]}
check(list(groups) == ["Listening", "How music is played", "Library", "Which genre was listened to"],
      f"the groups: listening, how it is played, the library, the genres ({list(groups)})")
rows = {row["label"]: row for row in groups["Listening"]["rows"]}
check(list(rows) == ["Listened", "Daily average", "Plays", "Listeners", "Artists", "Albums", "Tracks"],
      f"the rows of listening ({list(rows)})")
check(abs(rows["Listened"]["a"] - 240 / 3600) < 1e-9 and abs(rows["Listened"]["b"] - 120 / 3600) < 1e-9
      and abs(rows["Listened"]["difference"] - 120 / 3600) < 1e-9 and rows["Listened"]["form"] == "hours",
      f"listened: hours of A and B and the difference ({rows['Listened']})")
check((rows["Plays"]["a"], rows["Plays"]["b"]) == (3, 2) and (rows["Listeners"]["a"], rows["Listeners"]["b"]) == (2, 1)
      and (rows["Tracks"]["a"], rows["Tracks"]["b"]) == (3, 2),
      f"plays, listeners and tracks of both periods ({rows['Plays']['a']}/{rows['Plays']['b']},"
      f" {rows['Listeners']['a']}/{rows['Listeners']['b']}, {rows['Tracks']['a']}/{rows['Tracks']['b']})")
how = {row["label"]: row for row in groups["How music is played"]["rows"]}
check(abs(how["Transcode share"]["a"] - 25.0) < 1e-9 and how["Transcode share"]["b"] == 0
      and how["Transcode share"]["relative"] is None and "Sent over the network" in how,
      f"the transcode share in percent, without a relative change; the data sent ({how.get('Transcode share')})")
genres = groups["Which genre was listened to"]
check(genres.get("with_share") is True and genres["rows"] and all("share_a" in row and "share_b" in row
                                                                   for row in genres["rows"]),
      "the genres with their shares, like the languages of the video comparison")
check([row["label"] for row in c["a"]["top"]][:1] and all(row["detail_url"].startswith("/album/") for row in c["a"]["top"])
      and c["a"]["top"] == sorted(c["a"]["top"], key=lambda row: -row["hours"]) and len(c["a"]["top"]) <= 5,
      f"the most played albums of a period, by hours, with the album's page ({c['a']['top'][:2]})")
check({row["label"] for row in c["a"]["users"]} == {LISTENERS["u-jana"], LISTENERS["u-petr"]}
      and all(row["user_id"] for row in c["a"]["users"]),
      "the most active listeners of a period carry their id (anonymising knows them by it)")
check(c["has_data"] and c["same_length"] and not c["overlap"], "data, the same length, no overlap")
empty = stats.music_comparison(stats.period_between("2001-01-01", "2001-01-31"),
                               stats.period_between("2001-02-01", "2001-02-28"))
check(empty["has_data"] is False, "two periods without listening have nothing to compare")

print()
print("--- the page ---")
admin = login("admin")
page = admin.get("/music/compare?a_days=7&b_since=" + second.since[:10] + "&b_until=" + second.until[:10])
s = page.text
check(page.status_code == 200 and "<h2>Listening</h2>" in s and 'class="compare"' in s and "delta up" in s,
      f"the table of the groups with the difference ({page.status_code})")
check(all(name in s for name in (LISTENERS["u-jana"], LISTENERS["u-petr"])) and 'href="/album/' in s,
      "the administrator sees the listeners of both periods and the albums lead to their pages")
check('href="/music/compare"' in s and 'href="/music/insights"' in s,
      "Music's menu: the comparison between the overview and the insights")
menu = re.findall(r'href="(/music/[a-z]+)"', s)
check(menu.index("/music/overview") < menu.index("/music/compare") < menu.index("/music/insights"),
      f"in the order of Movies & series ({menu})")

accounts.save_permissions(reader_account["id"], access.pack_permissions(DEFAULTS + ["music_comparison"], False))
db.forget_settings()
reader = login("reader")
r = reader.get("/music/compare?a_days=7")
check(r.status_code == 200 and not any(name in r.text for name in LISTENERS.values()) and "/users/" not in r.text,
      "with the comparison but without “…who listened”: not a single listener's name")
accounts.save_permissions(reader_account["id"], access.pack_permissions(DEFAULTS, False))
db.forget_settings()
check(reader.get("/music/compare", follow_redirects=False).status_code == 403,
      "without the comparison's area: 403 (the comparison is not on by default, like the video one)")

fresh = login("admin")             # the comparison remembers its periods in the session: a fresh one
fresh.get("/music/overview?days=0")                    # the whole history chosen on the overview
whole = fresh.get("/music/compare")
check(whole.status_code == 200 and re.search(r'Period A<br>\s*<span class="muted">30 days</span>', whole.text),
      "the whole history has no window before it: period A is the default 30 days, like the video comparison")
nothing = admin.get("/music/compare?a_since=2001-01-01&a_until=2001-01-31&b_since=2001-02-01&b_until=2001-02-28")
check("There is nothing to compare in either period" in nothing.text and "class=\"compare\"" not in nothing.text,
      "no listening in either period: it says so instead of an empty table")

print()
print("--- the final review of 22. 11. 2026: an unknown album, a library without genres ---")
# A track the music sync does not know (yet) - its album has no name: the chart says so instead of an empty cell.
moment = (datetime.now(UTC) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name,"
        " play_method, transcode_reasons, media_runtime_ticks, started_at, last_seen_at, watched_seconds, paused_seconds,"
        " is_paused, is_active) VALUES ('unsynced-1', 'u-jana', ?, 'track-not-synced', 'Unsynced Song', 'Audio', 'Finamp',"
        " 'Telefon', 'DirectPlay', NULL, 400000000, ?, ?, 600, 0, 0, 0)", (LISTENERS["u-jana"], moment, moment))
    conn.execute("UPDATE music_track SET genres = ''")
    conn.commit()
today = datetime.now(UTC).date()
a_since, a_until = (today - timedelta(days=1)).isoformat(), today.isoformat()
b_since, b_until = (today - timedelta(days=12)).isoformat(), (today - timedelta(days=8)).isoformat()
untagged = stats.music_comparison(stats.period_between(a_since, a_until), stats.period_between(b_since, b_until))
check(untagged["has_data"] and "Which genre was listened to" not in [g["heading"] for g in untagged["groups"]],
      "no genre in the library: no empty genre table (like the languages of the video comparison)")
unknown = admin.get(f"/music/compare?a_since={a_since}&a_until={a_until}&b_since={b_since}&b_until={b_until}").text
top_a = unknown.split("Most played albums")[1].split("</table>")[0] if "Most played albums" in unknown else ""
check("unknown album" in top_a, "the album the sync does not know is “unknown album”, not an empty cell")

print()
print(f"DONE - failures: {failures}")
sys.exit(1 if failures else 0)
