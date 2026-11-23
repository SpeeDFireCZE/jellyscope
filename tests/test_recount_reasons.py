# -*- coding: utf-8 -*-
r"""Transcoding reasons in plain words.

Jellyfin sends the reason for transcoding as a code: `VideoCodecNotSupported,
AudioCodecNotSupported`. That is how it used to be written out everywhere - in History (without
a space after the comma, so it did not even wrap), on the card of the playback running right now
and in the chart "Why it transcodes", where on top it was cut off at
"AudioCodecNotSupp…". Nobody reads from that, in Czech, English or Spanish,
that the player cannot do the audio codec.

Watched:

* every code Jellyfin knows has a sentence in all languages,
* an unknown code (a newer Jellyfin) shows the way it came - it does not disappear,
* History, the playback card and Insights show the sentence and the code stays
  in `title` for whoever looks for it in Jellyfin's documentation,
* the Insights data keeps the code beside the sentence - it can be searched for
  in Jellyfin's documentation by it.

Run:
    .venv/bin/python tests/test_recount_reasons.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_tmp = tempfile.mkdtemp()
os.environ["JELLYSCOPE_HOME"] = _tmp
os.environ["DATABASE_PATH"] = str(Path(_tmp) / "duvody.db")
os.environ["SECRET_KEY"] = "testovaci-podpisovy-klic-dostatecne-dlouhy"
os.environ["JELLYFIN_URL"] = "http://127.0.0.1:1"
os.environ["JELLYFIN_API_KEY"] = "test-key"
os.environ["JELLYSCOPE_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from jellyscope import accounts, db, formatting, insights, web  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'OK    ' if condition else 'FAIL  '} {label}")
    if not condition:
        failures += 1


print("--- every code has a sentence in all languages ---")
FOLDER = PROJECT / "jellyscope" / "translations"
for language in ("cs", "en", "es"):
    dictionary = json.loads((FOLDER / f"{language}.json").read_text(encoding="utf-8"))
    missing = [sentence for sentence in formatting.TRANSCODE_REASONS.values()
               if not dictionary.get(sentence, "").strip()]
    check(not missing, f"{language}.json: all {len(formatting.TRANSCODE_REASONS)} sentences ({missing[:2]})")
check({"VideoCodecNotSupported", "AudioCodecNotSupported",
       "ContainerBitrateExceedsLimit", "SubtitleCodecNotSupported"} <= set(formatting.TRANSCODE_REASONS),
      "the table knows the codes that come most often")

print()
print("--- converting codes ---")
db.init_db()
db.set_setting("ui_language", "en")
db.forget_settings()
check(formatting.transcode_reasons_text("VideoCodecNotSupported,AudioCodecNotSupported")
      == "Unsupported video codec, unsupported audio codec",
      "two codes, two reasons separated by a comma and a space, a capital letter only at the start")
check(formatting.transcode_reasons_text("NecoNovehoZJellyfinu") == "NecoNovehoZJellyfinu",
      "an unknown code shows the way it came")
check(formatting.transcode_reasons_text("") == "" and formatting.transcode_reasons_text(None) == "",
      "an empty reason is an empty text")
check("VideoCodec" not in formatting.transcode_reasons_text("VideoCodecNotSupported"),
      "a known code is translated")

print()
print("--- History and Insights ---")
accounts.create("admin", "longpassword", is_admin=True)
yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(db.TIME_FORMAT)
with db.connect() as conn:
    conn.execute("INSERT INTO libraries (id, name, collection_type) VALUES ('filmy', 'Filmy', 'movies')")
    conn.execute("INSERT INTO items (id, name, type, library_id, is_missing) "
                 "VALUES ('f1', 'Film', 'Movie', 'filmy', 0)")
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " library_id, started_at, last_seen_at, watched_seconds, is_active, play_method,"
        " transcode_reasons, device_name, client)"
        " VALUES ('s1', 'u1', 'Jana', 'f1', 'Film', 'Movie', 'filmy', ?, ?, 3600, 0,"
        " 'Transcode', 'VideoCodecNotSupported, AudioCodecNotSupported', 'Notebook', 'Jellyfin Web')",
        (yesterday, yesterday))
    conn.commit()

client = TestClient(web.app)
client.post("/login", data={"username": "admin", "password": "longpassword", "method": "local"})
db.set_setting("ui_language", "en")
db.forget_settings()

video_sentence = formatting.transcode_reasons_text("VideoCodecNotSupported")
history_page = client.get("/history").text
check(video_sentence in history_page, "History shows a sentence instead of the code")
check('title="VideoCodecNotSupported, AudioCodecNotSupported"' in history_page,
      "and the code stays in title")

reasons = insights.transcode_reasons(30)
check(any(r.get("label") == "VideoCodecNotSupported" for r in reasons),
      "Insights: the code stays in the data")
check(any(r.get("name") == video_sentence for r in reasons), "and beside it is the sentence")
insights_page = client.get("/video/insights").text
parts = insights_page.split("hbars labels-above")
check(len(parts) > 1 and video_sentence in parts[1][:3000],
      "the reasons chart has the sentence above the bar, whole, not cut off")

print()
print("--- the card of the playback running right now ---")
# The real card is drawn (the partial the Overview fetches itself),
# not a search in the template's source - a typo in the filter would not be found otherwise.
with db.connect() as conn:
    conn.execute(
        "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type,"
        " library_id, started_at, last_seen_at, watched_seconds, is_active, play_method,"
        " transcode_reasons, device_name, client)"
        " VALUES ('s2', 'u2', 'Petr', 'f1', 'Film', 'Movie', 'filmy', ?, ?, 60, 1,"
        " 'Transcode', 'ContainerBitrateExceedsLimit', 'TV', 'Jellyfin Android TV')",
        (yesterday, yesterday))
    conn.commit()
card = client.get("/partials/now-playing").text
stream_sentence = formatting.transcode_reasons_text("ContainerBitrateExceedsLimit")
check(stream_sentence in card and "ContainerBitrateExceedsLimit</p>" not in card,
      f"the playback card shows the sentence ({stream_sentence!r})")
check('title="ContainerBitrateExceedsLimit"' in card, "and the code is in title")

print()
print("DONE - failures:", failures)
sys.exit(1 if failures else 0)
