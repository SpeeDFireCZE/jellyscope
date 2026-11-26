"""The playback collector - the heart of the history.

Jellyfin tells you only what is happening **right now**. It remembers no
history. When you want to know who watched what last week, you have to build
that history yourself.

The principle is simple and worth understanding, because Jellystat and Tautulli
work the same way:

    every N seconds ask "what is playing?"
    -> a new session I did not see before  = a playback starts
    -> a session I already know            = add the elapsed time
    -> a session that vanished             = a playback ends

A continuous record thus comes out of a sequence of instant snapshots.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from . import db, i18n
from .jellyfin import QUICK_TIMEOUT, JellyfinClient, JellyfinError
from .jellyfin import media_streams as jellyfin_streams
from .jellyfin import selected_languages as jellyfin_languages
from .jellyfin import source_bitrate as jellyfin_bitrate
from .jellyfin import video_dimensions as jellyfin_dimensions

log = logging.getLogger("jellyscope.collector")

# Keys under which we store the operating state in the settings table.
# The underscore at the start is our sign "this is no user setting".
STATUS_KEY = "_collector_status"
ERROR_KEY = "_collector_error"
# The values of the error's key (i18n.message_parts): the side panel reads it in the reader's language.
ERROR_VALUES_KEY = "_collector_error_values"
LAST_POLL_KEY = "_collector_last_poll"

# How many first minutes of a playback are ignored when the language is determined.
#
# The start of a movie is distributors' logos, jingles and trailers - and the
# viewer only looks for the track during them. Anything that plays at that time
# says nothing about their choice.
#
# The watched time is not lost by it: the whole playback including these minutes
# is added to the language. It is ignored only when DECIDING which language it was.
LANGUAGE_GRACE_SECONDS = 240

# How long a track must play before it counts into the language statistics.
#
# The reason is from practice: a file is often started with the wrong track and
# the viewer switches it right away. Without this period a language nobody
# listened to would get into the statistics - and "60 % English" would say
# something else than what really happened. Only the combination the viewer
# stayed with counts.
#
# The whole combination (audio + subtitles) counts at once: whoever fixes the
# language usually sorts out the subtitles right away too, so they make sense together.
MIN_LANGUAGE_SECONDS = 60


def store_error(error: BaseException | i18n.Message) -> None:
    """The collector failed: its error as a key and its values (i18n.message_parts), read in each reader's language."""
    template, values = i18n.message_parts(error)
    db.set_setting(STATUS_KEY, "error")
    db.set_setting(ERROR_KEY, template)
    db.set_setting(ERROR_VALUES_KEY, values or "")


async def _store_error_quietly(error: BaseException | i18n.Message) -> None:
    """store_error() that cannot end the collector's loop.

    The database that failed the poll (locked by a VACUUM, a PostgreSQL restart, a full disk) usually fails
    this write too. The error is in the log already; the loop waits and tries again. In a thread: a locked
    database makes every write wait for its timeout, and the web would stand still meanwhile.
    """
    try:
        await asyncio.to_thread(store_error, error)
    except Exception:  # noqa: BLE001 - whatever fails here, the collector runs on
        log.exception("collector: the error could not be stored")


def clear_error() -> None:
    db.set_setting(ERROR_KEY, "")
    db.set_setting(ERROR_VALUES_KEY, "")


def error_text(language: str | None = None) -> str:
    """The stored error in a language - the reader's when None."""
    return i18n.stored_text(db.get_setting(ERROR_KEY, ""), db.get_setting(ERROR_VALUES_KEY, ""), language)


def _parse(timestamp: str) -> datetime:
    """The opposite of db.utcnow() - from text back to a date and time."""
    return datetime.strptime(timestamp, db.TIME_FORMAT).replace(tzinfo=UTC)


def _session_key(session: dict[str, Any], item: dict[str, Any]) -> str:
    """An unambiguous mark "this session plays this item".

    Why not just the session's id? Because when an episode ends and the next
    one starts, Jellyfin uses **the same** session. Without the item's id in the
    key both episodes would merge into one record.
    """
    return f"{session.get('Id')}::{item.get('Id')}"


