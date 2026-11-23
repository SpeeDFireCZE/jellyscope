"""Music: artists, albums and tracks of Jellyfin's music libraries.

The collector writes music listening into the playback history (`playback`, type "Audio") like any
other playback. This package fetches what the music libraries hold, so that listening has an
artist, an album, a format and a size; the statistics are in `stats/music_listening.py`, the cards in
`cards_music.py`. Watching numbers do not count music (`stats.watching_only`).

The door of the package - whoever writes `from jellyscope import music` gets everything from here:

* `sync` - an incremental run every hour, a full pass once a day (what vanished gets `missing`),
  pairing of imported history afterwards, the state of the last run, `has_music()` (whether the
  Music page has something to show).

Spec: docs/superpowers/specs/2026-11-15-hudba-design.md
"""
from __future__ import annotations

from .sync import (
    ALREADY_RUNNING,
    NAME,
    MusicError,
    has_music,
    sync_running,
    sync_state,
    synchronize,
)

__all__ = [
    "ALREADY_RUNNING",
    "NAME",
    "MusicError",
    "has_music",
    "sync_running",
    "sync_state",
    "synchronize",
]
