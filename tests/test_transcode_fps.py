# -*- coding: utf-8 -*-
r"""The transcode speed (fps) in the bubble by the transcode mark.

Jellyfin sends in `TranscodingInfo.Framerate` how many frames per second
the transcode manages. The number by itself says nothing - 30 fps is enough for a movie (24), for
sport (50) not - that is why the video's frame rate is stored too and the bubble says
when the transcode cannot keep up.

For a running play the state now holds, for the history the average over the whole
play. A pause does not count into the average: ffmpeg stands still during it and the average
would claim the server could not keep up.

The bubble is in four places: Now playing, History, the title's detail
and the library's detail.

Run:
    .venv/bin/python tests/test_transcode_fps.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "fps.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, collector, db, stats, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)
accounts.create("admin", "longpassword", is_admin=True)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('lib', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, video_codec, audio_codec, is_missing)"
                 " VALUES ('film', 'Duna', 'Movie', 'lib', 'hevc', 'eac3', 0)")
    conn.commit()

print("--- reading the number ---")
check(collector._fps(23.976) == 24, "23,976 is 24")
check(collector._fps("96.4") == 96, "text from JSON is read")
for nonsense in (None, 0, "", "abc", -5, 99999):
    check(collector._fps(nonsense) is None, f"{nonsense!r} -> nothing")


def session(fps, paused=False, transcoding=True):
    """A session the way Jellyfin sends it."""
    item = {"Id": "film", "Name": "Duna", "Type": "Movie", "RunTimeTicks": 90_000_000_000,
            "MediaStreams": [{"Type": "Video", "Codec": "hevc", "Width": 3840,
                              "Height": 2160, "RealFrameRate": 23.976},
                             {"Type": "Audio", "Codec": "eac3"}]}
    s = {"Id": "s1", "UserId": "u1", "UserName": "Jana", "DeviceId": "d1",
         "Client": "Jellyfin Web", "DeviceName": "Chrome",
         "NowPlayingItem": item,
         "PlayState": {"PlayMethod": "Transcode" if transcoding else "DirectPlay",
                       "IsPaused": paused, "PositionTicks": 600_000_000}}
    if transcoding:
        s["TranscodingInfo"] = {"VideoCodec": "h264", "AudioCodec": "aac",
                                "IsVideoDirect": False, "IsAudioDirect": False,
                                "HardwareAccelerationType": "qsv",
                                "Framerate": fps,
                                "TranscodeReasons": ["VideoCodecNotSupported"]}
    return s


def row():
    return dict(db.query_one("SELECT * FROM playback WHERE item_id = 'film'"))


print()
print("--- the collector ---")
collector._store_sessions([session(96.3)], 30)
r = row()
check(r["transcode_fps"] == 96 and r["video_fps"] == 24,
      f"the first snapshot: transcode 96, video 24 ({r['transcode_fps']}, {r['video_fps']})")
check(r["transcode_fps_sum"] == 96 and r["transcode_fps_samples"] == 1, "and it is the first sample")

collector._store_sessions([session(120)], 30)
r = row()
check(r["transcode_fps"] == 120 and r["transcode_fps_samples"] == 2
      and r["transcode_fps_sum"] == 216, "the second sample is added")

collector._store_sessions([session(3, paused=True)], 30)
r = row()
check(r["transcode_fps_samples"] == 2 and r["transcode_fps"] is None,
      "a pause does not count and the state now disappears - ffmpeg stands still, that is not falling behind")

print()
print("--- the bubble by a running one ---")
live = stats.active_sessions()[0]
fact = next((f for f in live["recount"] if f.get("fps")), None)
check(fact is not None and fact["fps"] == 108 and fact["average"],
      f"during a pause it shows the average ({fact})")
collector._store_sessions([session(18)], 30)
live = stats.active_sessions()[0]
fact = next(f for f in live["recount"] if f.get("fps"))
check(fact["fps"] == 18 and not fact["average"] and fact["too_slow"],
      "now 18 at 24 fps of video: it cannot keep up")
order = [f["what"] for f in live["recount"]]
check(order.index("Frames") == order.index("Audio") + 1
      and order.index("Frames") < order.index("Hardware"),
      f"under Audio, above Hardware ({order})")

client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword",
                            "method": "local"})
now_playing = client.get("/partials/now-playing").text
check("Převod: 18 fps (video má 24) – nestíhá" in now_playing,
      "the Now playing card says so in the bubble")

print()
print("--- the history: the average ---")
collector._store_sessions([], 30)          # the session ended
# The test runs in seconds, so nothing was "watched" - and the pages
# show only plays that have some time.
with db.connect() as conn:
    conn.execute("UPDATE playback SET watched_seconds = 3600 WHERE item_id = 'film'")
    conn.commit()
r = row()
check(r["is_active"] == 0, "the play is closed")
stored = stats.transcode_speed(r)
check(stored["fps"] == 78 and stored["average"] and not stored["too_slow"],
      f"in the history the average (96+120+18)/3 = 78 ({stored})")
for address, where in (("/history", "History"), ("/item/film", "the title's detail"),
                       ("/library/lib?tab=activity", "the library's detail")):
    html = client.get(address).text
    check("Převod: v průměru 78 fps (video má 24)" in html, f"{where} shows the average")

print()
print("--- a transcode of the audio only: the video is copied, the speed is not written ---")
copying = session(640)
copying["Id"] = "s2"
copying["DeviceId"] = "d2"
# Another viewer: for the same viewer and movie the collector would rightly continue
# the previous play (continuing after a pause) and no new row would be created.
copying["UserId"] = "u2"
copying["TranscodingInfo"]["IsVideoDirect"] = True
copying["TranscodingInfo"]["TranscodeReasons"] = ["AudioCodecNotSupported"]
collector._store_sessions([copying], 30)
copy_row = dict(db.query_one("SELECT * FROM playback WHERE device_id = 'd2'"))
check(copy_row["transcode_fps"] is None and copy_row["transcode_fps_samples"] == 0,
      "640 fps of copying the video was not written as the transcode speed")
check(stats.transcode_speed({**copy_row, "transcode_fps_sum": 640,
                             "transcode_fps_samples": 1}) is None,
      "and the bubble does not show it even for data stored before")
collector._store_sessions([], 30)

print()
print("--- without a video transcode nothing ---")
check(stats.transcode_speed({"is_active": 0, "transcode_fps_samples": 0}) is None,
      "without samples no row")
check(stats.transcode_speed({"is_active": 1, "transcode_fps": 30, "video_fps": None})["too_slow"] is False,
      "without the video's frame rate falling behind is not claimed")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