# How far back an already running record of the same playback is looked for. It
# must be more than the polling interval, but not so much that a playback from
# yesterday would count as a continuation.
TAKEOVER_MINUTES = 15


def _take_over_session(conn: Any, key: str, session: dict[str, Any],
                       item: dict[str, Any], now: str) -> Any:
    """Does this playback not run under another key already? Then take it over.

    Why it is here: **more collectors at once** can by mistake run against one
    database - typically when an old version of the application stays running
    next to the new one. Each creates its own row for the same movie and
    duplicates arise in the history that nobody untangles any more.

    The session key is not enough for that: every version can put it together
    differently, and exactly that is why those two rows did not meet. So we look
    by what is the same on both sides - **who** plays and **what** plays.

    When such a record is found, we rewrite its key to ours and continue in it.
    A duplicate thus does not arise at all, instead of being cleaned up later.
    """
    user = session.get("UserId")
    item_id = item.get("Id")
    if not user or not item_id:
        return None

    # We compute the boundary in Python, not in SQL. `datetime(?, ?)` is a
    # function of SQLite; PostgreSQL does not know it and the dialect translator
    # handles only the shape `datetime('now', ?)`. A finished time as a parameter works in both.
    since = (_parse(now) - timedelta(minutes=TAKEOVER_MINUTES)).strftime(db.TIME_FORMAT)

    row = conn.execute(
        """SELECT id, last_seen_at, watched_seconds,
                  audio_language, subtitle_language,
                  current_audio_language, current_subtitle_language,
                  language_since, language_confirmed
             FROM playback
            WHERE is_active = 1
              AND user_id = ? AND item_id = ?
              AND last_seen_at >= ?
         ORDER BY last_seen_at DESC
            LIMIT 1""",
        (str(user), str(item_id), since),
    ).fetchone()

    if row is None:
        return None

    conn.execute("UPDATE playback SET session_key = ? WHERE id = ?",
                 (key, row["id"]))
    log.info("took over a running record %s (different session key), no duplicate created",
             row["id"])
    return row


# How long after an interruption a playback still counts as the same.
#
# Half an hour is a compromise: a break for coffee or for the TV going to sleep
# fits, but a movie played in the evening and again before sleep are already two
# different viewings.
RESUME_MINUTES = 30

# From which position it makes sense to tell "continues" from "started again".
# Below five minutes it does not matter - whoever plays a movie from the start
# after two minutes has rather just restarted it.
RESTART_TICKS = 5 * 60 * 10_000_000


def _resume_interrupted(conn: Any, key: str, session: dict[str, Any],
                        item: dict[str, Any], position: Any, now: str) -> Any:
    """Is this viewer not continuing what they interrupted a moment ago?

    Why it is here: some players vanish from `/Sessions` completely on a pause.
    The collector closes such a record - and when the playback goes on, it
    creates a new one. One viewing is then in the history twice and in the
    statistics as two plays.

    It counts as a continuation when all of this fits:

      * the same viewer and the same title,
      * less than RESUME_MINUTES passed since the last snapshot,
      * and **it did not start from the beginning** - when the player is at
        zero, whereas before it was in the middle of the movie, it is a new
        viewing, not a continuation.

    The watched time does not inflate by it: the gap between snapshots is cut
    to a few times the polling interval (see max_gap_seconds), so the pause's
    time counts nowhere.
    """
    user = session.get("UserId")
    item_id = item.get("Id")
    if not user or not item_id:
        return None

    since = (_parse(now) - timedelta(minutes=RESUME_MINUTES)).strftime(db.TIME_FORMAT)
    row = conn.execute(
        """SELECT id, last_seen_at, watched_seconds, position_ticks,
                  audio_language, subtitle_language,
                  current_audio_language, current_subtitle_language,
                  language_since, language_confirmed
             FROM playback
            WHERE is_active = 0
              AND user_id = ? AND item_id = ?
              AND last_seen_at >= ?
         ORDER BY last_seen_at DESC
            LIMIT 1""",
        (str(user), str(item_id), since),
    ).fetchone()

    if row is None:
        return None

    try:
        new_position = int(position or 0)
        old_position = int(row["position_ticks"] or 0)
    except (TypeError, ValueError):
        new_position = old_position = 0

    # Started again from the beginning? Then it is a second viewing of the same movie.
    if old_position > RESTART_TICKS and new_position < old_position / 2:
        return None

    conn.execute(
        """UPDATE playback
              SET is_active = 1, ended_at = NULL, session_key = ?
            WHERE id = ?""",
        (key, row["id"]),
    )
    log.info("continued the interrupted playback %s (a pause, not a new start)",
             row["id"])
    return row


