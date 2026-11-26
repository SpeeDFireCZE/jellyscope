"""The import's shared helpers: time, record keys, the guard "we only read from Jellyfin", building and writing a row, splitting an episode title.

Part of the package importers (see jellyscope/importers/__init__.py)."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import db, languages
from ..i18n import TranslatableError
from ..jellyfin import id_without_dashes
from ..stats.kinds import watching_only

# Listening to music and books (audiobooks, reading) are no orphan records (a piece of SQL for queries over
# `playback p`). Neither a track nor a book is in `items` - only movies and episodes are there. Without this
# condition the tidy relinked such a row by name to a movie of the same name ("Interstellar") and looking it up in
# Jellyfin spent requests on it. `watching_only` leaves out the kinds Audio, Book and AudioBook. The NOT EXISTS
# clauses concern rows whose kind is missing or wrong: one whose item is a known track in `music_track` is paired
# by the music sync, one whose item is a known book in `book` by the books sync.
NOT_LISTENING_OR_READING = (watching_only("p.")
                            + " AND NOT EXISTS (SELECT 1 FROM music_track hs WHERE hs.id = p.item_id)"
                            + " AND NOT EXISTS (SELECT 1 FROM book bk WHERE bk.id = p.item_id)")


class ImportError_(TranslatableError):
    """An import error worth showing to the user - a key and its values (i18n.TranslatableError)."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_time(raw: Any) -> str | None:
    """Convert a time from a foreign format to our single shape.

    Every tool writes time a little differently. Converting to one shape right at the input is the same
    principle as with language codes - normalisation.
    """
    if raw is None:
        return None

    text = str(raw).strip()
    if not text:
        return None

    candidate = text.replace("Z", "+00:00").replace("T", " ")
    if "." in candidate:
        candidate = candidate.split(".", 1)[0]
    if "+" in candidate[10:]:
        candidate = candidate[:10] + candidate[10:].split("+", 1)[0]

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(candidate.strip(), pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=UTC).strftime(db.TIME_FORMAT)

    return None


def _existing_keys(prefix: str) -> set[str]:
    """The keys we already have from this source - so that nothing doubles."""
    rows = db.query_all(
        "SELECT session_key FROM playback WHERE session_key LIKE ?", (f"{prefix}%",)
    )
    return {row["session_key"] for row in rows}


def _normalize_id(value: Any) -> str:
    """Unify the shape of an identifier from Jellyfin.

    The same user may be written as "a1b2c3d4e5f6..." or "a1b2c3d4-e5f6-...". It is one and the same
    number, only written differently: Jellyfin sends it in its API without dashes, whereas the Playback
    Reporting plugin and Jellystat often store it with dashes.

    If we did not deal with it, `users.id = playback.user_id` would not match and question marks would
    stay in the history for ever instead of names. The one function for the whole app is
    `jellyfin.id_without_dashes`.
    """
    return id_without_dashes(value)


