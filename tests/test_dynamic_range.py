# -*- coding: utf-8 -*-
r"""The dynamic range: SDR / HDR / Dolby Vision.

The range is taken from two sources - from ffprobe (the file is read) or from
Jellyfin (the library is read). The second one knows only SDR and HDR in the
field `VideoRange`, so Dolby Vision hides under it as ordinary HDR; only
`VideoRangeType` tells it apart ("DOVIWithHDR10" is profile 8.1).

Without that the library's overview "SDR / HDR / Dolby Vision" always had the
third column empty - and the same file counted differently depending on where
the details came from.

Run:
    .venv/bin/python tests/test_dynamic_range.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tests"))
from _package import substitute  # noqa: E402 - a substitute in every submodule of the scanner package

_tmp = tempfile.mkdtemp()
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "rozsah.db")
os.environ["SECRET_KEY"] = "testovaci-klic"

from jellyscope import db, formatting, jellyfin, probe, stats  # noqa: E402

db.init_db()
db.set_setting("ui_language", "cs")  # the checks read Czech (English is the default)

failures = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label} {detail}")
    if not condition:
        failures += 1


def from_jellyfin(**video) -> str | None:
    tech = jellyfin.extract_tech_from_item(
        {"MediaSources": [{"MediaStreams": [dict(video, Type="Video", Codec="hevc")]}]})
    return tech.get("video_range")


print("--- what Jellyfin reports ---")
# Profile 8.1 is exactly the case from the library: Jellyfin writes it in the
# badge as "Dolby Vision Profile 8.1 (HDR10)" and in the data as DOVIWithHDR10.
check(from_jellyfin(VideoRange="HDR", VideoRangeType="DOVIWithHDR10") == "DOVI",
      "Dolby Vision profile 8.1 is DOVI, not just HDR")
check(from_jellyfin(VideoRange="HDR", VideoRangeType="DOVI") == "DOVI",
      "profile 5 too")
check(from_jellyfin(VideoRange="HDR", VideoRangeType="DOVIWithHLG") == "DOVI",
      "and DV over HLG too")
# Literally from the library: Jellyfin reports VideoRange "HDR" for that movie
# and VideoRangeType "DOVIWithHDR10Plus" (profile 8, level 6, compat id 1).
# Were exact names looked for instead of the prefix "DOVI", this shape with
# "Plus" would fall through.
check(from_jellyfin(VideoRange="HDR", VideoRangeType="DOVIWithHDR10Plus",
                    VideoDoViTitle="Dolby Vision Profile 8.1 (HDR10)") == "DOVI",
      "DOVIWithHDR10Plus (profile 8.1 from the live library)")
check(from_jellyfin(VideoRange="HDR", VideoRangeType="HDR10") == "HDR", "HDR10 is HDR")
check(from_jellyfin(VideoRange="HDR", VideoRangeType="HDR10Plus") == "HDR", "HDR10+ too")
check(from_jellyfin(VideoRange="HDR", VideoRangeType="HLG") == "HDR", "HLG too")
check(from_jellyfin(VideoRange="SDR", VideoRangeType="SDR") == "SDR", "SDR stays SDR")

# An older Jellyfin does not have `VideoRangeType` - there what `VideoRange` says applies.
check(from_jellyfin(VideoRange="HDR") == "HDR", "without the type VideoRange is believed")
check(from_jellyfin(VideoRange="SDR") == "SDR", "in both directions")

# "Unknown" used to be stored as it came, so a column literally named
# "Unknown" appeared in the chart next to our "neznámé".
check(from_jellyfin(VideoRange="Unknown") is None, "\"Unknown\" does not pass for a range")
check(from_jellyfin() is None, "and a missing detail does not either")

print()
print("--- what ffprobe measures ---")
# Different versions of ffprobe and different containers report Dolby Vision
# differently. It is enough to miss one path and a DV file is written as
# ordinary HDR - so it is looked for three ways.
check(probe._detect_video_range(
    {"side_data_list": [{"side_data_type": "DOVI configuration record",
                         "dv_profile": 8, "dv_bl_signal_compatibility_id": 1}],
     "color_transfer": "smpte2084"}) == "DOVI", "side data, the exact name")
check(probe._detect_video_range(
    {"side_data_list": [{"side_data_type": "Dolby Vision Configuration Record"}]}) == "DOVI",
    "the block's name written differently")
check(probe._detect_video_range(
    {"side_data_list": [{"side_data_type": "unknown", "dv_version_major": 1,
                         "dv_profile": 5}]}) == "DOVI",
    "an unknown block name, but the profile is in it")
check(probe._detect_video_range(
    {"codec_tag_string": "dvhe", "color_transfer": "smpte2084"}) == "DOVI",
    "the codec tag dvhe (MP4/MOV)")
check(probe._detect_video_range({"codec_tag_string": "dvh1"}) == "DOVI", "and dvh1")
check(probe._detect_video_range(
    {"profile": "Dolby Vision Profile 8.1"}) == "DOVI", "the profile's name")

# And above all: none of it may make DV out of an ordinary file.
check(probe._detect_video_range(
    {"codec_tag_string": "hvc1", "color_transfer": "smpte2084"}) == "HDR",
    "ordinary HDR10 stays HDR")
check(probe._detect_video_range(
    {"codec_tag_string": "hev1", "color_transfer": "bt709"}) == "SDR",
    "and SDR stays SDR")
check(probe._detect_video_range(
    {"side_data_list": [{"side_data_type": "Content light level metadata",
                         "max_content": 1000}],
     "color_transfer": "smpte2084"}) == "HDR",
    "other side data do not make DV")
check(probe._detect_video_range({"color_transfer": "smpte2084"}) == "HDR", "HDR from the file")
check(probe._detect_video_range({"color_transfer": "bt709"}) == "SDR", "SDR from the file")

# Both sources must make the same record out of the same file - otherwise the
# statistics would depend on the settings, not on the library.
check(from_jellyfin(VideoRange="HDR", VideoRangeType="DOVIWithHDR10")
      == probe._detect_video_range(
          {"side_data_list": [{"side_data_type": "DOVI configuration record"}]}),
      "both sources agree on Dolby Vision")

print()
print("--- how it is written for a person ---")
check(formatting.video_range_human("DOVI") == "Dolby Vision", "DOVI is Dolby Vision")
check(formatting.video_range_human("HDR") == "HDR", "HDR stays")
check(formatting.video_range_human(None) == "neznámé", "empty is unknown")
check(formatting.video_range_human("nesmysl") == "neznámé", "and nonsense too")

print()
print("--- the breakdown in the library ---")
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name) VALUES ('l1','Filmy')")
    for i, video_range in enumerate(["DOVI", "DOVI", "HDR", "SDR", "SDR", "SDR", None, ""]):
        conn.execute(
            "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
            " video_range) VALUES (?,?,?,?,0,'jellyfin',?)",
            (f"i{i}", f"Film{i}", "Movie", "l1", video_range))
    conn.commit()

breakdown = {r["label"]: r["item_count"] for r in stats.video_range_breakdown()}
check(breakdown.get("Dolby Vision") == 2, f"Dolby Vision has its own column ({breakdown})")
check(breakdown.get("SDR") == 3 and breakdown.get("HDR") == 1, "SDR and HDR match")
# NULL and an empty string are the same - one column, not two named the same.
check(breakdown.get("neznámé") == 2, f"the unknown ones add up into one ({breakdown})")

check(stats.library_overview("l1")["hdr_count"] == 3,
      "the tile \"HDR / Dolby Vision\" counts both together")

print()
print("--- an old ffprobe does not know about Dolby Vision, Jellyfin does ---")
# This is the literal output of ffprobe 4.2.7 (Ubuntu 20.04) on a movie
# Jellyfin describes as "Dolby Vision Profile 8.1 (HDR10)". Only ffmpeg 5 can
# read DV from Matroska, so there is not a trace of it here: no side data, the
# codec tag "[0][0][0][0]" (MKV has no tags) and the profile "Main 10".
old_ffprobe = {
    "codec_name": "hevc", "profile": "Main 10", "codec_type": "video",
    "codec_tag_string": "[0][0][0][0]", "codec_tag": "0x0000",
    "width": 3822, "height": 2066, "pix_fmt": "yuv420p10le",
    "color_range": "tv", "color_space": "bt2020nc",
    "color_transfer": "smpte2084", "color_primaries": "bt2020",
}
check(probe._detect_video_range(old_ffprobe) == "HDR",
      "DV cannot be read from such output - it comes out HDR")
check(jellyfin.video_range_of({"MediaSources": [{"MediaStreams": [
        {"Type": "Video", "VideoRange": "HDR",
         "VideoRangeType": "DOVIWithHDR10Plus",
         "VideoDoViTitle": "Dolby Vision Profile 8.1 (HDR10)"}]}]}) == "DOVI",
      "but Jellyfin knows about the same file")

# So the reported detail is stored next to the measured one and Dolby Vision
# counts as a positive finding: no source reports it by mistake, both can miss it.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
        " video_range, video_range_reported) VALUES"
        " ('dv1','Tvůj film','Movie','l1',0,'ffprobe','HDR','DOVI')")
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
        " video_range, video_range_reported) VALUES"
        " ('dv2','Naopak','Movie','l1',0,'ffprobe','DOVI','HDR')")
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
        " video_range, video_range_reported) VALUES"
        " ('dv3','Bez měření','Movie','l1',0,'jellyfin',NULL,'HDR')")
    conn.commit()

check(stats.item_range(stats.item("dv1")) == "DOVI",
      "measured HDR + reported DOVI = Dolby Vision")
check(stats.item_range(stats.item("dv2")) == "DOVI",
      "and the other way round too - Jellyfin can miss it")
check(stats.item_range(stats.item("dv3")) == "HDR",
      "without a measurement what Jellyfin reports applies")

breakdown2 = {r["label"]: r["item_count"] for r in stats.video_range_breakdown()}
check(breakdown2.get("Dolby Vision") == 4, f"the overview has both ({breakdown2})")
check(stats.library_overview("l1")["hdr_count"] == 6,
      f"and the tile counts HDR and DV ({stats.library_overview('l1')['hdr_count']})")

print()
print("--- filling in the range during the file analysis ---")
# The reported range was written only by the library sync. But a person ties
# the technical data to the "Analýza souborů": they ran it, nothing changed,
# and had no reason to suspect that Dolby Vision is filled in only by another task.
import asyncio  # noqa: E402

from jellyscope import scanner  # noqa: E402

with db.connect() as conn:
    conn.execute(
        "INSERT INTO items (id, name, type, library_id, is_missing, tech_source,"
        " video_range) VALUES ('an1','Analyzovaný','Movie','l1',0,'ffprobe','HDR')")
    conn.commit()

check("an1" in scanner.candidates_for_range_from_jellyfin(),
      "an item without a reported range is a candidate")
check("dv1" not in scanner.candidates_for_range_from_jellyfin(),
      "what already has it reported is not fetched again")


class _FakeClient:
    """A Jellyfin that answers the way the real one did."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def items_by_ids(self, ids):
        return [{"Id": "an1", "MediaSources": [{"MediaStreams": [
            {"Type": "Video", "Codec": "hevc", "VideoRange": "HDR",
             "VideoRangeType": "DOVIWithHDR10Plus",
             "VideoDoViTitle": "Dolby Vision Profile 8.1 (HDR10)"}]}]}]


original = scanner.JellyfinClient
substitute(scanner, "JellyfinClient", _FakeClient)
try:
    check(asyncio.run(scanner.fill_range_from_jellyfin(["an1"])) == 1,
          "the analysis asks Jellyfin for the range")
finally:
    substitute(scanner, "JellyfinClient", original)

after = stats.item("an1")
check(after["video_range"] == "HDR", "the measured detail stays untouched")
check(after["video_range_reported"] == "DOVI", "and the reported one is added next to it")
check(stats.item_range(after) == "DOVI", "the item is Dolby Vision")
check("an1" not in scanner.candidates_for_range_from_jellyfin(),
      "and the second time we do not ask Jellyfin about it")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
