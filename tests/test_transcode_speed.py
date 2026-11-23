# -*- coding: utf-8 -*-
r"""The transcode speed statistic on the Insights page (and in the API).

The average, the slowest and the fastest transcode, how many times the transcode could not keep up with
the video, and the same broken down by what transcodes (the processor, QSV...).

* The unit is one play and its average (the sum / the number of samples).
* The overall average is weighted by the transcode time, not a plain average of plays.
* Plays without a measurement (older than 1.7.3, an import) are not counted at all.

Run:
    .venv/bin/python tests/test_transcode_speed.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "rychlost.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from _package import sample_movie  # noqa: E402
from jellyscope import accounts, api, db, insights, web  # noqa: E402

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
when = (datetime.now(timezone.utc) - timedelta(days=2)).strftime(db.TIME_FORMAT)


def transcode(key: str, title: str, hw: str | None, fps: int, samples: int,
              video: int | None = 24, video_direct: int = 0) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name,"
            " item_type, started_at, last_seen_at, watched_seconds, is_active,"
            " play_method, transcode_hw, transcode_fps_sum, transcode_fps_samples,"
            " video_fps, transcode_video_direct) VALUES (?, 'u1', 'Jana', ?, ?, 'Movie',"
            " ?, ?, 600, 0, 'Transcode', ?, ?, ?, ?, ?)",
            (key, key, title, when, when, hw, fps * samples, samples, video, video_direct))
        conn.commit()


# The processor: 40 fps for long (100 samples), 18 fps briefly (10) - that one could not keep up.
transcode("a", "Duna", None, 40, 100)
transcode("b", "Amélie", None, 18, 10)
# QSV: 150 and 210 fps.
transcode("c", "Matrix", "qsv", 150, 50)
transcode("d", "Kolja", "qsv", 210, 40)
# An old play without a measurement - must not affect anything.
transcode("e", "Starý film", None, 0, 0)
# A transcode of the audio only with the "speed" of copying the video, stored still before
# the collector's fix - must not become the fastest transcode.
transcode("f", "Jen zvuk", None, 640, 30, video_direct=1)

print("--- the numbers ---")
r = insights.transcode_speeds(30)
check(r["playbacks"] == 4, f"four plays with a measurement, the old one does not count ({r['playbacks']})")
# Weighted: (40*100 + 18*10 + 150*50 + 210*40) / 200 = 20080 / 200 = 100,4
check(r["average"] == 100, f"the average is weighted by the transcode time ({r['average']}, a plain one would be 105)")
check(r["least"]["fps"] == 18 and r["least"]["label"] == "Amélie",
      f"the slowest: {r['least']['label']} {r['least']['fps']} fps")
check(r["most"]["fps"] == 210 and r["most"]["label"] == "Kolja",
      f"the fastest: {r['most']['label']} {r['most']['fps']} fps")
check(r["too_slow"] == 1 and round(r["too_slow_share"]) == 25,
      f"one of four could not keep up ({r['too_slow']}, {r['too_slow_share']:.0f} %)")

hw = {s["hw"]: s for s in r["by_hw"]}
check(set(hw) == {"", "qsv"}, f"two groups: the processor and QSV ({sorted(hw)})")
check(hw[""]["average"] == 38 and hw[""]["least"]["fps"] == 18 and hw[""]["most"]["fps"] == 40,
      f"the processor: average {hw['']['average']}, 18–40")
check(hw["qsv"]["average"] == 177 and hw["qsv"]["too_slow"] == 0,
      f"QSV: average {hw['qsv']['average']}, it always kept up")

print()
print("--- the Insights page ---")
k = TestClient(web.app)
k.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})
html = k.get("/video/insights?days=30").text
start = html.index('id="transcode-speed"')
card = html[start:html.index("</table>", start)]
for what in ("100 fps", "18 fps", "Amélie", "210 fps", "Kolja", "QSV", "procesor"):
    check(what in card, f"the card shows {what}")

print()
print("--- API ---")
token = api.create_token("test")["token"]
data = k.get("/api/v1/insights?days=30",
             headers={"Authorization": f"Bearer {token}"}).json()["transcode_speed"]
check(data["average_fps"] == 100 and data["slowest_fps"] == 18 and data["fastest_fps"] == 210,
      f"the API returns the same ({data['average_fps']}, {data['slowest_fps']}, {data['fastest_fps']})")
check({s["hardware"] for s in data["by_hardware"]} == {"cpu", "qsv"}, "and the breakdown by hardware")

print()
print("--- without data ---")
with db.connect() as conn:
    conn.execute("DELETE FROM playback")
    conn.commit()
check(insights.transcode_speeds(30)["playbacks"] == 0, "empty is empty, not an error")
check("Zatím žádný převod" in k.get("/video/insights?days=30").text, "and the page says so")
check(k.get("/api/v1/insights?days=30", headers={"Authorization": f"Bearer {token}"}
            ).json()["transcode_speed"] == {"playbacks": 0, "by_hardware": []},
      "the API too")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
