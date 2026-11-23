"""What is playing right now and the description of a transcode.

Part of the statistics package (see jellyscope/stats/__init__.py)."""
from __future__ import annotations

from typing import Any

from .. import db


def active_session_count() -> int:
    """How much is playing right now. Only a number, not the whole list.

    A query of its own on purpose: every page wants the number (the badge in the side menu), and so does /health,
    which the browser asks every ten seconds from every open tab. Through `len(active_sessions())` it meant joining
    three tables every time and pulling out every column only to count the rows.
    """
    return int(db.query_value(
        "SELECT COUNT(*) FROM playback WHERE is_active = 1", default=0) or 0)


def transcode_speed(row: dict[str, Any]) -> dict[str, Any] | None:
    """How many frames per second the transcode managed - and whether it was enough.

    For a running play the state NOW is interesting (is it stuttering right now?), for the history the average
    over the whole play - one number from the last snapshot would be random. When one is missing, the other is
    taken.

    `too_slow` is true only when the speed is noticeably below the video's frame rate: a transcode running exactly
    at the video's pace is fine, and a deviation of a frame is measuring noise.
    """
    # The picture was only copied - no transcode whose speed would make sense.
    if row.get("transcode_video_direct") == 1:
        return None
    samples = int(row.get("transcode_fps_samples") or 0)
    average = (round(int(row.get("transcode_fps_sum") or 0) / samples)
               if samples else None)
    now_fps = row.get("transcode_fps")
    if row.get("is_active"):
        fps, is_average = (now_fps, False) if now_fps else (average, True)
    else:
        fps, is_average = (average, True) if average else (now_fps, False)
    if not fps:
        return None
    video = row.get("video_fps") or None
    return {"what": "Frames", "fps": int(fps), "average": is_average,
            "video_fps": video,
            "too_slow": bool(video and int(fps) < int(video) - 1)}


def transcode_description(row: dict[str, Any]) -> list[dict[str, Any]]:
    """What exactly the server transcodes in this play.

    The mark "transcode" on its own says almost nothing. Transcoding the picture loads the CPU many times more
    than transcoding the sound - and when one looks for why the server is heating, it is the first thing they
    need to see. Jellyfin reports the difference itself in `TranscodingInfo` (see collector).

    Returns a list of facts, not a finished sentence: the translating belongs to the template, where the signed-in
    person's language is. Every fact is a dict:

        {"what": "Obraz", "direct": False, "z": "hevc", "to": "h264"}
        {"what": "Frames", "fps": 96, "average": True, "video_fps": 24, ...}
        {"what": "Titulky", "burn_in": True}
        {"what": "Hardware", "text": "qsv"}

    With older records (and with imported history) the flag is missing. Then it is derived from the codecs: the
    same codec on both sides means the stream is only repackaged. That is exactly what "direct" means - and both
    codecs show side by side, so one can check it.
    """
    if (row.get("play_method") or "") != "Transcode":
        return []

    facts: list[dict[str, Any]] = []
    for what, direct, source, target in (
        ("Video#picture", row.get("transcode_video_direct"),
         row.get("source_codec"), row.get("video_codec")),
        ("Audio", row.get("transcode_audio_direct"),
         row.get("source_audio_codec"), row.get("audio_codec")),
    ):
        if direct is None:
            if not source or not target:
                continue   # nothing to say, rather stay silent
            direct = str(source).lower() == str(target).lower()
        facts.append({"what": what, "direct": bool(direct), "z": source, "to": target})

    speed = transcode_speed(row)
    if speed:
        facts.append(speed)

    # Subtitles are burnt into the picture - i.e. a transcode of the picture, even when the codec is supported.
    # Jellyfin says so only by this reason.
    reasons = (row.get("transcode_reasons") or "").lower()
    if "subtitle" in reasons:
        facts.append({"what": "Subtitles", "burn_in": True})

    if row.get("transcode_hw"):
        facts.append({"what": "Hardware", "text": str(row["transcode_hw"])})

    return facts


def active_sessions() -> list[dict[str, Any]]:
    """What is playing right now - including where in the show it is.

    We compute the play's progress here, not in the template: it is a calculation and that does not belong in the
    template. The template only shows.
    """
    rows = db.query_all(
        """
        SELECT p.*,
               -- The length from the session comes first: an item we have
               -- not synced yet has none in `items` - and without it the
               -- progress bar would be missing.
               COALESCE(p.media_runtime_ticks, i.runtime_ticks) AS runtime_ticks,
               -- What the session itself reports comes first. We reach into
               -- `items` only when the session sent no dimensions (older records).
               COALESCE(p.video_height, i.height) AS height,
               COALESCE(p.video_width,  i.width)  AS width,
               i.video_codec  AS source_codec,
               i.audio_codec  AS source_audio_codec,
               i.image_tag,
               i.series_image_tag,
               i.series_id,
               i.season_name,
               i.index_number,
               i.parent_index_number,
               l.name AS library_name
        FROM playback p
        LEFT JOIN items i ON i.id = p.item_id
        LEFT JOIN libraries l ON l.id = p.library_id
        WHERE p.is_active = 1
        ORDER BY p.started_at
        """
    )

    for row in rows:
        runtime = row.get("runtime_ticks") or 0
        position = row.get("position_ticks") or 0

        row["runtime_seconds"] = runtime / 10_000_000 if runtime else 0
        row["position_seconds"] = position / 10_000_000 if position else 0
        row["remaining_seconds"] = max(
            0.0, row["runtime_seconds"] - row["position_seconds"]
        )
        # Without a known length a percentage makes no sense - rather nothing than a made-up value. We round already
        # here: the percentage goes straight into the bar's width in the style, and "5.126498002663116%" in HTML
        # serves nobody.
        row["progress"] = (
            round(min(100.0, position / runtime * 100), 1)
            if runtime and position else None
        )
        # For an episode we want the series' poster, not a still from the episode - and with it the tag of the right
        # poster, so that an old one is not served after a change.
        row["poster_id"] = row.get("series_id") or row.get("item_id")
        row["poster_tag"] = (row.get("series_image_tag")
                             if row.get("series_id") else row.get("image_tag"))
        # What exactly is transcoded - for the bubble at the mark "transcode".
        row["recount"] = transcode_description(row)

    return rows
