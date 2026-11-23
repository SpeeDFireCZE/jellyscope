"""Shared helpers of the music tests.

Jellyfin does not run in the tests: its answers are recorded from a real server
(tests/music_responses/) - `*_12.json` from Jellyfin 12.1 (three albums), `*_10.json` from
Jellyfin 10.11 (the same three albums plus edge cases: a hi-res FLAC with a MusicBrainz id, a
track with two artists in one field, a second disc, m4a, a track without an album and an artist).

`fake_jellyfin(...)` is a class in place of `JellyfinClient`: it does what the music sync asks
(`virtual_folders`, `items_page`), pages like the real server and records who asked for what. It
is substituted through `_package.substitute(music.sync, "JellyfinClient", fake_jellyfin(...))`.

The file starts with an underscore, so the suite does not run it as a test.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent
RESPONSES = PROJECT / "tests" / "music_responses"


def recorded(name: str) -> Any:
    """A recorded answer (`skladby_10`, `knihovny_12`, ...) - a fresh copy every time."""
    return json.loads((RESPONSES / f"{name}.json").read_text(encoding="utf-8"))


def _items(name: str) -> list[dict[str, Any]]:
    return recorded(name)["Items"]


def _by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return copy.deepcopy(next(i for i in items if i.get("Name") == name))


def track(pattern: str = "First Light", version: str = "10", **changes: Any) -> dict[str, Any]:
    """A track after a recorded one, with values of its own (`Id`, `Name`, `AlbumId`, ...)."""
    return {**_by_name(_items(f"skladby_{version}"), pattern), **changes}


def album(pattern: str = "Aurora Sessions", version: str = "10", **changes: Any) -> dict[str, Any]:
    """An album after a recorded one, with values of its own."""
    return {**_by_name(_items(f"alba_{version}"), pattern), **changes}


def artist(pattern: str = "Northern Lights Ensemble", version: str = "10", **changes: Any) -> dict[str, Any]:
    """An artist after a recorded one, with values of its own."""
    return {**_by_name(_items(f"interpreti_{version}"), pattern), **changes}


def fake_jellyfin(libraries: list[dict[str, Any]] | None = None,
                  artists: list[dict[str, Any]] | None = None,
                  albums: list[dict[str, Any]] | None = None,
                  tracks: list[dict[str, Any]] | None = None,
                  version: str = "10",
                  fail_at: tuple[str, int] | None = None,
                  calls: list[tuple[str, int, int]] | None = None) -> type:
    """A class in place of `jellyfin.JellyfinClient` over recorded (or given) items.

    The lists are read again on every request, so a test may change them between syncs (add a
    track, remove one). Items come in the given order - the recordings go from the newest added, as
    the sync asks. `fail_at=("Audio", 10)` = a request for tracks from the tenth item on ends with a
    Jellyfin error. `calls` collects (type, start, limit).
    """
    from jellyscope.jellyfin import JellyfinError

    server_version = "10.11.11" if version == "10" else "12.1.0"
    data = {
        "libraries": libraries if libraries is not None else recorded(f"knihovny_{version}"),
        "MusicArtist": artists if artists is not None else _items(f"interpreti_{version}"),
        "MusicAlbum": albums if albums is not None else _items(f"alba_{version}"),
        "Audio": tracks if tracks is not None else _items(f"skladby_{version}"),
    }

    class Client:
        DATA = data
        verze = server_version

        def __init__(self, base_url: str = "", api_key: str = "", *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def virtual_folders(self) -> list[dict[str, Any]]:
            await asyncio.sleep(0)          # like a real request: another task gets its turn meanwhile
            return copy.deepcopy(self.DATA["libraries"])

        async def items_page(self, start_index: int, limit: int, item_types: str = "Movie,Episode",
                             user_id: str | None = None, parent_id: str | None = None,
                             sort_by: str = "SortName", sort_order: str = "Ascending",
                             fields: str | None = None) -> dict[str, Any]:
            await asyncio.sleep(0)
            if calls is not None:
                calls.append((item_types, start_index, limit))
            if fail_at and item_types == fail_at[0] and start_index >= fail_at[1]:
                raise JellyfinError("Nepodarilo se spojit s Jellyfinem: spojeni bylo preruseno")
            items = self.DATA.get(item_types, [])
            return {"Items": copy.deepcopy(items[start_index:start_index + limit]),
                    "TotalRecordCount": len(items), "StartIndex": start_index}

    return Client


# Listening for page tests: (viewer, track name from the 10.11 recordings, seconds, hours ago, app, method, reason)
LISTENS = (
    ("u-jana", "First Light", 40, 2, "Finamp", "DirectPlay", None),
    ("u-jana", "Polar Drift", 40, 3, "Finamp", "DirectPlay", None),
    ("u-jana", "Dial Tone Lullaby", 10, 4, "Finamp", "DirectPlay", None),
    ("u-petr", "Neon Pier", 40, 26, "Jellyfin Web", "Transcode", "AudioCodecNotSupported"),
    ("u-petr", "Hi-res s MusicBrainz", 6, 30, "Jellyfin Web", "DirectPlay", None),
    ("u-eva", "Low Tide Drive", 40, 24 * 5, "Feishin", "DirectStream", None),
    ("u-eva", "First Light", 40, 24 * 40, "Feishin", "DirectPlay", None),
)
LISTENERS = {"u-jana": "Janička Posluchačová", "u-petr": "Petr Posluchač", "u-eva": "Eva Posluchačka"}


def fill_music(listens: tuple = LISTENS) -> dict[str, str]:
    """Sync the recorded music (10.11) and write listening. Returns {track name: id}.

    The listeners' names match nothing in the music - a test then tells by the bare text that a
    viewer's name got onto a page.
    """
    import asyncio as _asyncio
    from datetime import UTC, datetime, timedelta

    from _package import substitute

    from jellyscope import db, music
    from jellyscope.music import sync

    restore = substitute(sync, "JellyfinClient", fake_jellyfin())
    try:
        _asyncio.run(music.synchronize())
    finally:
        restore()
    tracks = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM music_track")}
    now = datetime.now(UTC)
    with db.connect() as conn:
        for uid, name in LISTENERS.items():
            conn.execute("INSERT INTO users (id, name) VALUES (?, ?) ON CONFLICT (id) DO NOTHING", (uid, name))
        for i, (uid, track_name, seconds, hours, app, method, reason) in enumerate(listens):
            when = (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO playback (session_key, user_id, user_name, item_id, item_name, item_type, client, device_name,"
                " play_method, transcode_reasons, media_runtime_ticks, started_at, last_seen_at, watched_seconds, paused_seconds,"
                " is_paused, is_active) VALUES (?, ?, ?, ?, ?, 'Audio', ?, 'Telefon', ?, ?, 400000000, ?, ?, ?, 0, 0, 0)",
                (f"hudba-test-{i}", uid, LISTENERS[uid], tracks[track_name], track_name, app, method, reason,
                 when, when, seconds))
        conn.commit()
    return tracks