def _insert(rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO playback (
                session_key, user_id, user_name, item_id, item_name, item_type,
                series_name, library_id, client, device_name, play_method,
                audio_language, started_at, last_seen_at, ended_at,
                watched_seconds, paused_seconds, is_active
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
            """,
            rows,
        )
    return len(rows)


# What the column with the track's language may be called. The plugin's basic table does not have it, but
# some versions and custom changes do - and when the value arrives, it would be a pity to throw it away.
# It is compared in lower case without underscores, so the spelling does not matter.
LANGUAGE_COLUMNS = ("audiolanguage", "audiostreamlanguage", "language",
                    "audiolang", "audio")


def _language_from_source(data: dict[str, Any]) -> str | None:
    """Take the track's language from the row, if the source sends it at all."""
    for key, value in data.items():
        compared = str(key).lower().replace("_", "").replace(" ", "")
        if compared in LANGUAGE_COLUMNS and value:
            code = languages.normalize(str(value))
            if code and code != languages.UNKNOWN:
                return code
    return None


def _build_row(
    key: str,
    user_id: str | None,
    item_id: str | None,
    started_at: str,
    duration: int,
    item_type: str | None,
    item_name: str | None,
    client: str | None,
    device: str | None,
    method: str | None,
    users: dict[str, str],
    items: dict[str, dict[str, Any]],
    user_name: str | None = None,
    audio_language: str | None = None,
    series_name: str | None = None,
) -> tuple[Any, ...]:
    """Build one history row in the shape of our table.

    The order in which the user's name and the item's type are looked for is not random: our own tables
    are the most reliable, then what the source sent, and only last "?". Thanks to that one can import
    even before the app connects to Jellyfin at all - the names are filled in later, see
    refresh_playback_metadata().
    """
    item_row = items.get(item_id or "") or {}
    ended = (
        datetime.strptime(started_at, db.TIME_FORMAT) + timedelta(seconds=duration)
    ).strftime(db.TIME_FORMAT)

    return (
        key,
        user_id,
        users.get(user_id or "") or user_name or "?",
        item_id,
        item_row.get("name") or item_name or "?",
        item_row.get("type") or item_type,
        # The series' name: our library is authoritative, but when we know nothing about the item yet
        # (typically an import before the first sync), what the source sent is taken.
        item_row.get("series_name") or series_name,
        item_row.get("library_id"),
        client,
        device,
        method,
        # The language the source sent takes precedence - it says what the viewer REALLY listened to. Some
        # versions of the Playback Reporting plugin store it; most sources do not.
        #
        # When the source does not have it, the file's default track is left - and when we do not know
        # even that, it stays empty. We invent nothing on purpose: a made-up value is worse than none.
        audio_language or item_row.get("default_audio_language"),
        started_at,
        ended,
        ended,
        duration,
    )


# ---------------------------------------------------------------------------
# The guard: we only read from Jellyfin
# ---------------------------------------------------------------------------
#
# Jellyscope **never changes** Jellyfin. Everything else is plain GET requests, where that holds by itself.
# The only exception is this plugin: its query interface is POST, because the SQL is sent to it in the
# request's body.
#
# A POST by itself changes nothing - what is sent in it does. So that no command other than reading gets
# here by mistake (say in a later change), every query goes through this sieve. Better the import refuses
# to work than writes into someone else's database.

FORBIDDEN_WORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "GRANT",
)


def read_only(sql: str) -> str:
    """Let through only a single SELECT query. Refuse anything else."""
    cleaned = " ".join(sql.split()).rstrip(";").strip()

    if not cleaned.upper().startswith("SELECT"):
        raise ImportError_(
            "Internal safeguard: only a SELECT may be sent to Jellyfin."
        )
    # A semicolon in the middle would mean several commands in a row.
    if ";" in cleaned:
        raise ImportError_(
            "Internal safeguard: the query must not hold more than one command."
        )

    words = set(cleaned.upper().replace("(", " ").replace(")", " ").split())
    found = sorted(words & set(FORBIDDEN_WORDS))
    if found:
        raise ImportError_(
            f"Internal safeguard: the query holds writing commands {found}."
        )
    return cleaned


# ---------------------------------------------------------------------------
# Splitting an episode title
# ---------------------------------------------------------------------------
#
# How Jellystat and Playback Reporting compose an episode's title:
#
#     "Seal Team 6 - s02e07 - 7. epizoda"
#     "Nadace - s02e10 - Mýty o stvoření"
#
# It holds everything we need to identify the episode unambiguously: the series, the season and the
# number. Looking up **the whole string** in the library makes no sense - there the episode is called
# only "7. epizoda". The series ends in a visible character (the title is stripped): `.+?` tried every length of
# it against a long run of spaces (seconds for one title), `.*?\S` skips the lengths that end in a space.
_EPISODE_TITLE = re.compile(
    r"^(?P<series>.*?\S)\s*[-–]\s*s(?P<season>\d{1,3})e(?P<episode>\d{1,4})"
    r"(?:\s*[-–]\s*(?P<name>.*))?$",
    re.I,
)


def split_episode_title(title: Any) -> dict[str, Any] | None:
    """Take the series, the season, the episode and the episode's name out of a composed title."""
    if not title:
        return None
    match = _EPISODE_TITLE.match(str(title).strip())
    if not match:
        return None
    series = match.group("series").strip(" -–")
    if not series:
        return None
    return {
        "series": series,
        "season": int(match.group("season")),
        "episode": int(match.group("episode")),
        "name": (match.group("name") or "").strip(),
    }
