# -*- coding: utf-8 -*-
"""The details of the stream running right now and the resolution group.

Two bugs that are unrelated, but both made the Overview show less than
Jellyfin knows:

  1. **A session's streams were looked for in the wrong place.** `/Items`
     returns the streams wrapped in `MediaSources`, but `/Sessions` sends them
     right in the item as `MediaStreams`. The code looked only in the first, so
     with direct playback the language, the codec and the bitrate were missing.
     With transcoding it did not show, because there the details are taken
     from `TranscodingInfo`.

  2. **The resolution was decided by the height.** A widescreen 4K movie has
     the dimensions 3840×1608 - the height 1608 is below the 1080p threshold,
     so a 4K movie came out as "1080p". The width must decide.
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
# An own home: without it the stored choice of the database in
# data/database.json would override DATABASE_PATH and the test would run
# against the live database.
os.environ["JELLYSCOPE_HOME"] = str(_tmp)
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "sessions.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic"

from jellyscope import collector, db, formatting, jellyfin, stats  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- the resolution group follows the width ---")
res = formatting.resolution_human
# This is the case from practice: HEVC 3840x1608, that is 4K in the ratio 2.40:1.
check(res(1608, 3840) == "4K", f"3840x1608 is 4K (was {res(1608, 3840)})")
check(res(2160, 3840) == "4K", "3840x2160 is 4K")
check(res(1080, 1920) == "1080p", "1920x1080 is 1080p")
check(res(800, 1920) == "1080p", f"1920x800 is 1080p (was {res(800, 1920)})")
check(res(720, 1280) == "720p", "1280x720 is 720p")
check(res(576, 720) == "576p", "720x576 is 576p")
# Without the width something must be told at least - older records do not have it.
check(res(2160) == "4K", "without the width the height decides")
check(res(1080) == "1080p", "without the width: 1080")
check(res(None, None) == "-", "without dimensions a dash")
check(res(None, 3840) == "4K", "the width alone is enough too")


print()
print("--- the streams are found wherever Jellyfin sends them ---")
IN_SOURCES = {"MediaSources": [{"Bitrate": 25_000_000, "MediaStreams": [
    {"Type": "Video", "Index": 0, "Codec": "hevc", "Width": 3840, "Height": 1608},
    {"Type": "Audio", "Index": 1, "Codec": "eac3", "Language": "cze", "IsDefault": True},
]}]}
# Exactly this is what it looks like in the answer of /Sessions - without MediaSources.
IN_ITEM = {"MediaStreams": [
    {"Type": "Video", "Index": 0, "Codec": "hevc", "Width": 3840, "Height": 1608,
     "BitRate": 25_000_000},
    {"Type": "Audio", "Index": 1, "Codec": "eac3", "Language": "cze", "IsDefault": True},
]}

for label, item in (("from MediaSources", IN_SOURCES), ("from MediaStreams", IN_ITEM)):
    streams = jellyfin.media_streams(item)
    check(len(streams) == 2, f"{label}: both streams were found")
    check(jellyfin.video_dimensions(item) == (3840, 1608), f"{label}: the dimensions")
    check(jellyfin.source_bitrate(item) == 25_000_000, f"{label}: the bitrate")
    langs = jellyfin.selected_languages({"PlayState": {"AudioStreamIndex": 1}}, item)
    check(langs["audio_language"] == "cs", f"{label}: the chosen language")

check(jellyfin.media_streams({}) == [], "an item without streams does not fail")
check(jellyfin.video_dimensions({}) == (None, None), "and the dimensions are empty")


print()
print("--- the session card knows the codec, the bitrate and the dimensions ---")
session = {
    "Id": "s1", "UserId": "u1", "UserName": "Karel",
    "Client": "Jellyfin for Android", "DeviceName": "Xiaomi 17",
    "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": True, "AudioStreamIndex": 1},
    "NowPlayingItem": dict(IN_ITEM, Id="ep-1", Name="1. díl", Type="Episode",
                           SeriesName="Kancelář"),
}
description = collector._describe_stream(session, session["NowPlayingItem"])
check(description["video_codec"] == "hevc", f"the codec: {description['video_codec']}")
check(description["bitrate"] == 25_000_000, f"the bitrate: {description['bitrate']}")
check(description["video_width"] == 3840 and description["video_height"] == 1608,
      f"the dimensions: {description['video_width']}x{description['video_height']}")


print()
print("--- the resolution works even for an item that is not in the library ---")
# Exactly the case from practice: an episode we did not synchronise yet. The
# dimensions used to be taken only from the `items` table, so the badge was missing here.
db.init_db()
collector._store_sessions([session], max_gap_seconds=60)

rows = stats.active_sessions()
check(len(rows) == 1, f"the session was written ({len(rows)})")
row = rows[0]
check(db.query_value("SELECT COUNT(*) FROM items WHERE id = 'ep-1'") == 0,
      "the item really is not in the library")
check(row["width"] == 3840 and row["height"] == 1608,
      f"still we know the dimensions: {row['width']}x{row['height']}")
check(formatting.resolution_human(row["height"], row["width"]) == "4K",
      "and 4K comes out of them")
check(row["current_audio_language"] == "cs", "and the audio stream's language")
# The language is written into the statistics only after MIN_LANGUAGE_SECONDS - see below.
check(row["audio_language"] is None, "but that one does not count in the statistics yet")
check(row["video_codec"] == "hevc", "and the codec")
check(row["is_paused"] == 1, "and that it is paused")


print()
print("--- the progress indicator works without the library too ---")
# The session itself reports the programme's length. It used to be taken only
# from the `items` table, so for an episode we did not synchronise yet the
# indicator was missing and only "Běží 3 min" stood in its place.
TICK = 10_000_000
playback = {
    "Id": "postup", "UserId": "u8", "UserName": "Karel",
    "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": False,
                  "AudioStreamIndex": 1, "PositionTicks": 77 * TICK},
    "NowPlayingItem": {"Id": "ep-bez-knihovny", "Name": "1. díl",
                       "Type": "Episode", "SeriesName": "Kancelář",
                       "RunTimeTicks": 1502 * TICK,
                       "MediaStreams": IN_ITEM["MediaStreams"]},
}
collector._store_sessions([playback], max_gap_seconds=60)
r = next(x for x in stats.active_sessions() if x["item_id"] == "ep-bez-knihovny")

check(db.query_value("SELECT COUNT(*) FROM items WHERE id = 'ep-bez-knihovny'") == 0,
      "the item really is not in the library")
check(r["progress"] is not None, "the progress was computed anyway")
check(round(r["position_seconds"]) == 77 and round(r["runtime_seconds"]) == 1502,
      f"the position and the length match ({r['position_seconds']}/{r['runtime_seconds']})")
check(round(r["remaining_seconds"]) == 1425, "and the remaining time too")

# The time is shown as in the player, not "1 h 17 min" - so it can be compared
# with what the viewer has on the screen.
check(formatting.timecode(r["position_seconds"]) == "1:17",
      f"the position as in the player ({formatting.timecode(r['position_seconds'])})")
check(formatting.timecode(r["runtime_seconds"]) == "25:02", "the length too")
check(formatting.timecode(3661) == "1:01:01", "over an hour the hours are added")
check(formatting.timecode(0) == "0:00" and formatting.timecode(None) == "0:00",
      "zero and a missing value are 0:00")

playback["PlayState"]["PositionTicks"] = 900 * TICK
collector._store_sessions([playback], max_gap_seconds=60)
r = next(x for x in stats.active_sessions() if x["item_id"] == "ep-bez-knihovny")
check(round(r["progress"]) == 60, f"after the move the indicator moved ({r['progress']:.0f} %)")


print()
print("--- switching the track while playing shows on the card ---")
# Two different questions, two different values:
#   audio_language          "what they watched it in"  -> the statistics
#   current_audio_language  "what plays right now"     -> the "Právě se hraje" card
# Were it one value, one of the answers would have to be wrong.
STREAMS = [
    {"Type": "Video", "Index": 0, "Codec": "hevc", "Width": 1920, "Height": 1080},
    {"Type": "Audio", "Index": 1, "Codec": "eac3", "Language": "eng", "IsDefault": True},
    {"Type": "Audio", "Index": 2, "Codec": "ac3", "Language": "cze"},
    {"Type": "Subtitle", "Index": 3, "Language": "cze"},
]


def snapshot(audio: int, subtitles: int | None) -> dict[str, Any]:
    """One view of the session - as if Jellyfin had just returned it."""
    return {
        "Id": "prepinani", "UserId": "u9", "UserName": "Karel",
        "PlayState": {"PlayMethod": "DirectPlay", "IsPaused": False,
                      "AudioStreamIndex": audio, "SubtitleStreamIndex": subtitles},
        "NowPlayingItem": {"Id": "film-prepinani", "Name": "Matrix",
                           "Type": "Movie", "MediaStreams": STREAMS},
    }


def session() -> dict[str, Any]:
    return next(r for r in stats.active_sessions()
                if r["item_id"] == "film-prepinani")


def age(seconds: int) -> None:
    """Moves the session into the past - as if the given time passed.

    The test cannot wait a minute; moving the time in the database is the same
    and it is done at once.
    """
    with db.connect() as conn:
        conn.execute(
            """UPDATE playback
                  SET last_seen_at   = datetime(last_seen_at, ?),
                      language_since = datetime(language_since, ?)
                WHERE session_key LIKE 'prepinani%'""",
            (f"-{seconds} seconds", f"-{seconds} seconds"),
        )


# The timing follows the collector's rule: the first four minutes are logos
# and jingles (they are ignored), then a minute for the track to settle.
collector._store_sessions([snapshot(1, None)], max_gap_seconds=600)
r = session()
check(r["current_audio_language"] == "en", "the card shows right away what plays")
check(r["audio_language"] is None,
      f"but nothing was written into the statistics yet ({r['audio_language']})")

age(30)
collector._store_sessions([snapshot(2, None)], max_gap_seconds=600)
r = session()
check(r["current_audio_language"] == "cs", "after the switch the card shows Czech")
check(r["audio_language"] is None, "and English did not get into the statistics")

age(120)          # 2:30 in total - still the intro
collector._store_sessions([snapshot(2, None)], max_gap_seconds=600)
check(session()["audio_language"] is None,
      "not yet in the middle of the second minute - the intro runs")

age(120)          # 4:30 in total - the intro ended, a minute is measured
collector._store_sessions([snapshot(2, None)], max_gap_seconds=600)
check(session()["audio_language"] is None,
      "not right after the intro either - a minute still has to pass")

age(60)           # 5:30 in total
collector._store_sessions([snapshot(2, None)], max_gap_seconds=600)
r = session()
check(r["audio_language"] == "cs",
      f"after five and a half minutes Czech counts ({r['audio_language']})")
check(r["language_confirmed"] == 1, "and it is marked as confirmed")


print()
print("--- a language once counted no longer changes ---")
age(120)
collector._store_sessions([snapshot(1, None)], max_gap_seconds=600)
r = session()
check(r["audio_language"] == "cs",
      f"a switch at the end does not rewrite the statistics ({r['audio_language']})")
check(r["current_audio_language"] == "en", "but the card goes on with the viewer")


print()
print("--- the subtitles are judged together with the audio ---")
# Whoever fixes the language usually sorts out the subtitles right away too -
# so the whole combination is confirmed at once.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE session_key LIKE 'prepinani%'")

collector._store_sessions([snapshot(1, 3)], max_gap_seconds=600)
age(20)
collector._store_sessions([snapshot(2, None)], max_gap_seconds=600)
check(session()["subtitle_language"] is None,
      "subtitles on at the start and off right away do not count")

age(400)          # past the intro and the minute to settle
collector._store_sessions([snapshot(2, None)], max_gap_seconds=600)
r = session()
check(r["audio_language"] == "cs" and r["subtitle_language"] is None,
      f"the combination the viewer stayed with counts "
      f"({r['audio_language']}/{r['subtitle_language']})")


print()
print("--- a short playback stays without a language ---")
# Whoever switches round and round has no "language they watched with".
# Admitting it is more honest than picking one at random; only plays longer
# than langstats.MIN_PLAY_SECONDS get into the statistics anyway.
with db.connect() as conn:
    conn.execute("DELETE FROM playback WHERE session_key LIKE 'prepinani%'")

for stream in (1, 2, 1, 2, 1, 2, 1, 2):
    collector._store_sessions([snapshot(stream, None)], max_gap_seconds=600)
    age(90)       # over ten minutes together, but nothing lasts a minute
check(session()["audio_language"] is None, "no language lasted long enough")


print()
print("--- the resolution statistics count the same ---")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, synced_at) VALUES ('l','L',?)",
                 (db.utcnow(),))
    for ident, width, height in (("sirokouhly-4k", 3840, 1608), ("plne-4k", 3840, 2160),
                                 ("full-hd", 1920, 1080), ("sirokouhle-hd", 1920, 800)):
        conn.execute(
            """INSERT INTO items (id, name, type, library_id, width, height,
                                  is_missing, synced_at)
               VALUES (?,?,'Movie','l',?,?,0,?)""",
            (ident, ident, width, height, db.utcnow()),
        )

groups = {row["label"]: row["item_count"]
          for row in stats.resolution_breakdown()}
check(groups.get("4K") == 2, f"both 4K movies are in 4K: {groups}")
check(groups.get("1080p") == 2, f"and both HD in 1080p: {groups}")

# A file without its width and height is in the group "unknown" - the card says it in the reader's language.
with db.connect() as conn:
    conn.execute("INSERT INTO items (id, name, type, library_id, is_missing, synced_at) VALUES ('no-size', 'no-size', 'Movie',"
                 " 'l', 0, ?)", (db.utcnow(),))
    conn.commit()
from jellyscope import i18n, web  # noqa: E402

rows = stats.resolution_breakdown()
check([r["label"] for r in rows][-1] == "unknown", f"the unknown group comes last ({[r['label'] for r in rows]})")
token = i18n.set_account_language("cs")
try:
    card = web.templates.get_template("_card_resolution.html").render(resolutions=rows)
finally:
    i18n.reset_account_language(token)
check(i18n.translate("unknown", "cs") in card and ">unknown<" not in card,
      "the card names the unknown group in the reader's language, the legend and the table alike")

# A play without its client and without its device: the Players card and the viewer's devices name it in the
# reader's language too (the API keeps the plain "unknown").
from jellyscope import cards  # noqa: E402

with db.connect() as conn:
    conn.execute("INSERT INTO users (id, name, synced_at) VALUES ('u-nobody', 'Nobody', ?)", (db.utcnow(),))
    conn.execute("INSERT INTO playback (session_key, user_id, item_id, item_name, started_at, last_seen_at, ended_at,"
                 " watched_seconds, is_active) VALUES ('no-client', 'u-nobody', 'no-size', 'no-size', ?, ?, ?, 600, 0)",
                 (db.utcnow(), db.utcnow(), db.utcnow()))
    conn.commit()
token = i18n.set_account_language("cs")
try:
    clients = cards.data_for([cards.BY_KEY["players"]], 30)["clients"]
    devices = stats.user_detail("u-nobody", 30)["devices"]
finally:
    i18n.reset_account_language(token)
czech_unknown = i18n.translate("unknown", "cs")
check([c["label"] for c in clients] == [czech_unknown], f"the Players card: the unknown client in Czech ({clients})")
check([d["label"] for d in devices] == [czech_unknown], f"the viewer's devices: the unknown device in Czech ({devices})")
check([c["label"] for c in stats.client_breakdown(30)] == ["unknown"], "the statistics themselves keep the plain value")


print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