def _bit(value: Any) -> int | None:
    """Yes/no from Jellyfin to 1/0 - and leaves I-do-not-know as I-do-not-know.

    On purpose we do not use bool(): when the detail is missing in the answer,
    it is a different situation than "not direct". Zero would claim the picture
    is transcoded, even though we in fact know nothing about it.
    """
    if value is None:
        return None
    return 1 if value else 0


def _fps(value: Any) -> int | None:
    """Frames per second as a whole number. None for nonsense and for zero.

    It is rounded: for the question "does the transcode keep up with the video?"
    the tenths do not matter and 23.976 reads to people as 24 anyway. Zero means
    "Jellyfin does not know yet" (the first seconds of a transcode, a pause), not
    "zero frames" - it does not belong in the average. The ceiling guards the
    average against one runaway number.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.5 <= number <= 2000:
        return None
    return int(round(number))


def _video_frame_rate(item: dict[str, Any]) -> int | None:
    """The source video's frame rate - what the transcode speed is measured against."""
    video = next((s for s in jellyfin_streams(item) if s.get("Type") == "Video"), {})
    return _fps(video.get("RealFrameRate")) or _fps(video.get("AverageFrameRate"))


def _describe_stream(session: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """What exactly flows to the player - codecs and bitrate.

    When the server transcodes, we care about the resulting (transcoded) flow.
    When it plays directly, it is the original file's codec.
    """
    # We take the dimensions from the source even when transcoding: `TranscodingInfo`
    # reports them only sometimes and the source's resolution is the more useful
    # information for a person ("I have a 4K movie") than what the server shrinks it to right now.
    width, height = jellyfin_dimensions(item)
    # We take the length from the session, not from the library: for an episode
    # we did not synchronise yet, the progress indicator would have nothing to compute from.
    length = item.get("RunTimeTicks")
    video_fps = _video_frame_rate(item)

    transcoding = session.get("TranscodingInfo") or {}
    if transcoding:
        reasons = transcoding.get("TranscodeReasons") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        # What exactly is transcoded. A bare "transcode" says almost nothing:
        # transcoding the picture loads the server many times more than
        # transcoding the audio, and a person asking "why does the server heat"
        # needs to see exactly this difference. Jellyfin says it itself, it need
        # not be guessed from the codecs.
        return {
            "video_codec": transcoding.get("VideoCodec"),
            "audio_codec": transcoding.get("AudioCodec"),
            "bitrate": transcoding.get("Bitrate"),
            "transcode_reasons": ", ".join(reasons) or None,
            "video_direct": _bit(transcoding.get("IsVideoDirect")),
            "audio_direct": _bit(transcoding.get("IsAudioDirect")),
            "hw": transcoding.get("HardwareAccelerationType") or None,
            # The transcode speed as ffmpeg reports it (frames per second).
            # Slower than the video itself = the transcode does not keep up and
            # the picture stutters. Only when the picture really is transcoded:
            # when only the audio is, the picture is copied and ffmpeg reports
            # the copying speed - easily hundreds of frames that would look like
            # "the fastest transcode" in the statistics.
            "fps": (None if transcoding.get("IsVideoDirect") is True
                    else _fps(transcoding.get("Framerate"))),
            "video_fps": video_fps,
            "video_width": width,
            "video_height": height,
            "runtime_ticks": length,
        }

    streams = jellyfin_streams(item)
    video = next((s for s in streams if s.get("Type") == "Video"), {})
    audio = next((s for s in streams if s.get("Type") == "Audio"), {})
    return {
        "video_codec": video.get("Codec"),
        "audio_codec": audio.get("Codec"),
        "bitrate": jellyfin_bitrate(item),
        "transcode_reasons": None,
        # Nothing is transcoded, so there is nothing to describe.
        "video_direct": None,
        "audio_direct": None,
        "hw": None,
        "fps": None,
        "video_fps": video_fps,
        "video_width": width,
        "video_height": height,
        "runtime_ticks": length,
    }


def close_orphans() -> int:
    """Closes records left hanging after the application's previous run.

    When Jellyscope falls or you turn it off in the middle of a movie, a record
    with is_active = 1 stays in the database. On the next start we close it with
    the time we last saw it - that is the most honest estimate we have.
    """
    with db.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE playback
               SET is_active = 0,
                   ended_at  = COALESCE(ended_at, last_seen_at)
             WHERE is_active = 1
            """
        )
        return cursor.rowcount


async def poll_once(client: JellyfinClient, max_gap_seconds: int) -> dict[str, int]:
    """One snapshot: load the active sessions and project them into the database.

    Waiting for Jellyfin is asynchronous, but writing to the database is not -
    sqlite3 has no `await`. Were it written right here, it would freeze the
    whole application for that time: nobody would load a page until the
    collector finished writing. So the writing goes to a thread (`asyncio.to_thread`).
    """
    sessions = await client.sessions()
    return await asyncio.to_thread(_store_sessions, sessions, max_gap_seconds)


def _find_session(conn: Any, key: str, session: dict[str, Any], item: dict[str, Any],
                  play_state: dict[str, Any], now: str) -> Any:
    """The running record of this playback - by the key, taken over, or resumed after a pause.

    `None` = there is no such thing and the playback is to be created as new.
    """
    existing = conn.execute(
        "SELECT id, last_seen_at, watched_seconds,"
        "       audio_language, subtitle_language,"
        "       current_audio_language, current_subtitle_language,"
        "       language_since, language_confirmed"
        "  FROM playback WHERE session_key = ? AND is_active = 1",
        (key,),
    ).fetchone()

    if existing is None:
        existing = _take_over_session(conn, key, session, item, now)

    # Only when nothing runs: are we not resuming something the viewer paused a
    # moment ago? It has to be only here - a running record wins over a closed one.
    if existing is None:
        existing = _resume_interrupted(
            conn, key, session, item, play_state.get("PositionTicks"), now)
    return existing


def _create_session(conn: Any, key: str, session: dict[str, Any], item: dict[str, Any],
                    play_state: dict[str, Any], is_paused: bool, stream: dict[str, Any],
                    chosen: dict[str, Any], fps_sample: float | None, now: str) -> None:
    """A new playback: looks up the item's library and writes the first snapshot."""
    library_row = conn.execute(
        "SELECT library_id FROM items WHERE id = ?", (item.get("Id"),)
    ).fetchone()

    conn.execute(
        """
        INSERT INTO playback (
            session_key, user_id, user_name,
            item_id, item_name, item_type, series_name, library_id,
            client, device_name, device_id, remote_address,
            play_method, transcode_reasons, video_codec, audio_codec, bitrate,
            transcode_video_direct, transcode_audio_direct, transcode_hw,
            transcode_fps, transcode_fps_sum, transcode_fps_samples,
            video_fps,
            audio_language, subtitle_language,
            current_audio_language, current_subtitle_language,
            language_since, language_confirmed,
            media_runtime_ticks, video_width, video_height,
            started_at, last_seen_at, watched_seconds, paused_seconds,
            position_ticks, is_paused, is_active
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,1)
        """,
        (
            key,
            session.get("UserId"),
            session.get("UserName"),
            item.get("Id"),
            item.get("Name"),
            item.get("Type"),
            item.get("SeriesName"),
            library_row["library_id"] if library_row else None,
            session.get("Client"),
            session.get("DeviceName"),
            session.get("DeviceId"),
            session.get("RemoteEndPoint"),
            play_state.get("PlayMethod"),
            stream["transcode_reasons"],
            stream["video_codec"],
            stream["audio_codec"],
            stream["bitrate"],
            stream["video_direct"],
            stream["audio_direct"],
            stream["hw"],
            fps_sample,
            fps_sample or 0,
            1 if fps_sample else 0,
            stream["video_fps"],
            # Nothing into the statistics yet - the language counts only after
            # MIN_LANGUAGE_SECONDS, see above. The first seconds often
            # play a track the viewer switches right away.
            None,
            None,
            chosen["audio_language"],
            chosen["subtitle_language"],
            now,
            0,
            stream["runtime_ticks"],
            stream["video_width"],
            stream["video_height"],
            now,
            now,
            play_state.get("PositionTicks"),
            1 if is_paused else 0,
        ),
    )


def _language_for_stats(
        existing: Any, chosen: dict[str, Any], is_paused: bool, gap: int,
        now: str, now_dt: datetime) -> tuple[str | None, str | None, str, bool]:
    """What to write as the playback's language: (audio, subtitles, language_since, confirmed).

    It only computes, it does not touch the database - the decision is all here, SQL only stores it.
    """
    # When the language counts into the statistics.
    #
    # As long as the tracks change, the clock resets - only the combination
    # the viewer stayed with for at least MIN_LANGUAGE_SECONDS gets into the
    # statistics. Once it counts, it no longer changes: the playback belongs
    # to the language it was really watched in, even when somebody switches it
    # at the end.
    #
    # We compute it here and not in SQL on purpose - it is deciding, not
    # storing, and in Python it is visible at first glance.
    tracks_changed = (
        chosen["audio_language"] != existing["current_audio_language"]
        or chosen["subtitle_language"] != existing["current_subtitle_language"]
    )
    confirmed = bool(existing["language_confirmed"])
    language_since = existing["language_since"] or now

    # The first minutes of a playback are skipped - see LANGUAGE_GRACE_SECONDS.
    # We count the watched time, not the time since the start: when a viewer
    # starts a movie and leaves for an hour for lunch, the pause is not to count
    # into those four minutes.
    watched = int(existing["watched_seconds"] or 0) + (0 if is_paused else gap)
    past_intro = watched >= LANGUAGE_GRACE_SECONDS

    if not confirmed:
        if tracks_changed or not past_intro:
            # During the intro we keep resetting the clock, so it starts
            # measuring only from the first snapshot after it.
            language_since = now
        elif ((now_dt - _parse(language_since)).total_seconds() >= MIN_LANGUAGE_SECONDS
              and watched >= LANGUAGE_GRACE_SECONDS + MIN_LANGUAGE_SECONDS):
            # Two conditions, not one. By itself the first would confirm the
            # language earlier: the clock resets at every snapshot of the
            # intro, so the last reset falls somewhere JUST BEFORE the fourth
            # minute - and the minute of measuring then ends before the fifth.
            #
            # The second condition says a plain requirement: before the
            # language counts, the playback must last at least the intro plus
            # that minute. How many snapshots fit in meanwhile no longer matters.
            confirmed = True

    # What is written into the statistics. The combination just confirmed,
    # otherwise what is there already.
    #
    # It used to be handled by `CASE WHEN ? THEN ? ELSE ... END` right in SQL.
    # It worked on SQLite, but PostgreSQL needs a truth value in WHEN, not a
    # number ("argument of CASE/WHEN must be type boolean, not type smallint").
    # The decision belongs here anyway - SQL is to store, not to decide.
    just_confirmed = confirmed and not existing["language_confirmed"]
    stored_audio = (chosen["audio_language"] if just_confirmed
                    else existing["audio_language"])
    stored_subtitles = (chosen["subtitle_language"] if just_confirmed
                        else existing["subtitle_language"])
    return stored_audio, stored_subtitles, language_since, confirmed


def _continue_session(conn: Any, existing: Any, play_state: dict[str, Any], is_paused: bool,
                      stream: dict[str, Any], chosen: dict[str, Any], fps_sample: float | None,
                      now: str, now_dt: datetime, max_gap_seconds: int) -> None:
    """A known playback: adds the time since the last snapshot and writes what plays now."""
    # I know the session already - add the time that passed since the last snapshot.
    gap = (now_dt - _parse(existing["last_seen_at"])).total_seconds()
    # A cut: when the application dropped out for an hour, we do not want to
    # write that hour as "watched". We count only a reasonable interval.
    gap = max(0, min(int(gap), max_gap_seconds))

    stored_audio, stored_subtitles, language_since, confirmed = _language_for_stats(
        existing, chosen, is_paused, gap, now, now_dt)

    conn.execute(
        """
        UPDATE playback
           SET last_seen_at      = ?,
               watched_seconds   = watched_seconds + ?,
               paused_seconds    = paused_seconds + ?,
               position_ticks    = ?,
               play_method       = COALESCE(?, play_method),
               transcode_reasons = COALESCE(?, transcode_reasons),
               bitrate           = COALESCE(?, bitrate),
               -- Without COALESCE: they describe the state now. When the viewer
               -- switches to a track that no longer needs a transcode,
               -- the badge is to stop claiming the picture is transcoded.
               transcode_video_direct = ?,
               transcode_audio_direct = ?,
               transcode_hw           = ?,
               -- The transcode speed now (without COALESCE for the same
               -- reason as a line above) and a sample into the average -
               -- only when there is one.
               transcode_fps          = ?,
               transcode_fps_sum   = transcode_fps_sum + ?,
               transcode_fps_samples   = transcode_fps_samples + ?,
               video_fps              = COALESCE(video_fps, ?),
               -- Two pairs of columns, because these are two different
               -- questions and one value could not answer both:
               --
               --   audio_language          "what they watched it in"
               --   current_audio_language  "what plays right now"
               --
               -- The first pair is written once - at the moment the
               -- language "lasted" long enough (see the computation above).
               -- The second is rewritten at every snapshot, because
               -- the "Now playing" card is to show reality.
               --
               -- Both pairs are written directly, without COALESCE:
               -- what is to be written is decided above already. For the second
               -- pair the rewrite is moreover intended - turned-off subtitles
               -- are to show by the badge vanishing, not by
               -- the last known language staying hanging.
               audio_language    = ?,
               subtitle_language = ?,
               language_since    = ?,
               language_confirmed = ?,
               current_audio_language    = ?,
               current_subtitle_language = ?,
               -- The dimensions are filled in as soon as Jellyfin sends them.
               -- At the first snapshot it sometimes does not have them yet.
               -- The length is filled in as soon as Jellyfin sends it;
               -- at the first snapshot it sometimes does not have it yet.
               media_runtime_ticks = COALESCE(media_runtime_ticks, ?),
               video_width       = COALESCE(video_width, ?),
               video_height      = COALESCE(video_height, ?),
               -- This one on the contrary is rewritten every time: we care about
               -- the state now, not the one at the start.
               is_paused         = ?
         WHERE id = ?
        """,
        (
            now,
            0 if is_paused else gap,
            gap if is_paused else 0,
            play_state.get("PositionTicks"),
            play_state.get("PlayMethod"),
            stream["transcode_reasons"],
            stream["bitrate"],
            stream["video_direct"],
            stream["audio_direct"],
            stream["hw"],
            fps_sample,
            fps_sample or 0,
            1 if fps_sample else 0,
            stream["video_fps"],
            stored_audio,
            stored_subtitles,
            language_since,
            1 if confirmed else 0,
            chosen["audio_language"],
            chosen["subtitle_language"],
            stream["runtime_ticks"],
            stream["video_width"],
            stream["video_height"],
            1 if is_paused else 0,
            existing["id"],
        ),
    )


def _close_vanished(conn: Any, active_keys: list[str], now: str) -> int:
    """Ends the active records Jellyfin no longer reports. Returns how many there were."""
    if active_keys:
        placeholders = ",".join("?" for _ in active_keys)
        cursor = conn.execute(
            f"""
            UPDATE playback
               SET is_active = 0, ended_at = ?
             WHERE is_active = 1 AND session_key NOT IN ({placeholders})
            """,
            (now, *active_keys),
        )
    else:
        cursor = conn.execute(
            "UPDATE playback SET is_active = 0, ended_at = ? WHERE is_active = 1",
            (now,),
        )
    return cursor.rowcount


def _store_sessions(sessions: list[dict[str, Any]], max_gap_seconds: int) -> dict[str, int]:
    """Writing the snapshot to the database. It runs in a thread, not on the event loop."""
    now = db.utcnow()
    now_dt = _parse(now)

    active_keys: list[str] = []
    started = 0

    with db.connect() as conn:
        for session in sessions:
            item = session.get("NowPlayingItem")
            if not item:
                continue  # the session is connected, but plays nothing

            key = _session_key(session, item)
            active_keys.append(key)

            play_state = session.get("PlayState") or {}
            is_paused = bool(play_state.get("IsPaused"))
            stream = _describe_stream(session, item)
            chosen = jellyfin_languages(session, item)
            # A sample for the transcode speed average - only when it really
            # transcodes and does not stand. During a pause ffmpeg slows down or
            # stands and the average would claim the server did not keep up.
            fps_sample = stream["fps"] if stream["fps"] and not is_paused else None

            existing = _find_session(conn, key, session, item, play_state, now)
            if existing is None:
                _create_session(conn, key, session, item, play_state, is_paused, stream,
                                chosen, fps_sample, now)
                started += 1
                continue

            _continue_session(conn, existing, play_state, is_paused, stream, chosen,
                              fps_sample, now, now_dt, max_gap_seconds)

        # Anything active in the database that Jellyfin no longer reports has ended.
        ended = _close_vanished(conn, active_keys, now)

    return {"active": len(active_keys), "started": started, "ended": ended}


def _close_orphans_quietly() -> None:
    """close_orphans() that never raises: it is a database write outside the loop's try.

    A database locked at the start (a restore or an update just before the restart) ended the collector
    before its first poll and nothing was collected until the next restart; at the shutdown its error
    replaced the cancellation. The left-open playbacks are closed by the first poll that sees them gone.
    """
    try:
        close_orphans()
    except Exception:  # noqa: BLE001 - the collector must start and stop whatever the database does
        log.exception("collector: closing the playbacks left open failed")


async def run_forever() -> None:
    """An endless loop running in the background for the whole time the application runs.

    It must never fall. When Jellyfin drops out, we write the error, wait and
    try again - the application as a whole runs on.
    """
    _close_orphans_quietly()

    backoff = 0  # how many seconds extra to wait after an error

    while True:
        try:
            # Inside the try: a database that cannot be read right now is an error like any other.
            interval = db.get_int_setting("poll_interval", minimum=2, maximum=300, fallback=10)
            async with JellyfinClient(*db.jellyfin_connection(),
                                      timeout=QUICK_TIMEOUT) as client:
                # Within one connection we run more snapshots - opening the
                # HTTP connection again every time is needless work.
                for _ in range(60):
                    result = await poll_once(client, max_gap_seconds=interval * 3)
                    db.set_setting(STATUS_KEY, "ok")
                    clear_error()
                    db.set_setting(LAST_POLL_KEY, db.utcnow())
                    backoff = 0

                    if result["started"] or result["ended"]:
                        log.info(
                            "playbacks: %d active, %d started, %d ended",
                            result["active"], result["started"], result["ended"],
                        )

                    interval = db.get_int_setting("poll_interval", 2, 300, 10)
                    await asyncio.sleep(interval)

        except asyncio.CancelledError:
            # The application shuts down - we clean up after ourselves and end.
            _close_orphans_quietly()
            raise
        except JellyfinError as exc:
            log.warning("collector: %s", exc)
            await _store_error_quietly(exc)
        except Exception as exc:  # noqa: BLE001 - the loop must not die of anything
            log.exception("collector: unexpected error")
            await _store_error_quietly(i18n.Message("Unexpected error: {error}", error=exc))

        # After an error we wait gradually longer (2, 4, 8 ... max 60 s). This is
        # called exponential backoff and it is polite behaviour towards a server
        # that has a problem right now.
        backoff = min(60, (backoff * 2) or 2)
        await asyncio.sleep(backoff)
